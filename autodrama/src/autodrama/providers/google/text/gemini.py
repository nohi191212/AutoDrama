from __future__ import annotations

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class GeminiTextProvider:
    name = "google"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings, *, model_key: str = "text") -> None:
        self.settings = settings
        self.runtime = runtime
        self.model_key = model_key
        self.model = settings.models.get(model_key) or settings.models.get("text") or "gemini-3.5-flash"
        self.base_url = (settings.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self.api_key = settings.secret("api_key_env")
        self.timeout = float(runtime.request_timeout_seconds)

    def _endpoint(self) -> str:
        base_url = self.base_url
        if base_url.endswith("/v1beta") or base_url.endswith("/v1"):
            return f"{base_url}/models/{self.model}:generateContent"
        return f"{base_url}/v1beta/models/{self.model}:generateContent"

    @classmethod
    def _gemini_json_schema(cls, schema: type[BaseModel]) -> dict[str, Any]:
        return cls._sanitize_schema_node(schema.model_json_schema())

    @classmethod
    def _sanitize_schema_node(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._sanitize_schema_node(item) for item in value]
        if not isinstance(value, dict):
            return value

        sanitized: dict[str, Any] = {}
        for key, raw_value in value.items():
            if key in {"default", "examples", "example", "readOnly", "writeOnly"}:
                continue
            if key == "const":
                sanitized["enum"] = [raw_value]
                continue
            sanitized[key] = cls._sanitize_schema_node(raw_value)
        return sanitized

    def build_payload(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        generation_config: dict[str, Any] = {
            "temperature": float(metadata.get("temperature", temperature)),
            "responseMimeType": "application/json",
            "responseJsonSchema": self._gemini_json_schema(schema),
        }
        max_output_tokens = metadata.get("max_output_tokens") or self.settings.options.get("max_output_tokens")
        if max_output_tokens:
            generation_config["maxOutputTokens"] = int(max_output_tokens)
        thinking_level = metadata.get("thinking_level") or self.settings.options.get("thinking_level")
        if thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": str(thinking_level)}

        return {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ],
            "generationConfig": generation_config,
        }

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderBadResponseError(f"Gemini response has no candidates: {payload}")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderBadResponseError(f"Gemini response candidate has no parts: {payload}")
        texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("text")]
        text = "\n".join(texts).strip()
        if not text:
            raise ProviderBadResponseError(f"Gemini response text is empty: {payload}")
        return text

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        refs: list[AssetRef] | None = None,
    ) -> T:
        if refs:
            raise ProviderBadResponseError("GeminiTextProvider currently supports text-only postgen planning refs")
        if not self.api_key:
            raise ProviderAuthError("Missing Google Gemini API key")

        merged_metadata = {**dict(metadata or {})}
        payload = self.build_payload(prompt, schema, temperature=temperature, metadata=merged_metadata)
        headers = {
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._endpoint(), params={"key": self.api_key}, headers=headers, json=payload)
        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Gemini generateContent failed with HTTP {response.status_code}: {response.text[:2000]}"
            )
        response_payload = response.json()
        content = self._extract_text(response_payload)
        parsed = parse_json_object(content)
        return schema.model_validate(parsed)


__all__ = ["GeminiTextProvider"]
