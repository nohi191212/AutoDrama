from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError

T = TypeVar("T", bound=BaseModel)


class DeepSeekTextProvider:
    name = "deepseek"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.deepseek.com").rstrip("/")
        self.model = settings.models.get("text", "deepseek-chat")
        self.api_key = settings.secret("api_key_env")

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del metadata
        if not self.api_key:
            raise ProviderAuthError("Missing DeepSeek API key environment variable")

        system_prompt = (
            "You are a structured JSON generation engine. "
            "Return only valid JSON that matches the requested schema. "
            "Do not wrap the answer in Markdown."
        )
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        user_prompt = f"{prompt}\n\nRequired JSON schema:\n{schema_json}"

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"DeepSeek request failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return schema.model_validate(parsed)
        except Exception as exc:
            raise ProviderBadResponseError(f"Failed to parse DeepSeek JSON response: {exc}") from exc
