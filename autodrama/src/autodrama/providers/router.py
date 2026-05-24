from __future__ import annotations

from autodrama.config import Settings
from autodrama.providers.aliyun.audio.qwen_tts import QwenVoiceDesignProvider
from autodrama.providers.aliyun.image.wanxiang import WanxiangImageProvider
from autodrama.providers.aliyun.music.fun_music import BailianMusicProvider
from autodrama.providers.aliyun.omni.qwen_omni import QwenOmniAudioJudgeProvider
from autodrama.providers.aliyun.text.qwen import QwenTextProvider
from autodrama.providers.aliyun.video.wanxiang import WanxiangVideoProvider
from autodrama.providers.base import (
    AudioJudgeLLM,
    ImageGenerator,
    MusicGenerator,
    SpeechSynthesizer,
    TextLLM,
    VideoGenerator,
    VoiceDesigner,
)
from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider
from autodrama.providers.elevenlabs.music.compose import ElevenLabsMusicProvider
from autodrama.providers.local.mock.fake import (
    FakeAudioJudgeProvider,
    FakeImageProvider,
    FakeMusicProvider,
    FakeTextProvider,
    FakeVideoProvider,
    FakeVoiceDesignProvider,
)
from autodrama.providers.minimax.music.music_26 import MiniMaxMusicProvider
from autodrama.providers.registry import ProviderRegistry
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider
from autodrama.providers.volcengine.audio.seed_icl import VolcengineVoiceProvider
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider


ALIYUN_TEXT_PROVIDER_NAMES = {"aliyun", "qwen", "bailian"}
ALIYUN_IMAGE_PROVIDER_NAMES = {"aliyun", "wanxiang"}
ALIYUN_VIDEO_PROVIDER_NAMES = {"aliyun", "wanxiang"}
ALIYUN_AUDIO_PROVIDER_NAMES = {"aliyun", "qwen_tts"}
ALIYUN_OMNI_PROVIDER_NAMES = {"aliyun_omni", "qwen_omni", "dashscope_omni"}
ALIYUN_MUSIC_PROVIDER_NAMES = {"aliyun", "bailian"}
MINIMAX_MUSIC_PROVIDER_NAMES = {"minimax", "minimax_music"}
ELEVENLABS_MUSIC_PROVIDER_NAMES = {"elevenlabs", "elevenlabs_music"}
VOLCENGINE_IMAGE_PROVIDER_NAMES = {"volcengine", "seedream", "volcengine_seedream"}


class ProviderRouter:
    def __init__(self, settings: Settings, provider_override: str | None = None) -> None:
        self.settings = settings
        self.provider_override = provider_override
        self._fake = FakeTextProvider()
        self._fake_image = FakeImageProvider()
        self._fake_music = FakeMusicProvider()
        self._fake_video = FakeVideoProvider()
        self._fake_voice = FakeVoiceDesignProvider()
        self._fake_judge = FakeAudioJudgeProvider()
        self.registry = ProviderRegistry()
        self._register_provider_factories()

    def _register_provider_factories(self) -> None:
        self.registry.register("text", {"fake"}, lambda **_: self._fake)
        self.registry.register(
            "text",
            {"deepseek"},
            lambda **_: DeepSeekTextProvider(self.settings.providers["deepseek"], self.settings.runtime),
        )
        self.registry.register(
            "text",
            ALIYUN_TEXT_PROVIDER_NAMES,
            lambda provider_name, **_: QwenTextProvider(
                self._openai_compatible_settings(provider_name),
                self.settings.runtime,
            ),
        )

        self.registry.register("image", {"fake"}, lambda **_: self._fake_image)
        self.registry.register(
            "image",
            {"rightcode"},
            lambda provider_name, **_: RightCodeImageProvider(
                self._settings_for(provider_name),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "image",
            {"toapi", "toapis"},
            lambda provider_name, **_: ToAPIImageProvider(
                self._settings_for("toapi" if provider_name == "toapis" else provider_name),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "image",
            VOLCENGINE_IMAGE_PROVIDER_NAMES,
            lambda **_: VolcengineSeedreamImageProvider(
                self._settings_for("volcengine"),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "image",
            ALIYUN_IMAGE_PROVIDER_NAMES,
            lambda provider_name, **_: WanxiangImageProvider(
                self._dashscope_api_v1_settings(provider_name),
                self.settings.runtime,
            ),
        )

        self.registry.register("video", {"fake"}, lambda **_: self._fake_video)
        self.registry.register(
            "video",
            {"volcengine", "seedance", "volcengine_seedance"},
            lambda **_: VolcengineSeedanceVideoProvider(
                self._settings_for("volcengine"),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "video",
            ALIYUN_VIDEO_PROVIDER_NAMES,
            lambda provider_name, **_: WanxiangVideoProvider(
                self._dashscope_api_v1_settings(provider_name),
                self.settings.runtime,
            ),
        )

        self.registry.register("audio", {"fake"}, lambda **_: self._fake_voice)
        self.registry.register(
            "audio",
            {"volcengine"},
            lambda purpose, provider_name, **_: (
                VolcengineVoiceProvider(self._settings_for(provider_name), self.settings.runtime)
                if purpose in {"voice_design", "voice_clone", "seed_icl"}
                else VolcengineSeedTTSProvider(self._settings_for(provider_name), self.settings.runtime)
            ),
        )
        self.registry.register(
            "audio",
            {"volcengine_icl"},
            lambda provider_name, **_: VolcengineVoiceProvider(
                self._settings_for(provider_name),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "audio",
            ALIYUN_AUDIO_PROVIDER_NAMES,
            lambda provider_name, **_: QwenVoiceDesignProvider(
                self._dashscope_api_v1_settings(provider_name),
                self.settings.runtime,
            ),
        )

        self.registry.register("judge", {"fake"}, lambda **_: self._fake_judge)
        self.registry.register(
            "judge",
            ALIYUN_OMNI_PROVIDER_NAMES,
            lambda provider_name, **_: QwenOmniAudioJudgeProvider(
                self._aliyun_omni_settings(provider_name),
                self.settings.runtime,
            ),
        )
        self.registry.register(
            "judge",
            {"aliyun"},
            lambda provider_name, **_: QwenOmniAudioJudgeProvider(
                self._aliyun_omni_settings(provider_name),
                self.settings.runtime,
            ),
        )

        self.registry.register("music", {"fake"}, lambda **_: self._fake_music)
        self.registry.register(
            "music",
            MINIMAX_MUSIC_PROVIDER_NAMES,
            lambda **_: MiniMaxMusicProvider(self._settings_for("minimax"), self.settings.runtime),
        )
        self.registry.register(
            "music",
            ELEVENLABS_MUSIC_PROVIDER_NAMES,
            lambda **_: ElevenLabsMusicProvider(self._settings_for("elevenlabs"), self.settings.runtime),
        )
        self.registry.register(
            "music",
            ALIYUN_MUSIC_PROVIDER_NAMES,
            lambda provider_name, **_: BailianMusicProvider(
                self._dashscope_root_settings(provider_name),
                self.settings.runtime,
            ),
        )

    def _provider_from_registry(self, capability: str, provider_name: str, purpose: str):
        factory = self.registry.factory_for(capability, provider_name)
        if factory is None:
            raise ValueError(f"Unsupported {capability} provider: {provider_name}")
        return factory(provider_name=provider_name, purpose=purpose)

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

    def _aliyun_omni_settings(self, provider_name: str):
        if provider_name in self.settings.providers:
            settings = self.settings.providers[provider_name].model_copy(deep=True)
        elif "aliyun_omni" in self.settings.providers:
            settings = self.settings.providers["aliyun_omni"].model_copy(deep=True)
        else:
            settings = self.settings.providers["aliyun"].model_copy(deep=True)
        settings.models = dict(settings.models)
        settings.models.setdefault("audio_judge", "qwen3.5-omni-plus")

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
        return self._provider_from_registry("text", provider_name, purpose)

    def image(self, purpose: str) -> ImageGenerator:
        provider_name = self.provider_override or self.settings.provider_for("image", purpose)
        return self._provider_from_registry("image", provider_name, purpose)

    def video(self, purpose: str) -> VideoGenerator:
        provider_name = self.provider_override or self.settings.provider_for("video", purpose)
        return self._provider_from_registry("video", provider_name, purpose)

    def audio(self, purpose: str) -> VoiceDesigner | SpeechSynthesizer:
        provider_name = self.provider_override or self.settings.provider_for("audio", purpose)
        return self._provider_from_registry("audio", provider_name, purpose)

    def judge(self, purpose: str) -> AudioJudgeLLM:
        provider_name = self.provider_override
        if not provider_name:
            try:
                provider_name = self.settings.provider_for("judge", purpose)
            except KeyError:
                provider_name = "aliyun_omni"
        return self._provider_from_registry("judge", provider_name, purpose)

    def music(self, purpose: str) -> MusicGenerator:
        provider_name = self.provider_override
        if not provider_name:
            try:
                provider_name = self.settings.provider_for("music", purpose)
            except KeyError:
                provider_name = self.settings.provider_for("audio", "music")

        return self._provider_from_registry("music", provider_name, purpose)
