from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError
from autodrama.providers.json_utils import parse_json_object

T = TypeVar("T", bound=BaseModel)


class QwenTextProvider:
    """Tongyi Qianwen text provider via DashScope OpenAI-compatible API."""

    name = "qwen"

    def __init__(self, settings: ProviderSettings, runtime: RuntimeSettings) -> None:
        self.settings = settings
        self.runtime = runtime
        self.base_url = (settings.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.model = settings.models.get("text", "qwen-plus")
        self.api_key = settings.secret("api_key_env")
        self.use_response_format = bool(settings.options.get("response_format", True))

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
            raise ProviderAuthError("Missing Qwen/DashScope API key environment variable")

        system_prompt = (
            "你是严格的结构化 JSON 生成引擎。"
            "只返回一个合法 JSON 对象，不要使用 Markdown，不要添加解释。"
        )
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        user_prompt = f"{prompt}\n\n请严格匹配以下 JSON Schema：\n{schema_json}"

        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if self.use_response_format:
            request_body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=self.runtime.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )

        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Qwen request failed with HTTP {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            return schema.model_validate(parsed)
        except Exception as exc:
            raise ProviderBadResponseError(f"Failed to parse Qwen JSON response: {exc}") from exc
