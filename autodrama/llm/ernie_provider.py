"""Baidu ERNIE (文心一言) provider.

Uses the ERNIE Bot REST API with OAuth 2.0 access-token authentication.
"""

from __future__ import annotations

import json
import time

import requests

from autodrama.config.schema import LLMProviderConfig
from autodrama.llm.base import LLMProvider, LLMResponse

# ---------------------------------------------------------------------------
# ERNIE API endpoints
# ---------------------------------------------------------------------------
_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
# Chat endpoint template — model name is part of the path
_CHAT_URL_TEMPLATE = (
    "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}"
)


class ERNIEChat(LLMProvider):
    """Baidu ERNIE Bot chat provider."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config
        # ERNIE requires both api_key (client_id) and secret_key
        self._client_id = config.api_key
        self._secret_key = getattr(config, "secret_key", "")
        self._model = config.model
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # LLMProvider impl
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        token = self._get_access_token()
        url = _CHAT_URL_TEMPLATE.format(model=self._model) + f"?access_token={token}"

        payload: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        payload.update(kwargs)

        resp = requests.post(url, json=payload, timeout=self._config.timeout)
        resp.raise_for_status()
        data = resp.json()

        result_text = data.get("result", "")
        usage_info = data.get("usage", {})
        return LLMResponse(
            content=result_text,
            model=self._model,
            usage={
                "prompt_tokens": usage_info.get("prompt_tokens", 0),
                "completion_tokens": usage_info.get("completion_tokens", 0),
                "total_tokens": usage_info.get("total_tokens", 0),
            },
        )

    def chat_with_structured_output(
        self,
        messages: list[dict[str, str]],
        output_schema: dict,
        *,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        """ERNIE does not natively support structured output.

        We inject a system message that instructs the model to return valid JSON
        matching the schema, then parse it in a retry loop.
        """
        import json as _json

        schema_json = _json.dumps(output_schema, ensure_ascii=False)
        instruction: dict[str, str] = {
            "role": "system",
            "content": (
                f"You MUST respond with a single valid JSON object "
                f"that conforms to the following JSON Schema:\n\n{schema_json}\n\n"
                f"Output ONLY the JSON object — no markdown fences, no extra text."
            ),
        }
        augmented = [instruction] + list(messages)

        # Try up to 2 times — ERNIE may wrap JSON in ``` fences
        for attempt in range(2):
            response = self.chat(augmented, temperature=temperature, **kwargs)
            content = response.content.strip()
            # Strip possible markdown fences
            if content.startswith("```"):
                content = content.strip("`").lstrip("json").strip()
            try:
                parsed = _json.loads(content)
                response.content = _json.dumps(parsed, ensure_ascii=False)
                return response
            except _json.JSONDecodeError:
                if attempt == 1:
                    raise
        return response  # unreachable; placate type-checker

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expiry - 60:
            return self._access_token

        params = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._secret_key,
        }
        resp = requests.get(_TOKEN_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = now + data.get("expires_in", 86400)
        return self._access_token  # type: ignore[return-value]
