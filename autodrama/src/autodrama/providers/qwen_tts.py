from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import VoiceDesignResult, VoiceSynthesisResult


class QwenVoiceDesignProvider:
    """DashScope TTS voice design provider for Qwen-TTS and CosyVoice."""

    name = "qwen_tts"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.endpoint = self._resolve_endpoint(settings.base_url)
        self.generation_endpoint = self._resolve_generation_endpoint(settings.base_url)
        self.websocket_endpoint = self._resolve_websocket_endpoint(
            settings.base_url,
            settings.options.get("websocket_url"),
        )
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

    @property
    def is_cosyvoice(self) -> bool:
        return self.target_model.startswith("cosyvoice-") or self.model == "voice-enrollment"

    @property
    def supports_local_voice_clone(self) -> bool:
        return not self.is_cosyvoice

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

    @staticmethod
    def _resolve_websocket_endpoint(base_url: str | None, configured_url: Any) -> str:
        if configured_url:
            return str(configured_url).rstrip("/")
        if base_url and "dashscope-intl.aliyuncs.com" in base_url:
            return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
        return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

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

        payload = self._voice_design_payload(
            model=str(metadata.get("model", self.model)),
            target_model=target_model,
            preferred_name=preferred_name,
            voice_prompt=voice_prompt,
            preview_text=preview_text,
            language=language,
            parameters=parameters,
        )

        response = await self._post_json(self.endpoint, payload)

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"DashScope voice design failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(f"DashScope voice design returned non-JSON response: {exc}") from exc

        output = body.get("output") or {}
        voice = output.get("voice") or output.get("voice_id")
        if not voice:
            raise ProviderBadResponseError(f"DashScope voice design response missing output.voice/voice_id: {body}")

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
        if not self.supports_local_voice_clone:
            raise ProviderBadResponseError(
                "CosyVoice voice cloning requires a public audio URL; local preview files cannot be reused as clone sources."
            )

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

        response = await self._post_json(self.endpoint, payload)

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
        if model.startswith("cosyvoice-"):
            return await self._synthesize_cosyvoice(voice=voice, text=text, model=model)

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

        response = await self._post_json(self.generation_endpoint, payload)

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

    async def _synthesize_cosyvoice(self, *, voice: str, text: str, model: str) -> VoiceSynthesisResult:
        try:
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer
        except ImportError as exc:
            raise ProviderBadResponseError(
                "CosyVoice synthesis requires the DashScope Python SDK. "
                "Install project dependencies so the 'dashscope' package is available."
            ) from exc

        def call_sdk() -> tuple[bytes, str | None, dict[str, Any]]:
            dashscope.api_key = self.api_key
            dashscope.base_websocket_api_url = self.websocket_endpoint
            synthesizer = SpeechSynthesizer(model=model, voice=voice)
            audio = synthesizer.call(text)
            if not isinstance(audio, (bytes, bytearray)):
                raise ProviderBadResponseError(f"CosyVoice synthesis returned unexpected audio type: {type(audio)}")

            request_id = None
            if get_request_id := getattr(synthesizer, "get_last_request_id", None):
                request_id = get_request_id()

            usage: dict[str, Any] = {}
            if get_delay := getattr(synthesizer, "get_first_package_delay", None):
                usage["first_package_delay_ms"] = get_delay()

            return bytes(audio), request_id, usage

        try:
            audio_bytes, request_id, usage = await asyncio.to_thread(call_sdk)
        except ProviderBadResponseError:
            raise
        except Exception as exc:
            raise ProviderBadResponseError(f"CosyVoice synthesis failed via DashScope SDK: {exc}") from exc

        return VoiceSynthesisResult(
            provider=self.name,
            model=model,
            voice=voice,
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_format="mp3",
            request_id=request_id,
            usage=usage,
            raw_response={
                "output": {"audio": "<binary audio omitted>"},
                "websocket_endpoint": self.websocket_endpoint,
            },
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
                return await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.ConnectError as exc:
            raise ProviderBadResponseError(
                f"DashScope connection failed before receiving an HTTP response. "
                f"Check network/proxy/TLS settings for {url}: {exc}"
            ) from exc

    def _voice_design_payload(
        self,
        *,
        model: str,
        target_model: str,
        preferred_name: str,
        voice_prompt: str,
        preview_text: str,
        language: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_cosyvoice_design(model, target_model):
            return {
                "model": "voice-enrollment",
                "input": {
                    "action": "create_voice",
                    "target_model": target_model,
                    "prefix": self._cosyvoice_prefix(preferred_name),
                    "voice_prompt": voice_prompt[:500],
                    "preview_text": preview_text[:200],
                    "language_hints": [language],
                },
                "parameters": parameters,
            }

        return {
            "model": model,
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

    @staticmethod
    def _is_cosyvoice_design(model: str, target_model: str) -> bool:
        return model == "voice-enrollment" or target_model.startswith("cosyvoice-")

    @staticmethod
    def _cosyvoice_prefix(preferred_name: str) -> str:
        prefix = "".join(ch for ch in preferred_name if ch.isascii() and ch.isalnum())
        return (prefix or "voice")[:10]

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
