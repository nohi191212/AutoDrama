"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    """Normalised response from any LLM provider."""

    content: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    # usage keys: prompt_tokens, completion_tokens, total_tokens


class LLMProvider(ABC):
    """Abstract interface for all LLM providers (OpenAI, Anthropic, ERNIE, Qwen, etc.)."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat-completion request.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            temperature: Sampling temperature.
            max_tokens: Max tokens to generate.
            **kwargs: Provider-specific extra parameters.

        Returns:
            LLMResponse with content, model name, and token usage.
        """

    @abstractmethod
    def chat_with_structured_output(
        self,
        messages: list[dict[str, str]],
        output_schema: dict,
        *,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        """Request structured JSON output conforming to *output_schema* (JSON Schema dict).

        The provider should use its native structured-output feature
        (e.g. response_format for OpenAI, tool-use for Anthropic).

        Returns:
            LLMResponse whose ``content`` is a JSON string matching the schema.
        """
