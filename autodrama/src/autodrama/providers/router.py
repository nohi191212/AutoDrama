from __future__ import annotations

from autodrama.config import Settings
from autodrama.providers.base import TextLLM, VideoGenerator
from autodrama.providers.deepseek import DeepSeekTextProvider
from autodrama.providers.fake import FakeTextProvider, FakeVideoProvider
from autodrama.providers.qwen import QwenTextProvider
from autodrama.providers.wanxiang import WanxiangVideoProvider


class ProviderRouter:
    def __init__(self, settings: Settings, provider_override: str | None = None) -> None:
        self.settings = settings
        self.provider_override = provider_override
        self._fake = FakeTextProvider()
        self._fake_video = FakeVideoProvider()

    def text(self, purpose: str) -> TextLLM:
        provider_name = self.provider_override or self.settings.provider_for("text", purpose)
        if provider_name == "fake":
            return self._fake
        if provider_name == "deepseek":
            return DeepSeekTextProvider(self.settings.providers["deepseek"], self.settings.runtime)
        if provider_name == "qwen":
            return QwenTextProvider(self.settings.providers["qwen"], self.settings.runtime)
        raise ValueError(f"Unsupported text provider: {provider_name}")

    def video(self, purpose: str) -> VideoGenerator:
        provider_name = self.provider_override or self.settings.provider_for("video", purpose)
        if provider_name == "fake":
            return self._fake_video
        if provider_name == "wanxiang":
            return WanxiangVideoProvider(self.settings.providers["wanxiang"], self.settings.runtime)
        raise ValueError(f"Unsupported video provider: {provider_name}")
