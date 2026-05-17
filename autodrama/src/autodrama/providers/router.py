from __future__ import annotations

from autodrama.config import Settings
from autodrama.providers.aliyun.audio.qwen_tts import QwenVoiceDesignProvider
from autodrama.providers.aliyun.image.wanxiang import WanxiangImageProvider
from autodrama.providers.aliyun.music.fun_music import BailianMusicProvider
from autodrama.providers.aliyun.text.qwen import QwenTextProvider
from autodrama.providers.aliyun.video.wanxiang import WanxiangVideoProvider
from autodrama.providers.base import ImageGenerator, MusicGenerator, TextLLM, VideoGenerator, VoiceDesigner
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider
from autodrama.providers.local.mock.fake import (
    FakeImageProvider,
    FakeMusicProvider,
    FakeTextProvider,
    FakeVideoProvider,
    FakeVoiceDesignProvider,
)
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider
from autodrama.providers.volcengine.audio.seed_icl import VolcengineVoiceProvider


ALIYUN_TEXT_PROVIDER_NAMES = {"aliyun", "qwen", "bailian"}
ALIYUN_IMAGE_PROVIDER_NAMES = {"aliyun", "wanxiang"}
ALIYUN_VIDEO_PROVIDER_NAMES = {"aliyun", "wanxiang"}
ALIYUN_AUDIO_PROVIDER_NAMES = {"aliyun", "qwen_tts"}
ALIYUN_MUSIC_PROVIDER_NAMES = {"aliyun", "bailian"}


class ProviderRouter:
    def __init__(self, settings: Settings, provider_override: str | None = None) -> None:
        self.settings = settings
        self.provider_override = provider_override
        self._fake = FakeTextProvider()
        self._fake_image = FakeImageProvider()
        self._fake_music = FakeMusicProvider()
        self._fake_video = FakeVideoProvider()
        self._fake_voice = FakeVoiceDesignProvider()

    def _settings_for(self, provider_name: str):
        if provider_name == "aliyun":
            return self.settings.providers["aliyun"]
        return self.settings.providers[provider_name]

    def _aliyun_settings(self, *, base_url: str | None = None):
        settings = self.settings.providers["aliyun"].model_copy(deep=True)
        if base_url is not None:
            settings.base_url = base_url
        return settings

    def _openai_compatible_settings(self, provider_name: str):
        if provider_name == "aliyun":
            base_url = (self.settings.providers["aliyun"].base_url or "https://dashscope.aliyuncs.com").rstrip("/")
            if not base_url.endswith("/compatible-mode/v1"):
                base_url = f"{base_url}/compatible-mode/v1"
            return self._aliyun_settings(base_url=base_url)

        settings = self._settings_for(provider_name).model_copy(deep=True)
        if provider_name == "bailian":
            base_url = (settings.base_url or "https://dashscope.aliyuncs.com").rstrip("/")
            if not base_url.endswith("/compatible-mode/v1"):
                base_url = f"{base_url}/compatible-mode/v1"
            settings.base_url = base_url
        return settings

    def _dashscope_api_v1_settings(self, provider_name: str):
        if provider_name == "aliyun":
            base_url = (self.settings.providers["aliyun"].base_url or "https://dashscope.aliyuncs.com").rstrip("/")
            if not base_url.endswith("/api/v1"):
                base_url = f"{base_url}/api/v1"
            return self._aliyun_settings(base_url=base_url)
        return self._settings_for(provider_name)

    def _dashscope_root_settings(self, provider_name: str):
        if provider_name == "aliyun":
            base_url = (self.settings.providers["aliyun"].base_url or "https://dashscope.aliyuncs.com").rstrip("/")
            return self._aliyun_settings(base_url=base_url)
        return self._settings_for(provider_name)

    def text(self, purpose: str) -> TextLLM:
        provider_name = self.provider_override or self.settings.provider_for("text", purpose)
        if provider_name == "fake":
            return self._fake
        if provider_name == "deepseek":
            return DeepSeekTextProvider(self.settings.providers["deepseek"], self.settings.runtime)
        if provider_name in ALIYUN_TEXT_PROVIDER_NAMES:
            return QwenTextProvider(self._openai_compatible_settings(provider_name), self.settings.runtime)
        raise ValueError(f"Unsupported text provider: {provider_name}")

    def image(self, purpose: str) -> ImageGenerator:
        provider_name = self.provider_override or self.settings.provider_for("image", purpose)
        if provider_name == "fake":
            return self._fake_image
        if provider_name == "rightcode":
            return RightCodeImageProvider(self._settings_for(provider_name), self.settings.runtime)
        if provider_name in ALIYUN_IMAGE_PROVIDER_NAMES:
            return WanxiangImageProvider(self._dashscope_api_v1_settings(provider_name), self.settings.runtime)
        raise ValueError(f"Unsupported image provider: {provider_name}")

    def video(self, purpose: str) -> VideoGenerator:
        provider_name = self.provider_override or self.settings.provider_for("video", purpose)
        if provider_name == "fake":
            return self._fake_video
        if provider_name in ALIYUN_VIDEO_PROVIDER_NAMES:
            return WanxiangVideoProvider(self._dashscope_api_v1_settings(provider_name), self.settings.runtime)
        raise ValueError(f"Unsupported video provider: {provider_name}")

    def audio(self, purpose: str) -> VoiceDesigner:
        provider_name = self.provider_override or self.settings.provider_for("audio", purpose)
        if provider_name == "fake":
            return self._fake_voice
        if provider_name == "volcengine":
            return VolcengineVoiceProvider(self._settings_for(provider_name), self.settings.runtime)
        if provider_name in ALIYUN_AUDIO_PROVIDER_NAMES:
            return QwenVoiceDesignProvider(self._dashscope_api_v1_settings(provider_name), self.settings.runtime)
        raise ValueError(f"Unsupported audio provider: {provider_name}")

    def music(self, purpose: str) -> MusicGenerator:
        provider_name = self.provider_override
        if not provider_name:
            try:
                provider_name = self.settings.provider_for("music", purpose)
            except KeyError:
                provider_name = self.settings.provider_for("audio", "music")

        if provider_name == "fake":
            return self._fake_music
        if provider_name in ALIYUN_MUSIC_PROVIDER_NAMES:
            return BailianMusicProvider(self._dashscope_root_settings(provider_name), self.settings.runtime)
        raise ValueError(f"Unsupported music provider: {provider_name}")
