"""LLM provider factory — registry-based instantiation."""

from __future__ import annotations

from autodrama.config.schema import LLMProviderConfig
from autodrama.llm.base import LLMProvider


class LLMFactory:
    """Create LLM provider instances by name.

    New providers are registered via ``LLMFactory.register("name", Class)``.
    """

    _registry: dict[str, type[LLMProvider]] = {}

    # ------------------------------------------------------------------
    # Registry API
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, name: str, provider_class: type[LLMProvider]) -> None:
        """Register a provider class under *name*."""
        cls._registry[name] = provider_class

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered provider names."""
        return sorted(cls._registry)

    # ------------------------------------------------------------------
    # Factory API
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, provider_name: str, config: LLMProviderConfig) -> LLMProvider:
        """Instantiate a registered provider.

        Raises:
            KeyError: if *provider_name* is not registered.
        """
        if provider_name not in cls._registry:
            raise KeyError(
                f"Unknown LLM provider '{provider_name}'. "
                f"Registered: {cls.list_providers()}"
            )
        return cls._registry[provider_name](config)

    @classmethod
    def create_default(cls, app_config: object) -> LLMProvider:
        """Create the default provider as configured in the AppConfig."""
        from autodrama.config.schema import AppConfig

        ac: AppConfig = app_config  # type: ignore[assignment]
        default = ac.llm.default_provider
        return cls.create(default, ac.llm.providers[default])


# ---------------------------------------------------------------------------
# Auto-register built-in providers
# ---------------------------------------------------------------------------
def _register_builtins() -> None:
    from autodrama.llm.openai_provider import OpenAIChat
    from autodrama.llm.anthropic_provider import AnthropicChat
    from autodrama.llm.ernie_provider import ERNIEChat

    LLMFactory.register("openai", OpenAIChat)
    LLMFactory.register("anthropic", AnthropicChat)
    LLMFactory.register("ernie", ERNIEChat)
    # Qwen and DeepSeek use the OpenAI-compatible class
    LLMFactory.register("qwen", OpenAIChat)
    LLMFactory.register("deepseek", OpenAIChat)


_register_builtins()
