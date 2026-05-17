from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError, ProviderError
from autodrama.providers.base import VoiceDesignResult, VoiceSynthesisResult


class VolcengineVoiceProvider:
    """Volcengine OpenSpeech voice design, clone, and Seed ICL TTS provider."""

    name = "volcengine"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://openspeech.bytedance.com").rstrip("/")
        self.voice_design_endpoint = self._endpoint("voice_design")
        self.voice_clone_endpoint = self._endpoint("voice_clone")
        self.get_voice_endpoint = self._endpoint("get_voice")
        self.tts_endpoint = self._endpoint("unidirectional")
        self.model = settings.models.get("voice_design", "voice_design")
        self.clone_model = settings.models.get("voice_clone", "voice_clone")
        self.target_model = settings.models.get("tts", settings.models.get("target_model", "seed-icl-2.0"))
        self.clone_target_model = settings.models.get("clone_target_model", self.target_model)
        self.api_key = settings.secret("api_key_env")
        self.app_key = settings.secret("app_key_env") or settings.secret("app_id_env")
        self.access_key = settings.secret("access_key_env") or settings.secret("secret_key_env")
        self.language = self._language_code(settings.options.get("language", 0))
        self.sample_rate = int(settings.options.get("sample_rate", 24000))
        self.response_format = str(settings.options.get("response_format", "mp3")).lower()
        self.poll_interval_seconds = float(settings.options.get("poll_interval_seconds", 5))
        self.max_polls = int(settings.options.get("max_polls", 60))
        self._speaker_allocations: dict[str, str] = {}
        self._speaker_pool_cursor = 0

    def _endpoint(self, operation: str) -> str:
        if self.base_url.endswith(f"/api/v3/tts/{operation}"):
            return self.base_url
        if self.base_url.endswith("/api/v3/tts"):
            return f"{self.base_url}/{operation}"
        return f"{self.base_url}/api/v3/tts/{operation}"

    @staticmethod
    def _language_code(value: object) -> int:
        if isinstance(value, int):
            return value
        normalized = str(value or "cn").strip().lower()
        return {
            "cn": 0,
            "zh": 0,
            "zh-cn": 0,
            "chinese": 0,
            "en": 1,
            "english": 1,
            "ja": 2,
            "jp": 2,
            "es": 3,
            "id": 4,
            "pt": 5,
            "de": 6,
            "fr": 7,
            "ko": 8,
        }.get(normalized, 0)

    @property
    def supports_local_voice_clone(self) -> bool:
        return True

    def _auth_headers(self, *, resource_id: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Request-Id": uuid4().hex,
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        elif self.app_key and self.access_key:
            headers["X-Api-App-Key"] = self.app_key
            headers["X-Api-App-Id"] = self.app_key
            headers["X-Api-Access-Key"] = self.access_key
        else:
            raise ProviderAuthError(
                "Missing Volcengine Speech credentials. Set api_key_env, or app_id_env/app_key_env plus access_key_env."
            )
        if resource_id:
            headers["X-Api-Resource-Id"] = resource_id
        return headers

    async def create_voice(
        self,
        *,
        voice_prompt: str,
        preview_text: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        speaker_id = self._resolve_speaker_id(preferred_name, metadata)
        target_model = str(metadata.get("target_model", self.target_model))
        language = self._language_code(metadata.get("language", self.language))
        payload = {
            "speaker_id": speaker_id,
            "text": preview_text[:300],
            "prompt": {
                "text_prompt": voice_prompt[:200],
            },
            "language": language,
        }
        image_prompt = metadata.get("image_prompt")
        if isinstance(image_prompt, dict):
            payload["prompt"]["image_prompt"] = image_prompt
        if extra_prompt := metadata.get("prompt"):
            if isinstance(extra_prompt, dict):
                payload["prompt"].update(extra_prompt)

        body, response = await self._post_json(self.voice_design_endpoint, payload)
        completed = await self._wait_for_ready_voice(speaker_id, body)
        demo_audio_url = self._demo_audio_url(completed) or self._demo_audio_url(body)
        preview_audio_data, preview_audio_format = await self._preview_audio(
            speaker_id=speaker_id,
            preview_text=preview_text,
            demo_audio_url=demo_audio_url,
            target_model=target_model,
            metadata=metadata,
        )

        return VoiceDesignResult(
            provider=self.name,
            model=self.model,
            voice=speaker_id,
            target_model=target_model,
            preview_audio_data=preview_audio_data,
            preview_audio_sample_rate=self.sample_rate,
            preview_audio_format=preview_audio_format,
            request_id=response.headers.get("X-Tt-Logid") or response.headers.get("X-Api-Request-Id"),
            usage=self._usage(completed or body),
            raw_response={
                "create": body,
                "status": completed,
                "request_headers": self._safe_headers(response.request.headers),
                "response_headers": self._selected_response_headers(response),
            },
        )

    async def clone_voice_from_audio(
        self,
        *,
        source_audio_path: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        speaker_id = self._resolve_speaker_id(preferred_name, metadata)
        target_model = str(metadata.get("target_model", self.clone_target_model))
        audio_path = Path(source_audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Voice clone source audio does not exist: {source_audio_path}")

        audio_format = self._audio_format(audio_path, metadata)
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        payload: dict[str, Any] = {
            "speaker_id": speaker_id,
            "audio": {
                "data": audio_data,
                "format": audio_format,
            },
            "language": self._language_code(metadata.get("language", self.language)),
        }
        if clone_text := metadata.get("text"):
            payload["text"] = str(clone_text)[:1000]

        extra_params: dict[str, Any] = {}
        configured_extra = self.settings.options.get("voice_clone_extra_params")
        if isinstance(configured_extra, dict):
            extra_params.update(configured_extra)
        metadata_extra = metadata.get("extra_params")
        if isinstance(metadata_extra, dict):
            extra_params.update(metadata_extra)
        if extra_params:
            payload["extra_params"] = extra_params

        body, response = await self._post_json(self.voice_clone_endpoint, payload)
        completed = await self._wait_for_ready_voice(speaker_id, body)

        return VoiceDesignResult(
            provider=self.name,
            model=self.clone_model,
            voice=speaker_id,
            target_model=target_model,
            request_id=response.headers.get("X-Tt-Logid") or response.headers.get("X-Api-Request-Id"),
            usage=self._usage(completed or body),
            raw_response={
                "clone": self._without_audio_data(body),
                "status": completed,
                "request_headers": self._safe_headers(response.request.headers),
                "response_headers": self._selected_response_headers(response),
            },
        )

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        metadata = metadata or {}
        model = str(metadata.get("model") or metadata.get("target_model") or self.clone_target_model)
        response_format = str(metadata.get("response_format", self.response_format)).lower()
        sample_rate = int(metadata.get("sample_rate", self.sample_rate))
        payload: dict[str, Any] = {
            "user": {
                "uid": str(metadata.get("uid") or metadata.get("project_id") or "autodrama"),
            },
            "req_params": {
                "text": text[:1024],
                "speaker": voice,
                "audio_params": {
                    "format": response_format,
                    "sample_rate": sample_rate,
                },
            },
        }
        if additions := metadata.get("req_params"):
            if isinstance(additions, dict):
                payload["req_params"].update(additions)

        headers = self._auth_headers(resource_id=model)
        audio_chunks: list[bytes] = []
        response_headers: dict[str, str] = {}
        raw_events: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            async with client.stream("POST", self.tts_endpoint, headers=headers, json=payload) as response:
                response_headers = self._selected_response_headers(response)
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ProviderBadResponseError(
                        f"Volcengine speech synthesis failed with HTTP {response.status_code}: "
                        f"{body.decode('utf-8', errors='replace')[:500]}"
                    )
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type.startswith("audio/") or content_type == "application/octet-stream":
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            audio_chunks.append(chunk)
                    raw_events.append({"content_type": content_type, "audio": "<binary audio omitted>"})
                else:
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = self._parse_stream_event(line)
                        if event is None:
                            continue
                        raw_events.append(self._without_event_audio(event))
                        audio_value = self._event_audio_data(event)
                        if audio_value:
                            audio_chunks.append(base64.b64decode(audio_value))
                        if self._event_is_error(event):
                            raise ProviderBadResponseError(f"Volcengine speech synthesis error event: {event}")

        if not audio_chunks:
            raise ProviderBadResponseError("Volcengine speech synthesis returned no audio chunks")

        audio_bytes = b"".join(audio_chunks)
        return VoiceSynthesisResult(
            provider=self.name,
            model=model,
            voice=voice,
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_sample_rate=sample_rate,
            audio_format=response_format,
            request_id=(
                response_headers.get("X-Tt-Logid")
                or response_headers.get("x-tt-logid")
                or response_headers.get("X-Api-Request-Id")
                or response_headers.get("x-api-request-id")
            ),
            usage={"chunk_count": len(audio_chunks), "byte_count": len(audio_bytes)},
            raw_response={
                "events": raw_events,
                "response_headers": response_headers,
            },
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], httpx.Response]:
        try:
            async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
                response = await client.post(url, headers=self._auth_headers(), json=payload)
        except httpx.ConnectError as exc:
            raise ProviderBadResponseError(
                f"Volcengine connection failed before receiving an HTTP response. "
                f"Check network/proxy/TLS settings for {url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Volcengine request failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Volcengine request returned non-JSON response: {exc}") from exc
        return body, response

    async def _wait_for_ready_voice(self, speaker_id: str, initial_body: dict[str, Any]) -> dict[str, Any]:
        if self._voice_is_ready(initial_body):
            return initial_body
        if self._voice_failed(initial_body):
            raise ProviderError(f"Volcengine voice {speaker_id} failed: {initial_body}")

        last_body = initial_body
        for _ in range(max(0, self.max_polls)):
            await asyncio.sleep(self.poll_interval_seconds)
            last_body, _ = await self._post_json(self.get_voice_endpoint, {"speaker_id": speaker_id})
            if self._voice_is_ready(last_body):
                return last_body
            if self._voice_failed(last_body):
                raise ProviderError(f"Volcengine voice {speaker_id} failed: {last_body}")
        raise ProviderError(f"Volcengine voice {speaker_id} did not become ready after {self.max_polls} polls")

    @staticmethod
    def _voice_is_ready(body: dict[str, Any]) -> bool:
        return body.get("status") in {2, 4}

    @staticmethod
    def _voice_failed(body: dict[str, Any]) -> bool:
        return body.get("status") in {0, 3}

    async def _preview_audio(
        self,
        *,
        speaker_id: str,
        preview_text: str,
        demo_audio_url: str | None,
        target_model: str,
        metadata: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        if demo_audio_url:
            async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
                response = await client.get(demo_audio_url)
            if response.status_code >= 400:
                raise ProviderBadResponseError(
                    f"Volcengine demo audio download failed with HTTP {response.status_code}: {response.text[:500]}"
                )
            return base64.b64encode(response.content).decode("ascii"), self._format_from_response(response)

        synthesis = await self.synthesize_speech(
            voice=speaker_id,
            text=preview_text,
            metadata={
                **metadata,
                "target_model": target_model,
                "response_format": self.response_format,
                "sample_rate": self.sample_rate,
            },
        )
        return synthesis.audio_data, synthesis.audio_format

    @staticmethod
    def _demo_audio_url(body: dict[str, Any]) -> str | None:
        if demo_audio := body.get("demo_audio"):
            return str(demo_audio)
        for item in body.get("speaker_status") or []:
            if isinstance(item, dict) and item.get("demo_audio"):
                return str(item["demo_audio"])
        return None

    def _resolve_speaker_id(self, preferred_name: str, metadata: dict[str, Any]) -> str:
        if explicit := metadata.get("speaker_id"):
            return str(explicit)

        key = self._speaker_key(preferred_name, metadata)
        if key in self._speaker_allocations:
            return self._speaker_allocations[key]

        options = self.settings.options
        speaker_ids = options.get("speaker_ids")
        if isinstance(speaker_ids, dict):
            for candidate_key in self._speaker_candidate_keys(preferred_name, metadata):
                if candidate := speaker_ids.get(candidate_key):
                    self._speaker_allocations[key] = str(candidate)
                    return str(candidate)

        speaker_pool = options.get("speaker_id_pool")
        if isinstance(speaker_pool, list) and speaker_pool:
            if self._speaker_pool_cursor >= len(speaker_pool):
                raise ProviderBadResponseError(
                    "Volcengine speaker_id_pool does not have enough speaker IDs for this voice generation run"
                )
            speaker_id = str(speaker_pool[self._speaker_pool_cursor])
            self._speaker_pool_cursor += 1
            self._speaker_allocations[key] = speaker_id
            return speaker_id

        if template := options.get("speaker_id_template"):
            speaker_id = str(template).format(
                preferred_name=preferred_name,
                project_id=metadata.get("project_id", ""),
                role_id=metadata.get("role_id", ""),
                role_name=metadata.get("role_name", ""),
                audio_id=metadata.get("audio_id", ""),
                emotion=metadata.get("emotion", ""),
                generation_method=metadata.get("generation_method", ""),
            )
            self._speaker_allocations[key] = speaker_id
            return speaker_id

        if speaker_id := options.get("speaker_id"):
            self._speaker_allocations[key] = str(speaker_id)
            return str(speaker_id)

        raise ProviderBadResponseError(
            "Missing Volcengine speaker_id. Configure providers.volcengine.options.speaker_id_pool, "
            "speaker_ids, speaker_id_template, or speaker_id."
        )

    @staticmethod
    def _speaker_key(preferred_name: str, metadata: dict[str, Any]) -> str:
        return str(metadata.get("audio_id") or metadata.get("role_id") or preferred_name)

    @staticmethod
    def _speaker_candidate_keys(preferred_name: str, metadata: dict[str, Any]) -> list[str]:
        role_id = str(metadata.get("role_id") or "")
        emotion = str(metadata.get("emotion") or "")
        audio_id = str(metadata.get("audio_id") or "")
        keys = [
            audio_id,
            f"{role_id}:{emotion}" if role_id and emotion else "",
            preferred_name,
            "default",
        ]
        if role_id and emotion in {"", "normal"}:
            keys.insert(2, role_id)
        return keys

    @staticmethod
    def _audio_format(audio_path: Path, metadata: dict[str, Any]) -> str:
        if explicit := metadata.get("audio_format"):
            return str(explicit).lower().lstrip(".")
        suffix = audio_path.suffix.lower().lstrip(".")
        if suffix in {"wav", "mp3", "ogg", "m4a", "aac", "pcm"}:
            return suffix
        mime = mimetypes.guess_type(audio_path.name)[0] or ""
        if "/" in mime:
            return mime.rsplit("/", 1)[1].lower().replace("mpeg", "mp3")
        return "wav"

    @staticmethod
    def _parse_stream_event(line: str) -> dict[str, Any] | None:
        value = line.strip()
        if value.startswith("data:"):
            value = value[5:].strip()
        if not value or value == "[DONE]":
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _event_audio_data(event: dict[str, Any]) -> str | None:
        for key in ("data", "audio", "audio_data"):
            value = event.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = value.get("data") or value.get("audio") or value.get("content")
                if isinstance(nested, str):
                    return nested
        result = event.get("result")
        if isinstance(result, dict):
            nested = result.get("data") or result.get("audio") or result.get("audio_data")
            if isinstance(nested, str):
                return nested
        return None

    @staticmethod
    def _event_is_error(event: dict[str, Any]) -> bool:
        code = event.get("code")
        if code in {None, 0, "0", 20000000, "20000000"}:
            return False
        return True

    @staticmethod
    def _without_event_audio(event: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(event)
        for key in ("data", "audio", "audio_data", "result"):
            value = sanitized.get(key)
            if isinstance(value, str):
                sanitized[key] = "<base64 audio omitted>"
            elif isinstance(value, dict):
                nested = dict(value)
                for nested_key in ("data", "audio", "audio_data", "content"):
                    if nested_key in nested:
                        nested[nested_key] = "<base64 audio omitted>"
                sanitized[key] = nested
        return sanitized

    @staticmethod
    def _without_audio_data(body: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(body)
        audio = sanitized.get("audio")
        if isinstance(audio, dict):
            nested = dict(audio)
            if "data" in nested:
                nested["data"] = "<base64 audio omitted>"
            sanitized["audio"] = nested
        return sanitized

    @staticmethod
    def _usage(body: dict[str, Any]) -> dict[str, Any]:
        usage: dict[str, Any] = {}
        for key in ("available_training_times", "create_time", "language", "status"):
            if key in body:
                usage[key] = body[key]
        return usage

    @staticmethod
    def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
        return {
            key: ("<secret omitted>" if key.lower() in {"x-api-key", "x-api-access-key"} else value)
            for key, value in headers.items()
            if key.lower().startswith("x-api-")
        }

    @staticmethod
    def _selected_response_headers(response: httpx.Response) -> dict[str, str]:
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() in {"x-tt-logid", "x-api-request-id", "content-type"}
        }

    @staticmethod
    def _format_from_response(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
            "audio/aac": "aac",
            "audio/mp4": "m4a",
        }.get(content_type, "mp3")
