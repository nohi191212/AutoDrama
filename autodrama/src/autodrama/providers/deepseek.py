from __future__ import annotations

import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class DeepSeekTextProvider:
    name = "deepseek"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://api.deepseek.com").rstrip("/")
        self.model = settings.models.get("text", "deepseek-v4-pro")
        self.api_key = settings.secret("api_key_env") or settings.api_key_env
        self.reasoning_effort = str(settings.options.get("reasoning_effort", "high"))
        self.thinking_enabled = bool(settings.options.get("thinking_enabled", True))

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

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.runtime.request_timeout_seconds,
        )

        extra_body: dict[str, Any] = {}
        if self.thinking_enabled:
            extra_body["thinking"] = {"type": "enabled"}

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
                reasoning_effort=self.reasoning_effort,
                extra_body=extra_body or None,
            )
        except Exception as exc:
            raise ProviderBadResponseError(f"DeepSeek request failed: {exc}") from exc

        try:
            content = response.choices[0].message.content
            if content is None:
                raise ProviderBadResponseError("DeepSeek response content is empty")
            parsed = parse_json_object(content)
            return schema.model_validate(parsed)
        except Exception as exc:
            raise ProviderBadResponseError(f"Failed to parse DeepSeek JSON response: {exc}") from exc
