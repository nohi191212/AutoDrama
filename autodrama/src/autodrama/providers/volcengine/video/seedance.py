from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError, ProviderError
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef, VideoGenerationResult
from autodrama.providers.http import request_id_from_response
from autodrama.providers.media_refs import asset_uri, file_to_data_url
from autodrama.services.audio_duration import probe_audio_duration_seconds


class VolcengineSeedanceVideoProvider:
    """Volcengine Ark Seedance video provider."""

    name = "volcengine_seedance"
    reference_video_requires_web_url = True

    _TERMINAL_SUCCESS = {"succeeded", "success", "completed", "done"}
    _TERMINAL_FAILURE = {"failed", "fail", "error", "expired", "cancelled", "canceled"}
    _IMAGE_ROLES = {"reference_image", "first_frame", "last_frame"}
    _FRAME_IMAGE_ROLES = {"first_frame", "last_frame"}

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = str(
            settings.options.get("seedance_base_url")
            or settings.options.get("ark_base_url")
            or "https://ark.cn-beijing.volces.com"
        ).rstrip("/")
        self.model = (
            settings.models.get("seedance_2")
            or settings.models.get("seedance")
            or settings.models.get("video")
            or "doubao-seedance-2-0-260128"
        )
        self.api_key = self._api_key(settings)
        self.ratio = str(settings.options.get("video_ratio") or settings.options.get("ratio") or "16:9")
        self.resolution = str(settings.options.get("video_resolution") or settings.options.get("resolution") or "720p")
        self.generate_audio = bool(settings.options.get("generate_audio", False))
        self.watermark = bool(settings.options.get("watermark", False))
        self.poll_interval_seconds = float(settings.options.get("video_poll_interval_seconds", settings.options.get("poll_interval_seconds", 5)))
        self.max_polls = int(settings.options.get("video_max_polls", settings.options.get("max_polls", 120)))
        self.min_duration_seconds = int(settings.options.get("video_min_duration_seconds", 4))
        self.max_duration_seconds = int(settings.options.get("video_max_duration_seconds", 10))
        self.max_reference_images = int(settings.options.get("max_reference_images", 2))
        self.max_reference_audio = int(settings.options.get("max_reference_audio", 1))
        self.max_reference_videos = int(settings.options.get("max_reference_videos", 2))
        self.max_reference_audio_duration_seconds = float(
            settings.options.get("max_reference_audio_duration_seconds", 15.2)
        )
        self.max_reference_video_total_duration_seconds = float(
            settings.options.get("max_reference_video_total_duration_seconds", 15.2)
        )

    @staticmethod
    def _api_key(settings: ProviderSettings) -> str | None:
        api_key_ref = settings.options.get("seedance_api_key_env") or settings.options.get("ark_api_key_env")
        if api_key_ref:
            copied_settings = settings.model_copy(update={"api_key_env": str(api_key_ref)})
            return copied_settings.secret("api_key_env")
        return settings.secret("api_key_env")

    @property
    def tasks_endpoint(self) -> str:
        if self.base_url.endswith("/api/v3/contents/generations/tasks"):
            return self.base_url
        if self.base_url.endswith("/api/v3"):
            return f"{self.base_url}/contents/generations/tasks"
        return f"{self.base_url}/api/v3/contents/generations/tasks"

    def task_endpoint(self, task_id: str) -> str:
        return f"{self.tasks_endpoint}/{task_id}"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderAuthError(
                "Missing Volcengine Ark API key. Set providers.volcengine.options.seedance_api_key_env "
                "or providers.volcengine.api_key_env."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def build_payload(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        payload: dict[str, Any] = {
            "model": str(metadata.get("model") or self.model),
            "content": self._content(prompt, refs or []),
            "generate_audio": bool(metadata.get("generate_audio", self.generate_audio)),
            "ratio": str(metadata.get("ratio") or metadata.get("video_ratio") or self.ratio),
            "resolution": str(metadata.get("resolution") or self.resolution),
            "watermark": bool(metadata.get("watermark", self.watermark)),
        }

        resolved_duration = self._duration(duration, metadata)
        if resolved_duration is not None:
            payload["duration"] = resolved_duration

        for key in (
            "seed",
            "return_last_frame",
            "service_tier",
            "execution_expires_after",
            "tools",
            "safety_identifier",
            "callback_url",
        ):
            if key in self.settings.options:
                payload[key] = self.settings.options[key]
            if key in metadata:
                payload[key] = metadata[key]

        extra_parameters = metadata.get("parameters")
        if isinstance(extra_parameters, dict):
            payload.update(extra_parameters)
        return payload

    def _duration(self, duration: float | None, metadata: dict[str, Any]) -> int | None:
        configured = metadata.get("duration", self.settings.options.get("duration"))
        value = duration if duration is not None else configured
        if value is None:
            return None
        if int(value) == -1:
            return -1
        duration_seconds = round(float(value))
        return max(self.min_duration_seconds, min(self.max_duration_seconds, duration_seconds))

    def _content(self, prompt: str, refs: list[AssetRef]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt[:5000]}]
        image_refs: list[tuple[str, str]] = []
        other_refs: list[AssetRef] = []

        for ref in refs:
            if ref.type != "image":
                other_refs.append(ref)
                continue
            url = self._ref_url_or_data(ref, expected_type="image")
            if not url:
                continue
            image_refs.append((url, self._image_role(ref)))

        if any(role in self._FRAME_IMAGE_ROLES for _, role in image_refs):
            frame_counts = {"first_frame": 0, "last_frame": 0}
            for url, role in image_refs:
                if role not in self._FRAME_IMAGE_ROLES:
                    continue
                if frame_counts[role] >= 1:
                    continue
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
                frame_counts[role] += 1
            return content

        image_count = 0
        audio_count = 0
        video_count = 0

        for url, _role in image_refs:
            if image_count < self.max_reference_images:
                content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
                image_count += 1

        for ref in other_refs:
            if ref.type == "audio" and audio_count < self.max_reference_audio:
                if self._audio_ref_exceeds_duration_limit(ref):
                    continue
                url = self._ref_url_or_data(ref, expected_type="audio")
                if not url:
                    continue
                content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
                audio_count += 1
                continue

            if ref.type == "video" and video_count < self.max_reference_videos:
                url = self._ref_url_or_data(ref, expected_type="video")
                if not url:
                    continue
                content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})
                video_count += 1

        return content

    def _image_role(self, ref: AssetRef) -> str:
        role = ref.metadata.get("seedance_role") or ref.metadata.get("role") or "reference_image"
        role_text = str(role).strip().lower()
        return role_text if role_text in self._IMAGE_ROLES else "reference_image"

    def _audio_ref_exceeds_duration_limit(self, ref: AssetRef) -> bool:
        if self.max_reference_audio_duration_seconds <= 0:
            return False

        duration = self._audio_ref_duration_seconds(ref)
        if duration is None or duration <= self.max_reference_audio_duration_seconds:
            return False

        get_logger().warning(
            "Seedance skipped audio reference %s because duration %.3fs exceeds max %.3fs",
            ref.id or ref.path or ref.url or "-",
            duration,
            self.max_reference_audio_duration_seconds,
        )
        return True

    def _audio_ref_duration_seconds(self, ref: AssetRef) -> float | None:
        for key in ("duration_seconds", "duration"):
            value = ref.metadata.get(key)
            if value is None:
                continue
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                return duration

        if not ref.path:
            return None
        path = Path(ref.path)
        if not path.exists() or not path.is_file():
            return None
        return probe_audio_duration_seconds(path, ffmpeg_path=self.runtime.ffmpeg_path)

    def _ref_url_or_data(self, ref: AssetRef, *, expected_type: str) -> str | None:
        if expected_type == "video":
            for value in (ref.url, ref.path):
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            return None
        if ref.url:
            return ref.url
        if asset_uri := self._asset_uri(ref):
            return asset_uri
        if not ref.path:
            return None
        return self._data_url(Path(ref.path), expected_type=expected_type)

    @staticmethod
    def _asset_uri(ref: AssetRef) -> str | None:
        return asset_uri(ref)

    @staticmethod
    def _data_url(path: Path, *, expected_type: str) -> str | None:
        default_mime = {"image": "image/png", "audio": "audio/mpeg", "video": "video/mp4"}.get(expected_type)
        return file_to_data_url(path, expected_type=expected_type, default_mime=default_mime)

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        payload = self.build_payload(prompt, refs, duration=duration, metadata=metadata)
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(self.tasks_endpoint, headers=self._headers(), json=payload)

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Seedance video submit failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Seedance video submit returned non-JSON response: {exc}") from exc

        task_id = self._task_id(body)
        if not task_id:
            raise ProviderBadResponseError(f"Seedance submit response missing task id: {body}")
        return self._result(body, request_id=self._request_id(body, response), fallback_task_id=task_id)

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.get(self.task_endpoint(task_id), headers=self._headers())

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Seedance task query failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Seedance task query returned non-JSON response: {exc}") from exc
        return self._result(body, request_id=self._request_id(body, response), fallback_task_id=task_id)

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        metadata = metadata or {}
        logger = get_logger()
        asset_id = str(metadata.get("asset_id") or "-")
        result = await self.submit_video(prompt, refs, duration=duration, metadata=metadata)
        logger.info(
            "Seedance video task submitted asset=%s task_id=%s status=%s wait=%s",
            asset_id,
            result.task_id or "-",
            result.task_status or "-",
            wait,
        )
        if not wait:
            return result
        if not result.task_id:
            raise ProviderBadResponseError("Seedance submit result has no task_id")

        task_id = result.task_id
        last_status = (result.task_status or "").lower()
        for poll_index in range(1, self.max_polls + 1):
            await asyncio.sleep(self.poll_interval_seconds)
            result = await self.query_video_task(task_id)
            status = (result.task_status or "").lower()
            if status != last_status or poll_index == 1 or poll_index % 5 == 0:
                logger.info(
                    "Seedance video task polling asset=%s task_id=%s status=%s poll=%d/%d",
                    asset_id,
                    task_id,
                    result.task_status or "-",
                    poll_index,
                    self.max_polls,
                )
                last_status = status
            if status in self._TERMINAL_SUCCESS:
                logger.info(
                    "Seedance video task completed asset=%s task_id=%s status=%s",
                    asset_id,
                    task_id,
                    result.task_status or "-",
                )
                return result
            if status in self._TERMINAL_FAILURE:
                raise ProviderError(f"Seedance task {task_id} ended with status {result.task_status}")

        raise ProviderError(f"Seedance task {task_id} did not finish after {self.max_polls} polls")

    @classmethod
    def _result(
        cls,
        body: dict[str, Any],
        *,
        request_id: str | None = None,
        fallback_task_id: str | None = None,
    ) -> VideoGenerationResult:
        return VideoGenerationResult(
            provider=cls.name,
            model=str(cls._first(body, "model") or ""),
            task_id=cls._task_id(body) or fallback_task_id,
            task_status=cls._status(body),
            video_url=cls._video_url(body),
            last_frame_url=cls._last_frame_url(body),
            request_id=request_id,
            usage=cls._usage(body),
            raw_response=body,
        )

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        return request_id_from_response(body, response, "x-request-id", "x-tt-logid")

    @classmethod
    def _task_id(cls, body: dict[str, Any]) -> str | None:
        return cls._first(body, "id", "task_id", "taskId")

    @classmethod
    def _status(cls, body: dict[str, Any]) -> str | None:
        value = cls._first(body, "status", "task_status", "taskStatus")
        return str(value) if value is not None else None

    @classmethod
    def _video_url(cls, body: dict[str, Any]) -> str | None:
        value = cls._first(body, "video_url", "videoUrl", "url")
        if isinstance(value, str):
            return value

        content = body.get("content")
        if isinstance(content, dict):
            value = cls._first(content, "video_url", "videoUrl", "url")
            if isinstance(value, str):
                return value
        if isinstance(content, list):
            for item in content:
                value = cls._first(item, "video_url", "videoUrl", "url")
                if isinstance(value, str):
                    return value
                if isinstance(item, dict) and isinstance(item.get("video_url"), dict):
                    nested = item["video_url"].get("url")
                    if isinstance(nested, str):
                        return nested

        return None

    @classmethod
    def _last_frame_url(cls, body: dict[str, Any]) -> str | None:
        value = cls._first(body, "last_frame_url", "lastFrameUrl")
        if isinstance(value, str) and value:
            return value
        content = body.get("content")
        if isinstance(content, dict):
            value = cls._first(content, "last_frame_url", "lastFrameUrl")
            if isinstance(value, str) and value:
                return value
        return None

    @classmethod
    def _usage(cls, body: dict[str, Any]) -> dict[str, Any]:
        usage = cls._first(body, "usage")
        return dict(usage) if isinstance(usage, dict) else {}

    @classmethod
    def _first(cls, value: object, *keys: str) -> object:
        if not isinstance(value, dict):
            return None
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
        for container_key in ("output", "data", "result"):
            nested = value.get(container_key)
            if isinstance(nested, dict):
                nested_value = cls._first(nested, *keys)
                if nested_value is not None:
                    return nested_value
        return None
