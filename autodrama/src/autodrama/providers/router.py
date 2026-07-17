from __future__ import annotations

from typing import Any

from autodrama.config import Settings
from autodrama.core.errors import ProviderBadResponseError
from autodrama.core.model_catalog import ModelBinding, ModelCapability
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider
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
from autodrama.providers.google.text.gemini import GeminiTextProvider
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider
from autodrama.providers.local.mock.fake import (
    FakeAudioJudgeProvider,
    FakeImageProvider,
    FakeMusicProvider,
    FakeTextProvider,
    FakeVideoProvider,
    FakeVoiceDesignProvider,
)
from autodrama.providers.media_refs import is_remote_url_expired, local_ref_path
from autodrama.providers.minimax.music.music_26 import MiniMaxMusicProvider
from autodrama.providers.registry import ProviderRegistry
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider
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
KLING_VIDEO_PROVIDER_NAMES = {"kling", "kling_omni", "kling_video"}
GOOGLE_TEXT_PROVIDER_NAMES = {"google", "gemini"}


class BoundProviderProxy:
    def __init__(
        self,
        provider: Any,
        binding: ModelBinding,
        *,
        reference_image_uploader: ToAPIImageProvider | None = None,
    ) -> None:
        self._provider = provider
        self._reference_image_uploader = reference_image_uploader
        self.model_binding = binding
        self.name = getattr(provider, "name", binding.provider)
        self.model = binding.provider_model_name
        self._apply_provider_model()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def _apply_provider_model(self) -> None:
        for attr in ("model",):
            if hasattr(self._provider, attr):
                try:
                    setattr(self._provider, attr, self.model)
                except Exception:
                    pass
        for key, value in self.model_binding.params.items():
            if hasattr(self._provider, key):
                try:
                    setattr(self._provider, key, value)
                except Exception:
                    pass
        if "response_format" in self.model_binding.params and hasattr(self._provider, "use_response_format"):
            response_format = self.model_binding.params["response_format"]
            try:
                use_response_format = str(response_format).strip().lower() in {"1", "true", "json_object"}
                setattr(self._provider, "use_response_format", use_response_format)
            except Exception:
                pass
        refresh_endpoint = getattr(self._provider, "refresh_endpoint", None)
        if callable(refresh_endpoint):
            try:
                refresh_endpoint()
            except Exception:
                pass

    def _metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(metadata or {})
        merged.update(self.model_binding.params)
        merged.setdefault("node_name", self.model_binding.node_name)
        merged["model"] = self.model_binding.provider_model_name
        return merged

    def _validate_media_request(
        self,
        *,
        refs: list[Any] | None = None,
        duration: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        context = f"node {self.model_binding.node_name} model {self.model_binding.model_id}"
        self.model_binding.spec.validate_refs(refs, context=context)
        effective_duration = duration
        if effective_duration is None and metadata is not None:
            effective_duration = metadata.get("duration", metadata.get("duration_seconds"))
        self.model_binding.spec.validate_duration(effective_duration, context=context)

    async def _refresh_expired_reference_images(self, refs: list[Any] | None) -> None:
        targets = [
            ref
            for ref in refs or []
            if str(getattr(ref, "type", "") or "") == "image"
            and bool(getattr(ref, "url", None))
            and is_remote_url_expired(str(ref.url))
            and local_ref_path(ref) is not None
        ]
        if not targets:
            return
        if self._reference_image_uploader is None:
            raise ProviderBadResponseError(
                f"node {self.model_binding.node_name} has expired reference image URL(s), "
                "but the shared ToAPI reference uploader is not configured"
            )
        await self._reference_image_uploader.reupload_expired_reference_images(targets)

    async def generate_json(self, prompt, schema, *, temperature: float = 0.7, metadata=None, refs=None):
        temperature = self.model_binding.params.get("temperature", temperature)
        merged_metadata = self._metadata(metadata)
        if refs:
            await self._refresh_expired_reference_images(refs)
            self._validate_media_request(refs=refs, metadata=merged_metadata)
        return await self._provider.generate_json(
            prompt,
            schema,
            temperature=temperature,
            metadata=merged_metadata,
            refs=refs,
        )

    async def judge_audio_json(self, prompt, schema, *, refs, temperature: float = 0.2, metadata=None):
        temperature = self.model_binding.params.get("temperature", temperature)
        await self._refresh_expired_reference_images(refs)
        self._validate_media_request(refs=refs, metadata=metadata)
        return await self._provider.judge_audio_json(
            prompt,
            schema,
            refs=refs,
            temperature=temperature,
            metadata=self._metadata(metadata),
        )

    async def generate_image(self, prompt, refs=None, *, size=None, metadata=None):
        merged_metadata = self._metadata(metadata)
        await self._refresh_expired_reference_images(refs)
        self._validate_media_request(refs=refs, metadata=merged_metadata)
        return await self._provider.generate_image(
            prompt,
            refs=refs,
            size=size,
            metadata=merged_metadata,
        )

    async def generate_music(self, prompt, *, lyrics=None, metadata=None):
        merged_metadata = self._metadata(metadata)
        self._validate_media_request(metadata=merged_metadata)
        return await self._provider.generate_music(prompt, lyrics=lyrics, metadata=merged_metadata)

    async def submit_video(self, prompt, refs=None, *, duration=None, metadata=None):
        merged_metadata = self._metadata(metadata)
        await self._refresh_expired_reference_images(refs)
        self._validate_media_request(refs=refs, duration=duration, metadata=merged_metadata)
        return await self._provider.submit_video(prompt, refs=refs, duration=duration, metadata=merged_metadata)

    async def query_video_task(self, task_id):
        return await self._provider.query_video_task(task_id)

    async def generate_video(self, prompt, refs=None, *, duration=None, wait: bool = False, metadata=None):
        merged_metadata = self._metadata(metadata)
        await self._refresh_expired_reference_images(refs)
        self._validate_media_request(refs=refs, duration=duration, metadata=merged_metadata)
        return await self._provider.generate_video(
            prompt,
            refs=refs,
            duration=duration,
            wait=wait,
            metadata=merged_metadata,
        )

    async def create_subject_element(
        self,
        *,
        element_name,
        element_description,
        reference_type,
        video_url=None,
        image_refs=None,
        metadata=None,
    ):
        await self._refresh_expired_reference_images(image_refs)
        return await self._provider.create_subject_element(
            element_name=element_name,
            element_description=element_description,
            reference_type=reference_type,
            video_url=video_url,
            image_refs=image_refs,
            metadata=self._metadata(metadata),
        )

    async def query_subject_element_task(self, task_id):
        return await self._provider.query_subject_element_task(task_id)

    async def query_subject_element(self, *, task_id=None, external_task_id=None):
        if hasattr(self._provider, "query_subject_element"):
            return await self._provider.query_subject_element(task_id=task_id, external_task_id=external_task_id)
        query_id = external_task_id or task_id
        if query_id is None:
            raise ValueError("Subject element query requires task_id or external_task_id")
        return await self._provider.query_subject_element_task(query_id)

    async def generate_subject_element(
        self,
        *,
        element_name,
        element_description,
        reference_type,
        video_url=None,
        image_refs=None,
        wait: bool = True,
        metadata=None,
    ):
        await self._refresh_expired_reference_images(image_refs)
        return await self._provider.generate_subject_element(
            element_name=element_name,
            element_description=element_description,
            reference_type=reference_type,
            video_url=video_url,
            image_refs=image_refs,
            wait=wait,
            metadata=self._metadata(metadata),
        )

    async def create_voice(self, *, voice_prompt, preview_text, preferred_name, metadata=None):
        return await self._provider.create_voice(
            voice_prompt=voice_prompt,
            preview_text=preview_text,
            preferred_name=preferred_name,
            metadata=self._metadata(metadata),
        )

    async def clone_voice_from_audio(self, *, source_audio_path, preferred_name, metadata=None):
        return await self._provider.clone_voice_from_audio(
            source_audio_path=source_audio_path,
            preferred_name=preferred_name,
            metadata=self._metadata(metadata),
        )

    async def synthesize_speech(self, *, voice, text, metadata=None):
        return await self._provider.synthesize_speech(
            voice=voice,
            text=text,
            metadata=self._metadata(metadata),
        )


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
        toapi_settings = self.settings.providers.get("toapi")
        self._reference_image_uploader = (
            ToAPIImageProvider(toapi_settings, self.settings.runtime)
            if toapi_settings is not None
            else None
        )
        self.registry = ProviderRegistry()
        self._register_provider_factories()

    def _register_provider_factories(self) -> None:
        self.registry.register("text", {"fake"}, lambda **_: self._fake)
        self.registry.register(
            "text",
            {"deepseek"},
            lambda purpose, **_: DeepSeekTextProvider(
                self.settings.providers["deepseek"],
                self.settings.runtime,
                model_key=purpose,
            ),
        )
        self.registry.register(
            "text",
            ALIYUN_TEXT_PROVIDER_NAMES,
            lambda provider_name, purpose, **_: QwenTextProvider(
                self._openai_compatible_settings(provider_name),
                self.settings.runtime,
                model_key=purpose,
            ),
        )
        self.registry.register(
            "text",
            {"rightcode"},
            lambda provider_name, purpose, **_: RightCodeTextProvider(
                self._settings_for(provider_name),
                self.settings.runtime,
                model_key=purpose,
            ),
        )
        self.registry.register(
            "text",
            GOOGLE_TEXT_PROVIDER_NAMES,
            lambda provider_name, purpose, **_: GeminiTextProvider(
                self._settings_for("google" if provider_name == "gemini" else provider_name),
                self.settings.runtime,
                model_key=purpose,
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
            {"aibox", "ai_box"},
            lambda provider_name, **_: AiboxImageProvider(
                self._settings_for("aibox" if provider_name == "ai_box" else provider_name),
                self.settings.runtime,
                reference_uploader_settings=self.settings.providers.get("toapi"),
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
            KLING_VIDEO_PROVIDER_NAMES,
            lambda **_: KlingOmniVideoProvider(
                self._settings_for("kling"),
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

    def _provider_from_registry(
        self,
        capability: str,
        provider_name: str,
        purpose: str,
        *,
        binding: ModelBinding | None = None,
    ):
        factory = self.registry.factory_for(capability, provider_name)
        if factory is None:
            raise ValueError(f"Unsupported {capability} provider: {provider_name}")
        provider = factory(provider_name=provider_name, purpose=purpose)
        if isinstance(provider, RightCodeTextProvider):
            provider.reference_image_uploader = self._reference_image_uploader
        if binding is None:
            return provider
        return BoundProviderProxy(
            provider,
            binding,
            reference_image_uploader=self._reference_image_uploader,
        )

    def _binding_for_node(self, capability: ModelCapability, node_name: str | None) -> ModelBinding | None:
        if self.provider_override or not node_name:
            return None
        node_settings = self.settings.nodes.get(node_name)
        if node_settings is None:
            return None
        spec = self.settings.model_catalog.validate_node_settings(node_name, node_settings)
        if spec.capability != capability:
            raise ValueError(
                f"nodes.{node_name}.model {node_settings.model} has capability {spec.capability!r}; "
                f"expected {capability!r}"
            )
        provider = spec.provider_name
        if not provider:
            raise ValueError(f"Model catalog entry {spec.id} must declare or imply a provider")
        return ModelBinding(
            node_name=node_name,
            model_id=node_settings.model,
            provider=provider,
            capability=capability,
            params=dict(node_settings.params),
            spec=spec,
        )

    def _provider_name_for(
        self,
        capability: ModelCapability,
        purpose: str,
        node_name: str | None,
    ) -> tuple[str, ModelBinding | None]:
        binding = self._binding_for_node(capability, node_name)
        if binding is not None:
            return binding.provider, binding
        return self.provider_override or self.settings.provider_for(capability, purpose), None

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

    def text(self, purpose: str, *, node_name: str | None = None) -> TextLLM:
        provider_name, binding = self._provider_name_for("text", purpose, node_name)
        return self._provider_from_registry("text", provider_name, purpose, binding=binding)

    def image(self, purpose: str, *, node_name: str | None = None) -> ImageGenerator:
        provider_name, binding = self._provider_name_for("image", purpose, node_name)
        return self._provider_from_registry("image", provider_name, purpose, binding=binding)

    def video(self, purpose: str, *, node_name: str | None = None) -> VideoGenerator:
        provider_name, binding = self._provider_name_for("video", purpose, node_name)
        return self._provider_from_registry("video", provider_name, purpose, binding=binding)

    def audio(self, purpose: str, *, node_name: str | None = None) -> VoiceDesigner | SpeechSynthesizer:
        provider_name, binding = self._provider_name_for("audio", purpose, node_name)
        return self._provider_from_registry("audio", provider_name, purpose, binding=binding)

    def judge(self, purpose: str, *, node_name: str | None = None) -> AudioJudgeLLM:
        binding = self._binding_for_node("judge", node_name)
        provider_name = self.provider_override or (binding.provider if binding else None)
        if not provider_name:
            try:
                provider_name = self.settings.provider_for("judge", purpose)
            except KeyError:
                provider_name = "aliyun_omni"
        return self._provider_from_registry("judge", provider_name, purpose, binding=binding)

    def music(self, purpose: str, *, node_name: str | None = None) -> MusicGenerator:
        binding = self._binding_for_node("music", node_name)
        provider_name = self.provider_override or (binding.provider if binding else None)
        if not provider_name:
            try:
                provider_name = self.settings.provider_for("music", purpose)
            except KeyError:
                provider_name = self.settings.provider_for("audio", "music")

        return self._provider_from_registry("music", provider_name, purpose, binding=binding)
