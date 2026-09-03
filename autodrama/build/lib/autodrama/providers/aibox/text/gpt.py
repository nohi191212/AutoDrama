from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


class AiboxGPTTextProvider(RightCodeTextProvider):
    """AIBOX GPT text provider via the OpenAI-compatible Chat Completions API."""

    name = "aibox"
    RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings, *, model_key: str = "text") -> None:
        super().__init__(settings, runtime, model_key=model_key)

    def _resolve_endpoint(self, base_url: str) -> str:
        value = str(base_url or "https://api.lk888.ai").rstrip("/")
        if value.endswith("/v1/chat/completions"):
            return value
        if value.endswith("/v1"):
            return f"{value}/chat/completions"
        return f"{value}/v1/chat/completions"

    def build_payload(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        refs: list[AssetRef] | None = None,
        repair: bool = False,
        original_content: str | None = None,
        parse_error: Exception | None = None,
    ) -> dict[str, Any]:
        responses_payload = super().build_payload(
            prompt,
            schema,
            temperature=temperature,
            metadata=metadata,
            refs=refs,
            repair=repair,
            original_content=original_content,
            parse_error=parse_error,
        )

        input_messages = responses_payload.get("input") or []
        if not input_messages:
            raise ProviderBadResponseError("AIBOX GPT payload is missing the user message")
        response_message = input_messages[0]
        response_content = response_message.get("content") if isinstance(response_message, dict) else None
        if not isinstance(response_content, list):
            raise ProviderBadResponseError("AIBOX GPT payload is missing user content")

        chat_content: list[dict[str, Any]] = []
        for part in response_content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "input_text":
                chat_content.append({"type": "text", "text": str(part.get("text") or "")})
            elif part_type == "input_image":
                image_url = part.get("image_url")
                if isinstance(image_url, str) and image_url:
                    chat_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "high"},
                        }
                    )

        metadata = metadata or {}
        max_tokens = metadata.get("max_output_tokens") or self.settings.options.get("max_output_tokens") or 32768
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": str(responses_payload.get("instructions") or ""),
                },
                {
                    "role": "user",
                    "content": chat_content,
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": int(max_tokens),
            "stream": False,
        }

    async def _post_responses(
        self,
        payload: dict[str, Any],
        *,
        console_stream: bool = False,
        console_stream_label: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.runtime.request_timeout_seconds, connect=30),
            trust_env=self.httpx_trust_env,
        ) as client:
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
                f"AIBOX GPT chat completion failed with HTTP {response.status_code}: {response.text[:2000]}"
            )
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(
                f"AIBOX GPT chat completion returned non-JSON content: {response.text[:500]}"
            ) from exc
        if not isinstance(response_payload, dict):
            raise ProviderBadResponseError("AIBOX GPT chat completion response must be a JSON object")
        return response_payload


__all__ = ["AiboxGPTTextProvider"]
