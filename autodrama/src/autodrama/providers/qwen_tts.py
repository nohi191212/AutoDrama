from __future__ import annotations

from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import VoiceDesignResult


class QwenVoiceDesignProvider:
    """Qwen voice design provider via DashScope TTS customization API."""

    name = "qwen_tts"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.endpoint = self._resolve_endpoint(settings.base_url)
        self.model = settings.models.get("voice_design", "qwen-voice-design")
        self.target_model = settings.models.get("target_model") or settings.models.get(
            "tts", "qwen3-tts-vd-realtime-2026-01-15"
        )
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
