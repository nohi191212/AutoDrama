from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import time
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError, ProviderError
from autodrama.providers.base import AssetRef, SubjectElementResult, VideoGenerationResult
from autodrama.providers.http import request_id_from_response


class KlingOmniVideoProvider:
    """Kling Omni video provider with subject element support."""

    name = "kling_omni"
    supports_subject_elements = True
    supports_kling_omni_placeholders = True
    reference_video_requires_web_url = True

    _TERMINAL_SUCCESS = {"succeed", "succeeded", "success", "completed", "done"}
    _TERMINAL_FAILURE = {"failed", "fail", "error", "expired", "cancelled", "canceled"}

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = str(
            settings.options.get("kling_base_url")
            or settings.options.get("base_url")
            or settings.base_url
            or "https://api-beijing.klingai.com"
        ).rstrip("/")
        self.model = settings.models.get("video") or settings.models.get("omni_video") or "kling-v3-omni"
        self.subject_element_model = settings.models.get("subject_element") or "advanced-custom-elements"
        self.mode = str(settings.options.get("mode") or settings.options.get("video_mode") or "pro")
        self.aspect_ratio = str(
            settings.options.get("aspect_ratio")
            or settings.options.get("video_aspect_ratio")
            or settings.options.get("ratio")
            or "9:16"
        )
        self.sound = self._sound_value(settings.options.get("sound") or "off")
        self.watermark = bool(settings.options.get("watermark", False))
        self.max_reference_images = int(settings.options.get("max_reference_images", 2))
        self.max_reference_elements = int(settings.options.get("max_reference_elements", 3))
        self.max_reference_videos = int(settings.options.get("max_reference_videos", 0))
        self.max_reference_audio = int(settings.options.get("max_reference_audio", 0))
        self.min_duration_seconds = int(settings.options.get("video_min_duration_seconds", 3))
        self.max_duration_seconds = int(settings.options.get("video_max_duration_seconds", 15))
        self.poll_interval_seconds = float(
            settings.options.get("video_poll_interval_seconds")
            or settings.options.get("poll_interval_seconds")
            or 10
        )
        self.max_polls = int(settings.options.get("video_max_polls") or settings.options.get("max_polls") or 120)
        self.status_log_interval_polls = int(settings.options.get("status_log_interval_polls", 6))

    @property
    def omni_video_path(self) -> str:
        return str(self.settings.options.get("omni_video_endpoint") or "/v1/videos/omni-video")

    @property
    def subject_element_path(self) -> str:
        return str(
            self.settings.options.get("subject_element_endpoint")
            or self.settings.options.get("advanced_custom_elements_endpoint")
            or "/v1/general/advanced-custom-elements"
        )

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _query_url(self, *, option_key: str, default_path: str, task_id: str) -> str:
        configured = self.settings.options.get(option_key)
        if configured:
            path = str(configured)
            if "{task_id}" in path:
                return self._url(path.format(task_id=task_id))
            return self._url(f"{path.rstrip('/')}/{task_id}")
        return self._url(f"{default_path.rstrip('/')}/{task_id}")

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @classmethod
    def _jwt_token(cls, access_key: str, secret_key: str) -> str:
        headers = {"alg": "HS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "iss": access_key,
            "exp": now + 1800,
            "nbf": now - 5,
        }
        header_text = cls._b64url(json.dumps(headers, separators=(",", ":")).encode("utf-8"))
        payload_text = cls._b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_text}.{payload_text}".encode("ascii")
        signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_text}.{payload_text}.{cls._b64url(signature)}"

    def _headers(self) -> dict[str, str]:
        access_key = self.settings.secret("access_key_env")
        secret_key = self.settings.secret("secret_key_env")
        if not access_key or not secret_key:
            raise ProviderAuthError(
                "Missing Kling credentials. Set providers.kling.access_key_env and providers.kling.secret_key_env."
            )
        return {
            "Authorization": f"Bearer {self._jwt_token(access_key, secret_key)}",
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
        refs = refs or []
        payload: dict[str, Any] = {
            "model_name": str(metadata.get("model") or self.model),
            "prompt": str(prompt or "")[:2500],
            "mode": str(metadata.get("mode") or self.mode),
            "aspect_ratio": str(metadata.get("aspect_ratio") or metadata.get("ratio") or self.aspect_ratio),
            "duration": str(self._duration(duration, metadata)),
            "sound": self._sound_value(metadata.get("sound") if "sound" in metadata else self.sound),
            "watermark_info": {"enabled": bool(metadata.get("watermark", self.watermark))},
        }

        image_list = self._image_list(refs)
        if image_list:
            payload["image_list"] = image_list

        element_list = self._element_list(refs)
        if element_list:
            payload["element_list"] = element_list

        video_list = self._video_list(refs)
        if video_list:
            payload["video_list"] = video_list
            payload["sound"] = "off"

        for key in ("callback_url", "external_task_id", "multi_shot", "shot_type", "multi_prompt"):
            if key in self.settings.options:
                payload[key] = self.settings.options[key]
            if key in metadata:
                payload[key] = metadata[key]

        parameters = metadata.get("parameters")
        if isinstance(parameters, dict):
            payload.update(parameters)
        return payload

    def _duration(self, duration: float | None, metadata: dict[str, Any]) -> int:
        configured = metadata.get("duration") or metadata.get("duration_seconds") or self.settings.options.get("duration")
        value = duration if duration is not None else configured
        if value is None:
            value = self.settings.options.get("video_duration_seconds") or 5
        resolved = round(float(value))
        return max(self.min_duration_seconds, min(self.max_duration_seconds, resolved))

    @staticmethod
    def _sound_value(value: object) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        text = str(value or "off").strip().lower()
        if text in {"1", "true", "yes"}:
            return "on"
        if text in {"0", "false", "no"}:
            return "off"
        return text if text in {"on", "off"} else "off"

    def _image_list(self, refs: list[AssetRef]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ref in refs:
            if ref.type != "image" or len(items) >= self.max_reference_images:
                continue
            image_value = self._image_ref_value(ref)
            if not image_value:
                continue
            item: dict[str, Any] = {"image_url": image_value}
            frame_type = str(ref.metadata.get("kling_type") or ref.metadata.get("type") or "").strip()
            if frame_type in {"first_frame", "end_frame"}:
                item["type"] = frame_type
            items.append(item)
        return items

    def _element_list(self, refs: list[AssetRef]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ref in refs:
            if ref.type != "element" or len(items) >= self.max_reference_elements:
                continue
            element_id = self._element_id(ref)
            if element_id is None:
                continue
            items.append({"element_id": element_id})
        return items

    def _video_list(self, refs: list[AssetRef]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ref in refs:
            if ref.type != "video" or len(items) >= self.max_reference_videos:
                continue
            video_url = self._web_ref_value(ref)
            if not video_url:
                continue
            refer_type = str(ref.metadata.get("refer_type") or "feature")
            item = {"video_url": video_url, "refer_type": refer_type}
            keep_original_sound = ref.metadata.get("keep_original_sound")
            if keep_original_sound in {"yes", "no"}:
                item["keep_original_sound"] = keep_original_sound
            items.append(item)
        return items

    @staticmethod
    def _element_id(ref: AssetRef) -> int | str | None:
        value = ref.metadata.get("element_id") or ref.metadata.get("subject_element_id") or ref.id
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        return text

    @staticmethod
    def _web_ref_value(ref: AssetRef) -> str | None:
        for value in (ref.url, ref.path):
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    @classmethod
    def _image_ref_value(cls, ref: AssetRef) -> str | None:
        if ref.url:
            return ref.url
        if isinstance(ref.path, str) and ref.path.startswith(("http://", "https://", "data:")):
            return ref.path
        if not ref.path:
            return None
        path = Path(ref.path)
        if not path.exists() or not path.is_file():
            return None
        return base64.b64encode(path.read_bytes()).decode("ascii")

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
            response = await client.post(self._url(self.omni_video_path), headers=self._headers(), json=payload)
        body = self._json_response(response, label="Kling omni video submit")
        return self._video_result(body, request_id=self._request_id(body, response), fallback_model=payload["model_name"])

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.get(
                self._query_url(option_key="omni_video_query_endpoint", default_path=self.omni_video_path, task_id=task_id),
                headers=self._headers(),
            )
        body = self._json_response(response, label="Kling omni video query")
        return self._video_result(
            body,
            request_id=self._request_id(body, response),
            fallback_task_id=task_id,
            fallback_model=self.model,
        )

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        result = await self.submit_video(prompt, refs=refs, duration=duration, metadata=metadata)
        if not wait:
            return result
        if not result.task_id:
            raise ProviderBadResponseError("Kling submit result has no task_id")

        task_id = result.task_id
        for _poll_index in range(1, self.max_polls + 1):
            await asyncio.sleep(self.poll_interval_seconds)
            result = await self.query_video_task(task_id)
            status = str(result.task_status or "").strip().lower()
            if status in self._TERMINAL_SUCCESS:
                return result
            if status in self._TERMINAL_FAILURE:
                raise ProviderError(f"Kling video task {task_id} ended with status {result.task_status}")
        raise ProviderError(f"Kling video task {task_id} did not finish after {self.max_polls} polls")

    async def create_subject_element(
        self,
        *,
        element_name: str,
        element_description: str,
        reference_type: str,
        video_url: str | None = None,
        image_refs: list[AssetRef] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubjectElementResult:
        metadata = metadata or {}
        payload = self.build_subject_element_payload(
            element_name=element_name,
            element_description=element_description,
            reference_type=reference_type,
            video_url=video_url,
            image_refs=image_refs,
            metadata=metadata,
        )
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(self._url(self.subject_element_path), headers=self._headers(), json=payload)
        body = self._json_response(response, label="Kling subject element submit")
        return self._subject_result(body, request_id=self._request_id(body, response), fallback_model=self.subject_element_model)

    def build_subject_element_payload(
        self,
        *,
        element_name: str,
        element_description: str,
        reference_type: str,
        video_url: str | None = None,
        image_refs: list[AssetRef] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        reference_type = str(reference_type or "video_refer").strip()
        payload: dict[str, Any] = {
            "element_name": str(element_name or "")[:20],
            "element_description": str(element_description or "")[:100],
            "reference_type": reference_type,
        }
        if reference_type == "video_refer":
            if not video_url:
                raise ValueError("Kling video_refer subject creation requires video_url")
            payload["element_video_list"] = {"refer_videos": [{"video_url": video_url}]}
        elif reference_type == "image_refer":
            images = [self._image_ref_value(ref) for ref in image_refs or []]
            images = [image for image in images if image]
            if not images:
                raise ValueError("Kling image_refer subject creation requires at least one reference image")
            payload["element_image_list"] = {
                "frontal_image": images[0],
                "refer_images": [{"image_url": image} for image in images[1:4]],
            }
        else:
            raise ValueError(f"Unsupported Kling subject reference_type: {reference_type}")

        if element_voice_id := metadata.get("element_voice_id"):
            payload["element_voice_id"] = element_voice_id
        tag_list = metadata.get("tag_list") or self.settings.options.get("subject_element_tag_list")
        if tag_list is None:
            tag_list = [{"tag_id": "o_102"}]
        if tag_list:
            payload["tag_list"] = tag_list
        for key in ("callback_url", "external_task_id"):
            if key in self.settings.options:
                payload[key] = self.settings.options[key]
            if key in metadata:
                payload[key] = metadata[key]
        return payload

    async def query_subject_element_task(self, task_id: str) -> SubjectElementResult:
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.get(
                self._query_url(
                    option_key="subject_element_query_endpoint",
                    default_path=self.subject_element_path,
                    task_id=task_id,
                ),
                headers=self._headers(),
            )
        body = self._json_response(response, label="Kling subject element query")
        return self._subject_result(
            body,
            request_id=self._request_id(body, response),
            fallback_task_id=task_id,
            fallback_model=self.subject_element_model,
        )

    async def generate_subject_element(
        self,
        *,
        element_name: str,
        element_description: str,
        reference_type: str,
        video_url: str | None = None,
        image_refs: list[AssetRef] | None = None,
        wait: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> SubjectElementResult:
        result = await self.create_subject_element(
            element_name=element_name,
            element_description=element_description,
            reference_type=reference_type,
            video_url=video_url,
            image_refs=image_refs,
            metadata=metadata,
        )
        if not wait:
            return result
        if not result.task_id:
            raise ProviderBadResponseError("Kling subject element submit result has no task_id")
        task_id = result.task_id
        for _poll_index in range(1, self.max_polls + 1):
            await asyncio.sleep(self.poll_interval_seconds)
            result = await self.query_subject_element_task(task_id)
            status = str(result.task_status or "").strip().lower()
            if status in self._TERMINAL_SUCCESS:
                return result
            if status in self._TERMINAL_FAILURE:
                raise ProviderError(f"Kling subject element task {task_id} ended with status {result.task_status}")
        raise ProviderError(f"Kling subject element task {task_id} did not finish after {self.max_polls} polls")

    @staticmethod
    def _request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        return request_id_from_response(body, response, "x-request-id", "x-kling-request-id")

    @staticmethod
    def _json_response(response: httpx.Response, *, label: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise ProviderBadResponseError(f"{label} failed with HTTP {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"{label} returned non-JSON response: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderBadResponseError(f"{label} returned non-object JSON: {body!r}")
        code = body.get("code")
        if code not in (None, 0, "0"):
            message = body.get("message") or body.get("msg") or body
            raise ProviderBadResponseError(f"{label} business error {code}: {message}")
        return body

    @classmethod
    def _video_result(
        cls,
        body: dict[str, Any],
        *,
        request_id: str | None = None,
        fallback_task_id: str | None = None,
        fallback_model: str | None = None,
    ) -> VideoGenerationResult:
        return VideoGenerationResult(
            provider=cls.name,
            model=str(cls._first(body, "model_name", "model") or fallback_model or ""),
            task_id=cls._task_id(body) or fallback_task_id,
            task_status=cls._status(body),
            video_url=cls._first_url(body),
            last_frame_url=cls._first(body, "last_frame_url", "lastFrameUrl"),
            request_id=request_id or cls._first(body, "request_id", "requestId"),
            usage=cls._usage(body),
            raw_response=body,
        )

    @classmethod
    def _subject_result(
        cls,
        body: dict[str, Any],
        *,
        request_id: str | None = None,
        fallback_task_id: str | None = None,
        fallback_model: str | None = None,
    ) -> SubjectElementResult:
        element_id = cls._first(body, "element_id", "elementId")
        return SubjectElementResult(
            provider=cls.name,
            model=str(cls._first(body, "model_name", "model") or fallback_model or "advanced-custom-elements"),
            task_id=cls._task_id(body) or fallback_task_id,
            task_status=cls._status(body),
            element_id=str(element_id) if element_id is not None else None,
            request_id=request_id or cls._first(body, "request_id", "requestId"),
            usage=cls._usage(body),
            raw_response=body,
        )

    @classmethod
    def _task_id(cls, body: dict[str, Any]) -> str | None:
        value = cls._first(body, "task_id", "taskId", "id")
        return str(value) if value is not None else None

    @classmethod
    def _status(cls, body: dict[str, Any]) -> str | None:
        value = cls._first(body, "task_status", "taskStatus", "status")
        return str(value) if value is not None else None

    @classmethod
    def _usage(cls, body: dict[str, Any]) -> dict[str, Any]:
        value = cls._first(body, "usage")
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _first_url(cls, value: object) -> str | None:
        if isinstance(value, dict):
            for key in ("video_url", "videoUrl"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
                if isinstance(candidate, dict):
                    nested = candidate.get("url")
                    if isinstance(nested, str) and nested:
                        return nested
            if str(value.get("type") or "").strip().lower() == "video_url":
                candidate = value.get("url")
                if isinstance(candidate, str) and candidate:
                    return candidate
            for key in ("output", "data", "result", "task_result", "taskResult", "content", "videos", "works"):
                found = cls._first_url(value.get(key))
                if found:
                    return found
            for item in value.values():
                found = cls._first_url(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._first_url(item)
                if found:
                    return found
        return None

    @classmethod
    def _first(cls, value: object, *keys: str) -> Any:
        if not isinstance(value, dict):
            return None
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
        for container_key in ("data", "output", "result", "task_result", "taskResult", "task_info", "taskInfo"):
            nested = value.get(container_key)
            if isinstance(nested, dict):
                nested_value = cls._first(nested, *keys)
                if nested_value is not None:
                    return nested_value
            elif isinstance(nested, list):
                for item in nested:
                    nested_value = cls._first(item, *keys)
                    if nested_value is not None:
                        return nested_value
        return None
