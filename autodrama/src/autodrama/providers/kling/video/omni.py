from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError, ProviderError
from autodrama.providers.base import (
    AssetRef,
    SubjectElementResult,
    VideoGenerationResult,
    VoiceAssetResult,
    VoiceSynthesisResult,
)
from autodrama.providers.http import request_id_from_response


class KlingOmniVideoProvider:
    """Kling Omni video provider with subject element support."""

    name = "kling_omni"
    supports_subject_elements = True
    supports_custom_voices = True
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
        self.voice_model = settings.models.get("voice") or "custom-voices"
        self.tts_model = settings.models.get("tts") or "kling-avatar-tts"
        self.api_schema = str(settings.options.get("api_schema") or "legacy").strip().lower()
        self.mode = str(settings.options.get("mode") or settings.options.get("video_mode") or "pro")
        self.aspect_ratio = str(
            settings.options.get("aspect_ratio")
            or settings.options.get("video_aspect_ratio")
            or settings.options.get("ratio")
            or "9:16"
        )
        self.sound = self._sound_value(settings.options.get("sound") or "off")
        self.resolution = str(settings.options.get("resolution") or "1080p")
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
        self.httpx_trust_env = bool(settings.options.get("httpx_trust_env", True))

    @property
    def omni_video_path(self) -> str:
        default = "/omni-video/kling-3.0-omni" if self.api_schema == "official_v3" else "/v1/videos/omni-video"
        return str(self.settings.options.get("omni_video_endpoint") or default)

    @property
    def task_query_path(self) -> str:
        return str(self.settings.options.get("omni_video_query_endpoint") or "/tasks")

    @property
    def subject_element_path(self) -> str:
        return str(
            self.settings.options.get("subject_element_endpoint")
            or self.settings.options.get("advanced_custom_elements_endpoint")
            or "/v1/general/advanced-custom-elements"
        )

    @property
    def custom_voice_path(self) -> str:
        return str(self.settings.options.get("custom_voice_endpoint") or "/v1/general/custom-voices")

    @property
    def preset_voices_path(self) -> str:
        return str(self.settings.options.get("preset_voices_endpoint") or "/v1/general/presets-voices")

    @property
    def tts_path(self) -> str:
        return str(self.settings.options.get("tts_endpoint") or "/v1/audio/tts")

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

    def _headers(self) -> dict[str, str]:
        api_key = self.settings.secret("api_key_env")
        if not api_key:
            raise ProviderAuthError(
                "Missing Kling API key. Set providers.kling.api_key_env to KLING_API_KEY "
                "and add KLING_API_KEY to apikeys.yaml."
            )
        return {
            "Authorization": f"Bearer {api_key}",
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
        if self.api_schema == "official_v3":
            return self._build_official_v3_payload(prompt, refs, duration=duration, metadata=metadata)
        return self._build_legacy_payload(prompt, refs, duration=duration, metadata=metadata)

    def _build_legacy_payload(
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
            "sound": self._legacy_sound_value(
                metadata.get("audio", metadata.get("sound", self.sound))
            ),
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

    def _build_official_v3_payload(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        refs = refs or []
        contents: list[dict[str, Any]] = []
        used_ids: set[str] = set()

        for index, ref in enumerate(refs, start=1):
            if ref.type == "image":
                value = self._image_ref_value(ref)
                if not value:
                    continue
                content_id = self._content_id(ref, fallback=f"image_{index}", used=used_ids)
                asset_type = str(ref.metadata.get("asset_type") or "")
                raw_type = str(ref.metadata.get("kling_type") or ref.metadata.get("type") or "")
                type_map = {
                    "clip_start_frame": "first_frame",
                    "clip_end_frame": "last_frame",
                    "end_frame": "last_frame",
                }
                content_type = type_map.get(asset_type, type_map.get(raw_type, raw_type))
                if content_type not in {"first_frame", "last_frame", "refer_image"}:
                    content_type = "refer_image"
                contents.append({"type": content_type, "url": value, "id": content_id})
            elif ref.type == "element":
                element_id = self._element_id(ref)
                if element_id is None:
                    continue
                content_id = self._content_id(ref, fallback=f"element_{index}", used=used_ids)
                contents.append({"type": "element", "element_id": str(element_id), "id": content_id})
            elif ref.type == "video":
                value = self._web_ref_value(ref)
                if not value:
                    continue
                content_id = self._content_id(ref, fallback=f"video_{index}", used=used_ids)
                refer_type = str(ref.metadata.get("refer_type") or "feature").strip().lower()
                content_type = "base_video" if refer_type in {"base", "edit"} else "feature_video"
                contents.append({"type": content_type, "url": value, "id": content_id})

        normalized_prompt = str(prompt or "")[:3072]
        for item in contents:
            content_id = str(item.get("id") or "")
            if content_id:
                normalized_prompt = normalized_prompt.replace(f"<<<{content_id}>>>", f"@{content_id}")
        contents.insert(0, {"type": "prompt", "text": normalized_prompt})

        audio = self._official_audio_value(metadata.get("audio", metadata.get("sound", self.sound)))
        if any(item["type"] == "feature_video" for item in contents):
            audio = "off"
        if any(item["type"] == "base_video" for item in contents) and audio == "native":
            audio = "original"
        settings: dict[str, Any] = {
            "multi_shot": bool(metadata.get("multi_shot", self.settings.options.get("multi_shot", False))),
            "audio": audio,
            "resolution": str(metadata.get("resolution") or self.resolution),
            "aspect_ratio": str(metadata.get("aspect_ratio") or metadata.get("ratio") or self.aspect_ratio),
            "duration": self._duration(duration, metadata),
        }
        options: dict[str, Any] = {
            "watermark_info": {"enabled": bool(metadata.get("watermark", self.watermark))},
        }
        for key in ("callback_url", "external_task_id"):
            value = metadata.get(key, self.settings.options.get(key))
            if value not in (None, ""):
                options[key] = value
        return {"contents": contents, "settings": settings, "options": options}

    @staticmethod
    def _content_id(ref: AssetRef, *, fallback: str, used: set[str]) -> str:
        base = str(ref.metadata.get("kling_content_id") or ref.metadata.get("slot") or ref.id or fallback).strip()
        base = "".join(char if char.isalnum() or char == "_" else "_" for char in base).strip("_") or fallback
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate

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
        return text if text in {"on", "off", "native", "original"} else "off"

    @classmethod
    def _legacy_sound_value(cls, value: object) -> str:
        return "on" if cls._sound_value(value) in {"on", "native"} else "off"

    @classmethod
    def _official_audio_value(cls, value: object) -> str:
        normalized = cls._sound_value(value)
        if normalized == "on":
            return "native"
        return normalized if normalized in {"native", "original", "off"} else "off"

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
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.post(self._url(self.omni_video_path), headers=self._headers(), json=payload)
        body = self._json_response(response, label="Kling omni video submit")
        return self._video_result(body, request_id=self._request_id(body, response), fallback_model=self.model)

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        if self.api_schema == "official_v3":
            query_url = self._url(self.task_query_path)
            params = {"task_ids": task_id}
        else:
            query_url = self._query_url(
                option_key="omni_video_query_endpoint",
                default_path=self.omni_video_path,
                task_id=task_id,
            )
            params = None
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.get(
                query_url,
                headers=self._headers(),
                params=params,
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

    def build_custom_voice_payload(
        self,
        *,
        voice_name: str,
        voice_url: str | None = None,
        video_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        if not voice_url and not video_id:
            raise ValueError("Kling custom voice creation requires voice_url or video_id")
        if voice_url and video_id:
            raise ValueError("Kling custom voice creation accepts only one of voice_url or video_id")
        payload: dict[str, Any] = {"voice_name": str(voice_name or "")[:20]}
        if voice_url:
            payload["voice_url"] = str(voice_url)
        if video_id:
            payload["video_id"] = str(video_id)
        for key in ("callback_url", "external_task_id"):
            value = metadata.get(key, self.settings.options.get(key))
            if value not in (None, ""):
                payload[key] = value
        return payload

    async def create_custom_voice(
        self,
        *,
        voice_name: str,
        voice_url: str | None = None,
        video_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceAssetResult:
        payload = self.build_custom_voice_payload(
            voice_name=voice_name,
            voice_url=voice_url,
            video_id=video_id,
            metadata=metadata,
        )
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.post(self._url(self.custom_voice_path), headers=self._headers(), json=payload)
        body = self._json_response(response, label="Kling custom voice submit")
        return self._voice_result(body, request_id=self._request_id(body, response), fallback_model=self.voice_model)

    async def query_custom_voice(self, task_id: str) -> VoiceAssetResult:
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.get(
                self._url(f"{self.custom_voice_path.rstrip('/')}/{task_id}"),
                headers=self._headers(),
            )
        body = self._json_response(response, label="Kling custom voice query")
        return self._voice_result(
            body,
            request_id=self._request_id(body, response),
            fallback_task_id=task_id,
            fallback_model=self.voice_model,
        )

    async def generate_custom_voice(
        self,
        *,
        voice_name: str,
        voice_url: str | None = None,
        video_id: str | None = None,
        wait: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceAssetResult:
        result = await self.create_custom_voice(
            voice_name=voice_name,
            voice_url=voice_url,
            video_id=video_id,
            metadata=metadata,
        )
        if not wait:
            return result
        if not result.task_id:
            raise ProviderBadResponseError("Kling custom voice submit result has no task_id")
        for _poll_index in range(1, self.max_polls + 1):
            await asyncio.sleep(self.poll_interval_seconds)
            result = await self.query_custom_voice(result.task_id)
            status = str(result.task_status or "").strip().lower()
            if status in self._TERMINAL_SUCCESS:
                if not result.voice_id:
                    raise ProviderBadResponseError(
                        f"Kling custom voice task {result.task_id} succeeded without voice_id"
                    )
                return result
            if status in self._TERMINAL_FAILURE:
                raise ProviderError(f"Kling custom voice task {result.task_id} ended with status {result.task_status}")
        raise ProviderError(f"Kling custom voice task {result.task_id} did not finish after {self.max_polls} polls")

    async def list_preset_voices(
        self,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the official Kling preset voices in voice-catalog speaker shape."""

        resolved_page_size = max(
            1,
            min(500, int(page_size or self.settings.options.get("preset_voice_page_size") or 500)),
        )
        resolved_max_pages = max(
            1,
            min(1000, int(max_pages or self.settings.options.get("preset_voice_max_pages") or 1000)),
        )
        voices_by_id: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            for page_num in range(1, resolved_max_pages + 1):
                response = await client.get(
                    self._url(self.preset_voices_path),
                    headers=self._headers(),
                    params={"pageNum": page_num, "pageSize": resolved_page_size},
                )
                body = self._json_response(response, label="Kling preset voices list")
                page_voices = self._voice_records(body)
                new_count = 0
                for voice in page_voices:
                    voice_id = str(voice.get("voice_id") or voice.get("voiceId") or voice.get("id") or "").strip()
                    if not voice_id:
                        continue
                    if voice_id not in voices_by_id:
                        new_count += 1
                    voice_name = str(
                        voice.get("voice_name")
                        or voice.get("voiceName")
                        or voice.get("name")
                        or voice_id
                    )
                    voices_by_id[voice_id] = {
                        "voice_type": voice_id,
                        "name": voice_name,
                        "resource_id": voice_id,
                        "model_family": "kling-3.0-omni",
                        "trial_url": str(
                            voice.get("trial_url")
                            or voice.get("trialUrl")
                            or voice.get("url")
                            or ""
                        ) or None,
                        "owned_by": str(voice.get("owned_by") or voice.get("ownedBy") or "kling"),
                        "language": str(voice.get("language") or "zh"),
                        "source": "official_preset",
                        "auto_selectable": "视频原声" not in voice_name,
                    }

                data = body.get("data")
                page_record_count = len(data) if isinstance(data, list) else len(page_voices)
                if not page_voices or not new_count or page_record_count < resolved_page_size:
                    break
        return list(voices_by_id.values())

    def build_tts_payload(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        payload = {
            "text": str(text or "")[:1000],
            "voice_id": str(voice or "").strip(),
            "voice_language": str(metadata.get("voice_language") or "zh"),
            "voice_speed": round(float(metadata.get("voice_speed") or 1.0), 1),
        }
        if not payload["text"]:
            raise ValueError("Kling TTS requires non-empty text")
        if not payload["voice_id"]:
            raise ValueError("Kling TTS requires voice_id")
        payload["voice_speed"] = max(0.8, min(2.0, payload["voice_speed"]))
        return payload

    async def _query_tts_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.get(
                self._url(self.task_query_path),
                headers=self._headers(),
                params={"task_ids": task_id},
            )
        return self._json_response(response, label="Kling TTS query")

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        payload = self.build_tts_payload(voice=voice, text=text, metadata=metadata)
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.post(self._url(self.tts_path), headers=self._headers(), json=payload)
        body = self._json_response(response, label="Kling TTS submit")
        request_id = self._request_id(body, response)
        task_id = self._task_id(body)
        status = str(self._status(body) or "").strip().lower()
        if status not in self._TERMINAL_SUCCESS:
            if not task_id:
                raise ProviderBadResponseError("Kling TTS submit result has no task_id or audio result")
            for _poll_index in range(1, self.max_polls + 1):
                await asyncio.sleep(self.poll_interval_seconds)
                body = await self._query_tts_task(task_id)
                status = str(self._status(body) or "").strip().lower()
                if status in self._TERMINAL_SUCCESS:
                    break
                if status in self._TERMINAL_FAILURE:
                    raise ProviderError(f"Kling TTS task {task_id} ended with status {status}")
            else:
                raise ProviderError(f"Kling TTS task {task_id} did not finish after {self.max_polls} polls")

        audio = self._audio_record(body)
        audio_url = str((audio or {}).get("url") or "").strip()
        if not audio_url:
            raise ProviderBadResponseError(f"Kling TTS task {task_id or '-'} succeeded without an audio URL")
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            audio_response = await client.get(audio_url, follow_redirects=True)
        if audio_response.status_code >= 400 or not audio_response.content:
            raise ProviderBadResponseError(
                f"Kling TTS audio download failed with HTTP {audio_response.status_code}"
            )
        content_type = str(audio_response.headers.get("content-type") or "").lower()
        audio_format = "wav" if "wav" in content_type else "mp3"
        raw_response = dict(body)
        raw_response["tts_audio"] = {
            "id": (audio or {}).get("id"),
            "duration": (audio or {}).get("duration"),
            "format": audio_format,
        }
        return VoiceSynthesisResult(
            provider=self.name,
            model=self.tts_model,
            voice=str(voice),
            audio_data=base64.b64encode(audio_response.content).decode("ascii"),
            audio_format=audio_format,
            request_id=request_id or self._first(body, "request_id", "requestId"),
            usage=self._usage(body),
            raw_response=raw_response,
        )

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
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
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
            if len(images) < 2 or len(images) > 4:
                raise ValueError(
                    "Kling image_refer subject creation requires one frontal image and 1-3 other reference images"
                )
            if len(set(images)) != len(images):
                raise ValueError("Kling image_refer subject reference images must be different")
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
        return await self.query_subject_element(task_id=task_id)

    async def query_subject_element(
        self,
        *,
        task_id: str | None = None,
        external_task_id: str | None = None,
    ) -> SubjectElementResult:
        if not task_id and not external_task_id:
            raise ValueError("Kling subject element query requires task_id or external_task_id")
        if task_id and external_task_id:
            raise ValueError("Kling subject element query accepts only one of task_id or external_task_id")
        async with httpx.AsyncClient(
            timeout=self.runtime.request_timeout_seconds,
            trust_env=self.httpx_trust_env,
        ) as client:
            response = await client.get(
                self._query_url(
                    option_key="subject_element_query_endpoint",
                    default_path=self.subject_element_path,
                    task_id=external_task_id or task_id,
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
            result = await self.query_subject_element(task_id=task_id)
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
        task_id = cls._task_id(body) or fallback_task_id
        task_status = cls._status(body)
        element_id = cls._subject_element_id(body)
        return SubjectElementResult(
            provider=cls.name,
            model=str(cls._first(body, "model_name", "model") or fallback_model or "advanced-custom-elements"),
            task_id=task_id,
            task_status=task_status,
            element_id=str(element_id) if element_id is not None else None,
            request_id=request_id or cls._first(body, "request_id", "requestId"),
            usage=cls._usage(body),
            raw_response=body,
        )

    @classmethod
    def _voice_result(
        cls,
        body: dict[str, Any],
        *,
        request_id: str | None = None,
        fallback_task_id: str | None = None,
        fallback_model: str | None = None,
    ) -> VoiceAssetResult:
        voice = cls._voice_record(body) or {}
        voice_id = voice.get("voice_id") or voice.get("voiceId") or voice.get("id")
        return VoiceAssetResult(
            provider=cls.name,
            model=str(cls._first(body, "model_name", "model") or fallback_model or "custom-voices"),
            task_id=cls._task_id(body) or fallback_task_id,
            task_status=cls._status(body),
            voice_id=str(voice_id) if voice_id is not None else None,
            voice_name=str(voice.get("voice_name") or voice.get("voiceName") or voice.get("name") or "") or None,
            trial_url=str(voice.get("trial_url") or voice.get("trialUrl") or voice.get("url") or "") or None,
            owned_by=str(voice.get("owned_by") or voice.get("ownedBy") or "") or None,
            request_id=request_id or cls._first(body, "request_id", "requestId"),
            usage=cls._usage(body),
            raw_response=body,
        )

    @classmethod
    def _voice_record(cls, value: object, *, inside_voice_list: bool = False) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if inside_voice_list and any(key in value for key in ("voice_id", "voiceId", "id")):
                return value
            for key in ("voices", "voice_list", "voiceList"):
                found = cls._voice_record(value.get(key), inside_voice_list=True)
                if found is not None:
                    return found
            if str(value.get("type") or "").strip().lower() == "voice" and value.get("id") is not None:
                return value
            for key in ("data", "output", "outputs", "result", "task_result", "taskResult", "content"):
                found = cls._voice_record(value.get(key), inside_voice_list=inside_voice_list)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._voice_record(item, inside_voice_list=inside_voice_list)
                if found is not None:
                    return found
        return None

    @classmethod
    def _voice_records(cls, value: object, *, inside_voice_list: bool = False) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if inside_voice_list and any(key in value for key in ("voice_id", "voiceId", "id")):
                records.append(value)
            for key, nested in value.items():
                records.extend(
                    cls._voice_records(
                        nested,
                        inside_voice_list=inside_voice_list or key in {"voices", "voice_list", "voiceList"},
                    )
                )
        elif isinstance(value, list):
            for item in value:
                records.extend(cls._voice_records(item, inside_voice_list=inside_voice_list))
        return records

    @classmethod
    def _audio_record(cls, value: object, *, inside_audio_list: bool = False) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if inside_audio_list and value.get("url"):
                return value
            for key, nested in value.items():
                found = cls._audio_record(
                    nested,
                    inside_audio_list=inside_audio_list or key in {"audios", "audio_list", "audioList"},
                )
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._audio_record(item, inside_audio_list=inside_audio_list)
                if found is not None:
                    return found
        return None

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
    def _subject_element_id(cls, value: object, *, allow_generic_id: bool = False) -> Any:
        direct_keys = (
            "element_id",
            "elementId",
            "subject_element_id",
            "subjectElementId",
            "custom_element_id",
            "customElementId",
        )
        if isinstance(value, dict):
            for key in direct_keys:
                candidate = value.get(key)
                if candidate is not None:
                    return candidate
            if allow_generic_id:
                candidate = value.get("id")
                if candidate is not None:
                    return candidate
            for key in ("data", "output", "result", "task_result", "taskResult", "content"):
                found = cls._subject_element_id(value.get(key))
                if found is not None:
                    return found
            for key in (
                "elements",
                "element_list",
                "elementList",
                "custom_elements",
                "customElements",
                "subject_elements",
                "subjectElements",
                "works",
                "items",
            ):
                found = cls._subject_element_id(value.get(key), allow_generic_id=True)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._subject_element_id(item, allow_generic_id=allow_generic_id)
                if found is not None:
                    return found
        return None

    @classmethod
    def _first_url(cls, value: object) -> str | None:
        if isinstance(value, dict):
            for key in ("video_url", "videoUrl", "url"):
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
        for container_key in (
            "data",
            "output",
            "outputs",
            "result",
            "task_result",
            "taskResult",
            "task_info",
            "taskInfo",
        ):
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
