from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class GeminiTextProvider:
    name = "google"
    supports_local_refs = True

    def __init__(
        self,
        settings: ProviderSettings,
        runtime: RuntimeSettings,
        *,
        model_key: str = "text",
        provider_name: str = "google",
    ) -> None:
        self.name = provider_name
        self.settings = settings
        self.runtime = runtime
        self.model_key = model_key
        self.model = settings.models.get(model_key) or settings.models.get("text") or "gemini-3.6-flash"
        self.base_url = (settings.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self.api_key = settings.secret("api_key_env")
        self.timeout = float(runtime.request_timeout_seconds)

    def _endpoint(self) -> str:
        base_url = self.base_url
        if base_url.endswith("/v1beta") or base_url.endswith("/v1"):
            return f"{base_url}/models/{self.model}:generateContent"
        return f"{base_url}/v1beta/models/{self.model}:generateContent"

    def _uses_aibox_auth(self) -> bool:
        return self.name in {"aibox", "aibox_gemini"} or "lk888.ai" in self.base_url

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderAuthError("Missing Gemini API key environment variable")
        headers = {"Content-Type": "application/json"}
        if self._uses_aibox_auth():
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def _request_params(self) -> dict[str, str]:
        return {} if self._uses_aibox_auth() else {"key": self.api_key or ""}

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
        refs: list[AssetRef] | None = None,
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

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for ref in refs or []:
            parts.append(self._ref_part(ref))
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": generation_config,
        }

    def _ref_part(self, ref: AssetRef) -> dict[str, Any]:
        if ref.url:
            mime_type = str(ref.metadata.get("mime_type") or mimetypes.guess_type(ref.url)[0] or "application/octet-stream")
            return {"file_data": {"file_uri": ref.url, "mime_type": mime_type}}
        if not ref.path:
            raise ProviderBadResponseError(f"Gemini reference {ref.id or '-'} has neither path nor URL")
        path = Path(ref.path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ProviderBadResponseError(f"Gemini local reference is missing: {path}")
        max_bytes = int(self.settings.options.get("max_inline_media_bytes") or 20 * 1024 * 1024)
        size = path.stat().st_size
        if size > max_bytes:
            raise ProviderBadResponseError(
                f"Gemini local reference is too large for inline upload: {path} ({size} > {max_bytes} bytes)"
            )
        mime_type = str(ref.metadata.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        return {
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
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
        if not self.api_key:
            raise ProviderAuthError("Missing Google Gemini API key")

        merged_metadata = {**dict(metadata or {})}
        payload = self.build_payload(prompt, schema, temperature=temperature, metadata=merged_metadata, refs=refs)
        headers = self._headers()
        request_params = self._request_params()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._endpoint(), params=request_params, headers=headers, json=payload)
        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Gemini generateContent failed with HTTP {response.status_code}: {response.text[:2000]}"
            )
        response_payload = response.json()
        content = self._extract_text(response_payload)
        parsed = parse_json_object(content)
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            repair_prompt = (
                "将下面未通过校验的 JSON 修复为指定结构。只输出修复后的 JSON，不要解释，不要新增事实。\n\n"
                f"JSON Schema:\n{json.dumps(self._gemini_json_schema(schema), ensure_ascii=False)}\n\n"
                f"校验错误:\n{exc}\n\n"
                f"原始 JSON:\n{content[:16000]}"
            )
            repair_payload = self.build_payload(
                repair_prompt,
                schema,
                temperature=0.0,
                metadata={**merged_metadata, "temperature": 0.0},
                refs=None,
            )
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                repair_response = await client.post(
                    self._endpoint(),
                    params=request_params,
                    headers=headers,
                    json=repair_payload,
                )
            if repair_response.status_code >= 400:
                raise ProviderBadResponseError(
                    f"Gemini JSON repair failed with HTTP {repair_response.status_code}: {repair_response.text[:2000]}"
                ) from exc
            repaired_content = self._extract_text(repair_response.json())
            return schema.model_validate(parse_json_object(repaired_content))


__all__ = ["GeminiTextProvider"]
