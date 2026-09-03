from __future__ import annotations

import base64
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import MusicGenerationResult


class ElevenLabsMusicProvider:
    """ElevenLabs music composition provider."""

    name = "elevenlabs"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.elevenlabs.io").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("music", "music_v1")
        self.api_key = settings.secret("api_key_env")
        self.output_format = str(settings.options.get("output_format", "mp3_44100_128"))
        self.force_instrumental = self._bool_option(settings.options.get("force_instrumental", True))
        self.respect_sections_durations = self._bool_option(
            settings.options.get("respect_sections_durations", True)
        )
        self.timeout_seconds = float(
            settings.options.get(
                "music_timeout_seconds",
                settings.options.get("timeout_seconds", max(runtime.request_timeout_seconds, 600)),
            )
        )

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        if base_url.endswith("/v1/music"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/music"
        return f"{base_url}/v1/music"

    @staticmethod
    def _bool_option(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def build_generation_payload(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del lyrics
        metadata = metadata or {}
        if isinstance(metadata.get("composition_plan"), dict):
            payload: dict[str, Any] = {
                "composition_plan": metadata["composition_plan"],
                "model_id": str(metadata.get("model_id") or metadata.get("model") or self.model),
                "respect_sections_durations": self._bool_option(
                    metadata.get("respect_sections_durations", self.respect_sections_durations)
                ),
            }
        else:
            duration_ms = metadata.get("music_length_ms")
            if duration_ms is None:
                duration_seconds = metadata.get("duration_seconds", metadata.get("duration"))
                if duration_seconds is not None:
                    duration_ms = round(float(duration_seconds) * 1000)
            payload = {
                "prompt": prompt[:4000],
                "model_id": str(metadata.get("model_id") or metadata.get("model") or self.model),
                "force_instrumental": self._bool_option(
                    metadata.get("force_instrumental", self.force_instrumental)
                ),
            }
            if duration_ms is not None:
                payload["music_length_ms"] = int(duration_ms)

        for key in ("seed", "store_for_inpainting", "sign_with_c2pa"):
            if key in metadata:
                payload[key] = metadata[key]
        return payload

    async def generate_music(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MusicGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing ElevenLabs API key environment variable")

        metadata = metadata or {}
        payload = self.build_generation_payload(prompt, lyrics=lyrics, metadata=metadata)
        output_format = str(metadata.get("output_format") or self.output_format)
        request_timeout = self._http_timeout()
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            try:
                response = await client.post(
                    self.endpoint,
                    params={"output_format": output_format},
                    headers={
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.ConnectError as exc:
                raise ProviderBadResponseError(
                    f"ElevenLabs music connection failed before receiving an HTTP response. "
                    f"Check network/proxy/TLS settings for {self.endpoint}: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise ProviderBadResponseError(
                    f"ElevenLabs music generation timed out after {self.timeout_seconds:g}s before receiving "
                    f"a complete HTTP response from {self.endpoint}."
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderBadResponseError(f"ElevenLabs music request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"ElevenLabs music generation failed with HTTP {response.status_code}: "
                f"{self._error_text(response)[:800]}"
            )

        audio_bytes = response.content
        if not audio_bytes:
            raise ProviderBadResponseError("ElevenLabs music generation returned an empty audio response")

        audio_format = self._audio_extension(output_format)
        return MusicGenerationResult(
            provider=self.name,
            model=str(payload.get("model_id") or self.model),
            audio_id=self._header_value(response, "song-id", "song_id", "x-song-id", "elevenlabs-song-id"),
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_format=audio_format,
            duration_seconds=self._duration_seconds(payload),
            request_id=self._header_value(response, "request-id", "x-request-id", "xi-request-id"),
            usage={"duration": self._duration_seconds(payload)} if self._duration_seconds(payload) else {},
            raw_response={
                "endpoint": self.endpoint,
                "output_format": output_format,
                "content_type": response.headers.get("content-type"),
                "content_length": response.headers.get("content-length"),
                "payload": self._safe_payload(payload),
            },
        )

    def _http_timeout(self) -> httpx.Timeout:
        connect_timeout = min(30.0, max(5.0, self.timeout_seconds))
        return httpx.Timeout(
            timeout=self.timeout_seconds,
            connect=connect_timeout,
            read=self.timeout_seconds,
            write=connect_timeout,
            pool=connect_timeout,
        )

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            return response.text
        except Exception:
            return repr(response.content[:500])

    @staticmethod
    def _audio_extension(output_format: str) -> str:
        codec = output_format.split("_", 1)[0].strip().lower()
        if codec == "pcm":
            return "pcm"
        if codec in {"mp3", "opus", "ulaw", "alaw"}:
            return codec
        return "mp3"

    @staticmethod
    def _header_value(response: httpx.Response, *keys: str) -> str | None:
        for key in keys:
            value = response.headers.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _duration_seconds(payload: dict[str, Any]) -> float | None:
        value = payload.get("music_length_ms")
        try:
            return round(float(value) / 1000, 3) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        safe = dict(payload)
        prompt = safe.get("prompt")
        if isinstance(prompt, str) and len(prompt) > 500:
            safe["prompt"] = prompt[:500] + "..."
        return safe
