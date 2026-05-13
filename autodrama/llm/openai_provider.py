"""OpenAI chat provider (also works with OpenAI-compatible APIs: Qwen, DeepSeek)."""

from __future__ import annotations

from autodrama.config.schema import LLMProviderConfig
from autodrama.llm.base import LLMProvider, LLMResponse


class OpenAIChat(LLMProvider):
    """LLM provider backed by the OpenAI / OpenAI-compatible API."""

    def __init__(self, config: LLMProviderConfig) -> None:
        import openai

        self._config = config
        client_kwargs: dict = {"api_key": config.api_key or None}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self._client = openai.OpenAI(**client_kwargs)
        self._model = config.model

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                "total_tokens": resp.usage.total_tokens if resp.usage else 0,
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
        # Use the native json_schema response_format (OpenAI >= 1.50).
        # The schema must have "name" at the top level per OpenAI spec.
        schema_with_name = {
            "name": output_schema.get("title", "response"),
            "schema": output_schema,
            "strict": True,
        }
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            response_format={"type": "json_schema", "json_schema": schema_with_name},
            **kwargs,
        )
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                "total_tokens": resp.usage.total_tokens if resp.usage else 0,
            },
        )
