"""Anthropic Claude provider via the Anthropic Python SDK."""

from __future__ import annotations

import json

from autodrama.config.schema import LLMProviderConfig
from autodrama.llm.base import LLMProvider, LLMResponse


class AnthropicChat(LLMProvider):
    """LLM provider backed by Anthropic's Messages API."""

    def __init__(self, config: LLMProviderConfig) -> None:
        import anthropic

        self._config = config
        self._client = anthropic.Anthropic(api_key=config.api_key or None)
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
        system_msg, user_messages = self._convert_messages(messages)

        resp = self._client.messages.create(
            model=self._model,
            system=system_msg or anthropic.NOT_GIVEN,
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        text = ""
        for block in resp.content:
            if block.type == "text":
                text += block.text
        return LLMResponse(
            content=text,
            model=resp.model,
            usage={
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
                "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
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
        """Use Anthropic tool-use to force structured JSON output.

        We define a single tool ``submit_result`` whose input_schema is the
        desired output schema.  The model is required to call it.
        """
        import anthropic

        system_msg, user_messages = self._convert_messages(messages)

        tool = {
            "name": "submit_result",
            "description": output_schema.get("description", "Submit the result"),
            "input_schema": output_schema,
        }

        # First attempt — ask the model to use the tool
        resp = self._client.messages.create(
            model=self._model,
            system=system_msg or anthropic.NOT_GIVEN,
            messages=user_messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_result"},
            temperature=temperature,
            max_tokens=kwargs.pop("max_tokens", 4096),
            **kwargs,
        )

        # Extract tool-use block
        for block in resp.content:
            if block.type == "tool_use":
                return LLMResponse(
                    content=json.dumps(block.input, ensure_ascii=False),
                    model=resp.model,
                    usage={
                        "prompt_tokens": resp.usage.input_tokens,
                        "completion_tokens": resp.usage.output_tokens,
                        "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
                    },
                )

        # Fallback — return text content
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(
            content=text,
            model=resp.model,
            usage={
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
                "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_messages(
        messages: list[dict[str, str]],
    ) -> tuple[str | None, list[dict[str, str]]]:
        """Extract the system message; return (system_text, anthropic_messages)."""
        system_text: list[str] = []
        anthropic_messages: list[dict[str, str]] = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_text.append(content)
            else:
                anthropic_messages.append({"role": role, "content": content})

        return ("\n".join(system_text) if system_text else None, anthropic_messages)
