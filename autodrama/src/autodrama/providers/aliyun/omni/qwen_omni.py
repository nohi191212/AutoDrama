from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class QwenOmniAudioJudgeProvider:
    """DashScope Qwen Omni audio judge via OpenAI-compatible chat completions."""

    name = "aliyun_omni"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        if not self.base_url.endswith("/compatible-mode/v1"):
            self.base_url = f"{self.base_url}/compatible-mode/v1"
        self.model = (
            settings.models.get("audio_judge")
            or settings.models.get("omni")
            or settings.models.get("text")
            or "qwen3.5-omni-plus"
        )
        self.api_key = settings.secret("api_key_env")
        self.use_response_format = bool(settings.options.get("omni_response_format", False))

    @staticmethod
    def _audio_format(path: str | Path | None, metadata: dict[str, Any] | None = None) -> str:
        metadata = metadata or {}
        explicit = metadata.get("format") or metadata.get("audio_format") or metadata.get("response_format")
        if explicit:
            return str(explicit).lower().lstrip(".")
        suffix = Path(str(path or "")).suffix.lower().lstrip(".")
        if suffix in {"mp3", "wav", "m4a", "aac", "ogg", "opus", "pcm"}:
            return suffix
        return "mp3"

    @staticmethod
    def _audio_content(ref: AssetRef) -> dict[str, Any]:
        if ref.url:
            audio_format = QwenOmniAudioJudgeProvider._audio_format(ref.url, ref.metadata)
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": ref.url,
                    "format": audio_format,
                },
            }
        if not ref.path:
            raise ProviderBadResponseError(f"Audio ref {ref.id or '-'} is missing local path or URL")
        path = Path(ref.path)
        if not path.exists() or not path.is_file():
            raise ProviderBadResponseError(f"Audio ref {ref.id or '-'} file is missing: {path}")
        audio_format = QwenOmniAudioJudgeProvider._audio_format(path, ref.metadata)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "input_audio",
            "input_audio": {
                "data": f"data:;base64,{data}",
                "format": audio_format,
            },
        }

    @classmethod
    def _content_parts(cls, prompt: str, refs: list[AssetRef]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for ref in refs:
            if ref.type != "audio":
                continue
            parts.append(cls._audio_content(ref))
        parts.append({"type": "text", "text": prompt})
        return parts

    @staticmethod
    def _delta_text(delta: Any) -> str:
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        texts.append(value)
            return "".join(texts)
        return ""

    @staticmethod
    def _message_text(message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        texts.append(value)
            return "".join(texts)
        return ""

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
        for message in sanitized.get("messages", []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "input_audio":
                    input_audio = part.get("input_audio")
                    if isinstance(input_audio, dict) and "data" in input_audio:
                        input_audio["data"] = "<base64 audio omitted>"
        return sanitized

    async def judge_audio_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        refs: list[AssetRef],
        temperature: float = 0.2,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        metadata = metadata or {}
        if not self.api_key:
            raise ProviderAuthError("Missing DashScope API key for Qwen Omni audio judge")

        system_prompt = (
            "你是严格的结构化 JSON 音频评审引擎。"
            "只返回一个合法 JSON 对象，不要使用 Markdown，不要添加解释。"
        )
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        user_prompt = f"{prompt}\n\n请严格匹配以下 JSON Schema：\n{schema_json}"
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._content_parts(user_prompt, refs)},
            ],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "modalities": ["text"],
        }
        if self.use_response_format:
            request_body["response_format"] = {"type": "json_object"}

        chunks: list[str] = []
        raw_events: list[dict[str, Any]] = []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=request_body,
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderBadResponseError(
                            f"Qwen Omni request failed with HTTP {response.status_code}: "
                            f"{body.decode('utf-8', errors='replace')[:500]}"
                        )
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line or line == "[DONE]":
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        raw_events.append(event)
                        choices = event.get("choices")
                        if not isinstance(choices, list):
                            continue
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            chunks.append(self._delta_text(choice.get("delta")))
                            chunks.append(self._message_text(choice.get("message")))
            except httpx.ConnectError as exc:
                raise ProviderBadResponseError(
                    f"Qwen Omni connection failed before receiving an HTTP response. "
                    f"Check network/proxy/TLS settings for {self.base_url}: {exc}"
                ) from exc

        content = "".join(chunks).strip()
        if not content:
            raise ProviderBadResponseError("Qwen Omni audio judge returned empty content")
        try:
            parsed = parse_json_object(content)
            return schema.model_validate(parsed)
        except Exception as exc:
            raise ProviderBadResponseError(
                f"Failed to parse Qwen Omni JSON response: {exc}; "
                f"metadata={metadata}; raw_request={self._safe_payload(request_body)}; raw_events={raw_events[-3:]}"
            ) from exc
