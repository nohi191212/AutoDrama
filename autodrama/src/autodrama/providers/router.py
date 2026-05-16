from __future__ import annotations

from autodrama.config import Settings
from autodrama.providers.base import TextLLM
from autodrama.providers.deepseek import DeepSeekTextProvider
from autodrama.providers.fake import FakeTextProvider


class ProviderRouter:
    def __init__(self, settings: Settings, provider_override: str | None = None) -> None:
        self.settings = settings
        self.provider_override = provider_override
        self._fake = FakeTextProvider()

    def text(self, purpose: str) -> TextLLM:
        provider_name = self.provider_override or self.settings.provider_for("text", purpose)
        if provider_name == "fake":
            return self._fake
        if provider_name == "deepseek":
            return DeepSeekTextProvider(self.settings.providers["deepseek"], self.settings.runtime)
        raise ValueError(f"Unsupported text provider: {provider_name}")
