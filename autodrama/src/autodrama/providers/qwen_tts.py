from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import VoiceDesignResult, VoiceSynthesisResult


class QwenVoiceDesignProvider:
    """Qwen voice design provider via DashScope TTS customization API."""

    name = "qwen_tts"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.endpoint = self._resolve_endpoint(settings.base_url)
        self.generation_endpoint = self._resolve_generation_endpoint(settings.base_url)
        self.model = settings.models.get("voice_design", "qwen-voice-design")
        self.clone_model = settings.models.get("voice_clone", "qwen-voice-enrollment")
        self.target_model = settings.models.get("target_model") or settings.models.get(
            "tts", "qwen3-tts-vd-realtime-2026-01-15"
        )
        self.clone_target_model = settings.models.get("clone_target_model", self.target_model)
        self.api_key = settings.secret("api_key_env")
        if not self.api_key and settings.api_key_env and settings.api_key_env.startswith("sk-"):
            self.api_key = settings.api_key_env
        self.language = str(settings.options.get("language", "zh"))
        self.sample_rate = int(settings.options.get("sample_rate", 24000))
        self.response_format = str(settings.options.get("response_format", "wav"))

    @staticmethod
    def _resolve_endpoint(base_url: str | None) -> str:
        default_endpoint = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        if not base_url:
            return default_endpoint

        normalized = base_url.rstrip("/")
        if normalized.endswith("/services/audio/tts/customization"):
            return normalized
        if normalized.endswith("/api/v1"):
            return f"{normalized}/services/audio/tts/customization"
        return normalized

    @staticmethod
    def _resolve_generation_endpoint(base_url: str | None) -> str:
        default_endpoint = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        if not base_url:
            return default_endpoint

        normalized = base_url.rstrip("/")
        if normalized.endswith("/services/aigc/multimodal-generation/generation"):
            return normalized
        if normalized.endswith("/services/audio/tts/customization"):
            return normalized.replace(
                "/services/audio/tts/customization",
                "/services/aigc/multimodal-generation/generation",
            )
        if normalized.endswith("/api/v1"):
            return f"{normalized}/services/aigc/multimodal-generation/generation"
        return default_endpoint

    async def create_voice(
        self,
        *,
        voice_prompt: str,
        preview_text: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Qwen/DashScope API key environment variable")

        metadata = metadata or {}
        target_model = str(metadata.get("target_model", self.target_model))
        language = str(metadata.get("language", self.language))
        parameters = {
            "sample_rate": int(metadata.get("sample_rate", self.sample_rate)),
            "response_format": str(metadata.get("response_format", self.response_format)),
        }
        parameters.update(metadata.get("parameters", {}))

        payload = {
            "model": metadata.get("model", self.model),
            "input": {
                "action": "create",
                "target_model": target_model,
                "preferred_name": preferred_name[:16],
                "voice_prompt": voice_prompt[:2048],
                "preview_text": preview_text[:1024],
                "language": language,
            },
            "parameters": parameters,
        }

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Qwen voice design failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Qwen voice design returned non-JSON response: {exc}") from exc

        output = body.get("output") or {}
        voice = output.get("voice") or output.get("voice_id")
        if not voice:
            raise ProviderBadResponseError(f"Qwen voice design response missing output.voice: {body}")

        preview_audio = output.get("preview_audio") or {}
        return VoiceDesignResult(
            provider=self.name,
            model=str(payload["model"]),
            voice=str(voice),
            target_model=output.get("target_model") or target_model,
            preview_audio_data=preview_audio.get("data"),
            preview_audio_sample_rate=preview_audio.get("sample_rate"),
            preview_audio_format=preview_audio.get("response_format"),
            request_id=body.get("request_id"),
            usage=body.get("usage") or {},
            raw_response=self._without_preview_audio_data(body),
        )

    async def clone_voice_from_audio(
        self,
        *,
        source_audio_path: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Qwen/DashScope API key environment variable")

        metadata = metadata or {}
        target_model = str(metadata.get("target_model", self.clone_target_model))
        audio_path = Path(source_audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Voice clone source audio does not exist: {source_audio_path}")

        audio_mime_type = str(metadata.get("audio_mime_type") or mimetypes.guess_type(audio_path.name)[0] or "audio/wav")
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        payload = {
            "model": metadata.get("model", self.clone_model),
            "input": {
                "action": "create",
                "target_model": target_model,
                "preferred_name": preferred_name[:16],
                "audio": {"data": f"data:{audio_mime_type};base64,{audio_data}"},
            },
        }

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Qwen voice clone failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Qwen voice clone returned non-JSON response: {exc}") from exc

        output = body.get("output") or {}
        voice = output.get("voice") or output.get("voice_id")
        if not voice:
            raise ProviderBadResponseError(f"Qwen voice clone response missing output.voice: {body}")

        return VoiceDesignResult(
            provider=self.name,
            model=str(payload["model"]),
            voice=str(voice),
            target_model=output.get("target_model") or target_model,
            request_id=body.get("request_id"),
            usage=body.get("usage") or {},
            raw_response=body,
        )

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Qwen/DashScope API key environment variable")

        metadata = metadata or {}
        model = str(metadata.get("model") or metadata.get("target_model") or self.clone_target_model)
        response_format = str(metadata.get("response_format", self.response_format))
        payload: dict[str, Any] = {
            "model": model,
            "input": {
                "text": text[:1024],
                "voice": voice,
            },
        }
        if parameters := metadata.get("parameters"):
            payload["parameters"] = parameters

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                self.generation_endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Qwen voice synthesis failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Qwen voice synthesis returned non-JSON response: {exc}") from exc

        output = body.get("output") or {}
        audio_data, sample_rate, audio_format = self._extract_audio(output, response_format)
        return VoiceSynthesisResult(
            provider=self.name,
            model=model,
            voice=voice,
            audio_data=audio_data,
            audio_sample_rate=sample_rate,
            audio_format=audio_format,
            request_id=body.get("request_id"),
            usage=body.get("usage") or {},
            raw_response=self._without_audio_data(body),
        )

    @staticmethod
    def _without_preview_audio_data(body: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(body)
        output = dict(sanitized.get("output") or {})
        preview_audio = dict(output.get("preview_audio") or {})
        if "data" in preview_audio:
            preview_audio["data"] = "<base64 preview audio omitted>"
        if preview_audio:
            output["preview_audio"] = preview_audio
        if output:
            sanitized["output"] = output
        return sanitized

    @staticmethod
    def _extract_audio(output: dict[str, Any], default_format: str) -> tuple[str | None, int | None, str | None]:
        for key in ("audio", "audio_data", "result"):
            value = output.get(key)
            if isinstance(value, dict):
                return (
                    value.get("data") or value.get("audio") or value.get("content"),
                    value.get("sample_rate"),
                    value.get("format") or value.get("response_format") or default_format,
                )
            if isinstance(value, str):
                return value, output.get("sample_rate"), output.get("format") or output.get("response_format") or default_format
        return (
            output.get("data"),
            output.get("sample_rate"),
            output.get("format") or output.get("response_format") or default_format,
        )

    @staticmethod
    def _without_audio_data(body: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(body)
        output = dict(sanitized.get("output") or {})
        for key in ("audio", "audio_data", "data", "result"):
            value = output.get(key)
            if isinstance(value, dict):
                nested = dict(value)
                for nested_key in ("data", "audio", "content"):
                    if nested_key in nested:
                        nested[nested_key] = "<base64 audio omitted>"
                output[key] = nested
            elif isinstance(value, str):
                output[key] = "<base64 audio omitted>"
        if output:
            sanitized["output"] = output
        return sanitized
