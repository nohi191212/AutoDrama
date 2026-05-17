from __future__ import annotations

import base64
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import MusicGenerationResult


class MiniMaxMusicProvider:
    """MiniMax Music 2.6 provider for instrumental BGM generation."""

    name = "minimax"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.minimaxi.com").rstrip("/")
        self.endpoint = self._resolve_endpoint(self.base_url)
        self.model = settings.models.get("music", "music-2.6")
        self.api_key = settings.secret("api_key_env")
        self.audio_format = str(settings.options.get("music_format", settings.options.get("format", "mp3"))).lower()
        self.sample_rate = int(settings.options.get("music_sample_rate", settings.options.get("sample_rate", 44100)))
        self.bitrate = int(settings.options.get("music_bitrate", settings.options.get("bitrate", 256000)))
        self.output_format = str(
            settings.options.get("music_output_format", settings.options.get("output_format", "url"))
        ).lower()
        self.is_instrumental = self._bool_option(
            settings.options.get("music_is_instrumental", settings.options.get("is_instrumental", True))
        )
        self.lyrics_optimizer = self._bool_option(settings.options.get("lyrics_optimizer", False))
        self.aigc_watermark = self._bool_option(
            settings.options.get("aigc_watermark", settings.options.get("enable_aigc_watermark", False))
        )

    @staticmethod
    def _resolve_endpoint(base_url: str) -> str:
        if base_url.endswith("/v1/music_generation"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/music_generation"
        return f"{base_url}/v1/music_generation"

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
        metadata = metadata or {}
        audio_setting = {
            "sample_rate": int(metadata.get("sample_rate", self.sample_rate)),
            "bitrate": int(metadata.get("bitrate", self.bitrate)),
            "format": str(metadata.get("format", metadata.get("audio_format", self.audio_format))).lower(),
        }
        if isinstance(metadata.get("audio_setting"), dict):
            audio_setting.update(metadata["audio_setting"])

        if "is_instrumental" in metadata:
            is_instrumental = self._bool_option(metadata["is_instrumental"])
        else:
            is_instrumental = self.is_instrumental if not lyrics else False

        payload: dict[str, Any] = {
            "model": metadata.get("model", self.model),
            "prompt": prompt[:2000],
            "audio_setting": audio_setting,
            "output_format": str(metadata.get("output_format", self.output_format)).lower(),
            "is_instrumental": is_instrumental,
        }

        if lyrics:
            payload["lyrics"] = lyrics[:3500]

        lyrics_optimizer = metadata.get("lyrics_optimizer", self.lyrics_optimizer)
        if lyrics_optimizer:
            payload["lyrics_optimizer"] = self._bool_option(lyrics_optimizer)

        aigc_watermark = metadata.get("aigc_watermark", self.aigc_watermark)
        if aigc_watermark:
            payload["aigc_watermark"] = self._bool_option(aigc_watermark)

        for optional_key in (
            "audio_url",
            "audio_base64",
            "cover_feature_id",
            "voice_id",
            "timber_weights",
            "stream",
        ):
            if optional_key in metadata:
                payload[optional_key] = metadata[optional_key]

        return payload

    async def generate_music(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MusicGenerationResult:
        if not self.api_key:
            raise ProviderAuthError("Missing MiniMax API key environment variable")

        metadata = metadata or {}
        payload = self.build_generation_payload(prompt, lyrics=lyrics, metadata=metadata)

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            try:
                response = await client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.ConnectError as exc:
                raise ProviderBadResponseError(
                    f"MiniMax music generation connection failed before receiving an HTTP response. "
                    f"Check network/proxy/TLS settings for {self.endpoint}: {exc}"
                ) from exc

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"MiniMax music generation failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"MiniMax music generation returned non-JSON response: {exc}") from exc

        self._raise_for_base_resp(body)
        audio_url, audio_data = self._extract_audio(body)
        if not audio_url and not audio_data:
            raise ProviderBadResponseError(f"MiniMax music generation response has no audio URL or data: {body}")

        audio_setting = payload.get("audio_setting") if isinstance(payload.get("audio_setting"), dict) else {}
        return MusicGenerationResult(
            provider=self.name,
            model=str(payload["model"]),
            audio_id=self._extract_audio_id(body),
            audio_url=audio_url,
            audio_data=audio_data,
            audio_format=str(audio_setting.get("format") or self.audio_format),
            duration_seconds=self._extract_duration_seconds(body),
            lyrics=self._extract_lyrics(body),
            sample_rate=self._extract_sample_rate(body) or int(audio_setting.get("sample_rate") or self.sample_rate),
            request_id=self._extract_request_id(body, response),
            usage=self._extract_usage(body),
            raw_response=self._without_audio_payload(body),
        )

    @staticmethod
    def _raise_for_base_resp(body: dict[str, Any]) -> None:
        base_resp = body.get("base_resp")
        if not isinstance(base_resp, dict):
            return
        status_code = base_resp.get("status_code")
        if status_code in (None, 0, "0"):
            return
        status_msg = base_resp.get("status_msg") or base_resp.get("message") or "unknown error"
        raise ProviderBadResponseError(f"MiniMax music generation failed: {status_code} {status_msg}")

    @classmethod
    def _extract_audio(cls, body: dict[str, Any]) -> tuple[str | None, str | None]:
        for container in cls._candidate_containers(body):
            audio = container.get("audio")
            if isinstance(audio, dict):
                url, data = cls._extract_audio_from_container(audio)
                if url or data:
                    return url, data
            elif isinstance(audio, str):
                url, data = cls._split_audio_value(audio)
                if url or data:
                    return url, data

            url, data = cls._extract_audio_from_container(container)
            if url or data:
                return url, data

        return None, None

    @staticmethod
    def _candidate_containers(body: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = [body]
        for key in ("data", "output", "result"):
            value = body.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        return candidates

    @classmethod
    def _extract_audio_from_container(cls, container: dict[str, Any]) -> tuple[str | None, str | None]:
        for key in ("url", "audio_url", "download_url", "file_url", "audio_file", "file"):
            value = container.get(key)
            if isinstance(value, str) and cls._looks_like_url(value):
                return value, None

        for key in ("data", "audio_data", "audio_base64", "audio_hex"):
            value = container.get(key)
            if isinstance(value, str):
                url, data = cls._split_audio_value(value)
                if url or data:
                    return url, data

        return None, None

    @classmethod
    def _split_audio_value(cls, value: str) -> tuple[str | None, str | None]:
        value = value.strip()
        if not value:
            return None, None
        if cls._looks_like_url(value):
            return value, None
        if value.startswith("data:") and ";base64," in value:
            return None, value.split(";base64,", 1)[1]
        if cls._looks_like_hex(value):
            return None, base64.b64encode(bytes.fromhex(value)).decode("ascii")
        return None, value

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return value.startswith("http://") or value.startswith("https://")

    @staticmethod
    def _looks_like_hex(value: str) -> bool:
        if len(value) < 32 or len(value) % 2 != 0:
            return False
        return all(ch in "0123456789abcdefABCDEF" for ch in value)

    @staticmethod
    def _extract_audio_id(body: dict[str, Any]) -> str | None:
        for container in MiniMaxMusicProvider._candidate_containers(body):
            for key in ("audio_id", "track_id", "task_id", "id"):
                value = container.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _extract_duration_seconds(body: dict[str, Any]) -> float | None:
        for container in MiniMaxMusicProvider._candidate_containers(body):
            for key in ("duration_seconds", "duration"):
                value = container.get(key)
                if value is not None:
                    return MiniMaxMusicProvider._float_or_none(value)
            extra_info = container.get("extra_info")
            if isinstance(extra_info, dict):
                value = extra_info.get("music_duration") or extra_info.get("audio_duration")
                numeric = MiniMaxMusicProvider._float_or_none(value)
                if numeric is not None:
                    return numeric / 1000 if numeric > 600 else numeric
        return None

    @staticmethod
    def _extract_sample_rate(body: dict[str, Any]) -> int | None:
        for container in MiniMaxMusicProvider._candidate_containers(body):
            for key in ("sample_rate", "music_sample_rate"):
                value = container.get(key)
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    pass
            extra_info = container.get("extra_info")
            if isinstance(extra_info, dict):
                for key in ("sample_rate", "music_sample_rate"):
                    value = extra_info.get(key)
                    try:
                        return int(value) if value is not None else None
                    except (TypeError, ValueError):
                        pass
        return None

    @staticmethod
    def _extract_lyrics(body: dict[str, Any]) -> str | None:
        for container in MiniMaxMusicProvider._candidate_containers(body):
            value = container.get("lyrics") or container.get("formatted_lyrics")
            if value:
                return str(value)
            extra_info = container.get("extra_info")
            if isinstance(extra_info, dict):
                value = extra_info.get("lyrics") or extra_info.get("formatted_lyrics")
                if value:
                    return str(value)
        return None

    @staticmethod
    def _extract_request_id(body: dict[str, Any], response: httpx.Response) -> str | None:
        for key in ("request_id", "requestId", "trace_id", "task_id"):
            value = body.get(key)
            if value:
                return str(value)
        return response.headers.get("x-request-id") or response.headers.get("x-trace-id")

    @staticmethod
    def _extract_usage(body: dict[str, Any]) -> dict[str, Any]:
        usage = body.get("usage")
        if isinstance(usage, dict):
            return usage
        usage: dict[str, Any] = {}
        duration = MiniMaxMusicProvider._extract_duration_seconds(body)
        if duration is not None:
            usage["duration"] = duration
        return usage

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _without_audio_payload(cls, body: dict[str, Any]) -> dict[str, Any]:
        return cls._sanitize_value(body)

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, nested in value.items():
                if key in {"audio", "audio_data", "audio_base64", "audio_hex"}:
                    if isinstance(nested, str) and cls._looks_like_url(nested):
                        sanitized[key] = nested
                    else:
                        sanitized[key] = "<audio payload omitted>"
                    continue
                sanitized[key] = cls._sanitize_value(nested)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_value(item) for item in value]
        return value
