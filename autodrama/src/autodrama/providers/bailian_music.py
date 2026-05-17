from __future__ import annotations

from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import MusicGenerationResult


class BailianMusicProvider:
    """DashScope Fun-Music provider via Alibaba Bailian."""

    name = "bailian"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("music", "fun-music-v1")
        self.api_key = settings.secret("api_key_env") or settings.api_key_env
        self.audio_format = str(settings.options.get("music_format", settings.options.get("format", "mp3")))
        self.gender = str(settings.options.get("music_gender", settings.options.get("gender", "female")))
        self.enable_watermark = bool(settings.options.get("enable_aigc_watermark", False))

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        if base_url.endswith("/services/audio/music/generation"):
            return base_url
        if base_url.endswith("/api/v1"):
            return f"{base_url}/services/audio/music/generation"
        return f"{base_url}/api/v1/services/audio/music/generation"

    async def generate_music(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MusicGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing Bailian/DashScope API key environment variable")

        metadata = metadata or {}
        input_payload: dict[str, Any] = {
            "gender": metadata.get("gender", self.gender),
            "format": metadata.get("format", self.audio_format),
            "enable_aigc_watermark": metadata.get("enable_aigc_watermark", self.enable_watermark),
        }
        if lyrics:
            input_payload["lyrics"] = lyrics
        else:
            input_payload["prompt"] = prompt[:2000]

        payload = {
            "model": metadata.get("model", self.model),
            "input": input_payload,
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
                f"Bailian music generation failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"Bailian music generation returned non-JSON response: {exc}") from exc

        output = body.get("output") or {}
        audio = output.get("audio") or {}
        extra_info = output.get("extra_info") or {}
        usage = body.get("usage") or {}
        sample_rate = extra_info.get("sample_rate")
        try:
            sample_rate_value = int(sample_rate) if sample_rate is not None else None
        except (TypeError, ValueError):
            sample_rate_value = None

        return MusicGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            audio_id=audio.get("id"),
            audio_url=audio.get("url"),
            audio_data=audio.get("data") or None,
            audio_format=str(input_payload.get("format") or "mp3"),
            duration_seconds=usage.get("duration"),
            lyrics=extra_info.get("lyrics"),
            sample_rate=sample_rate_value,
            request_id=body.get("request_id") or body.get("requestId"),
            usage=usage,
            raw_response=body,
        )
