from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    BGM,
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    RoleVoiceDesignOutput,
    RoleVoiceGenerationItem,
    RoleVoiceGenerationOutput,
    ShotDialogueAudioAsset,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.core.visual_style import visual_style_metadata
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.services.storyboard_service import StoryboardService
from autodrama.utils.prompts import PromptStore

NodeRunner = Callable[[ProjectState], Awaitable[ProjectState]]


PREGEN_NODES = [
    "script_outline",
    "script_detail",
    "script_polish",
    "role_design",
    "role_voice_design",
    "role_voice_generation",
    "role_appearance_design",
    "role_appearance_generation",
    "prop_design",
    "prop_image_generation",
    "script_compress",
    "layout_design",
    "layout_dedupe_review",
    "layout_image_generation",
    "bgm_design",
    "bgm_generation",
]

PREGEN_EPISODE_SCOPED_NODES: set[str] = set()


class PregenWorkflow:
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self.repo = repo
        self.router = router
        self.prompts = prompts or PromptStore()
        self.script_service = ScriptService(self.prompts)
        self.role_service = RoleService(self.prompts)
        self.asset_service = AssetService(self.prompts)
        self.storyboard_service = StoryboardService(self.prompts)

    def _expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def _validate_episode_keys(self, label: str, payload: dict[str, str], state: ProjectState) -> None:
        expected_keys = self._expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    @staticmethod
    def _add_role_lookup_key(lookup: dict[str, Role], key: str | None, role: Role) -> None:
        if not key:
            return
        normalized_key = str(key).strip()
        if not normalized_key:
            return
        lookup.setdefault(normalized_key, role)
        lookup.setdefault(normalized_key.casefold(), role)

    def _role_lookup(self, state: ProjectState) -> dict[str, Role]:
        lookup: dict[str, Role] = {}
        for role in state.roles.values():
            self._add_role_lookup_key(lookup, role.id, role)
            self._add_role_lookup_key(lookup, role.name, role)
            self._add_role_lookup_key(lookup, normalize_id("role", role.name), role)
            for alias in role.aliases:
                self._add_role_lookup_key(lookup, alias, role)
                self._add_role_lookup_key(lookup, normalize_id("role", alias), role)
        return lookup

    @staticmethod
    def _resolve_role(lookup: dict[str, Role], role_name: str) -> Role | None:
        key = str(role_name).strip()
        return lookup.get(key) or lookup.get(key.casefold())

    @staticmethod
    def _available_speakers(provider) -> list[dict[str, Any]]:
        getter = getattr(provider, "available_speakers", None)
        if not callable(getter):
            return []
        speakers = getter()
        if not isinstance(speakers, list):
            return []
        return [dict(speaker) for speaker in speakers if isinstance(speaker, dict)]

    @staticmethod
    def _available_speakers_for_prompt(provider) -> list[dict[str, Any]]:
        getter = getattr(provider, "available_speakers_for_prompt", None)
        if callable(getter):
            speakers = getter()
            if isinstance(speakers, list):
                return [dict(speaker) for speaker in speakers if isinstance(speaker, dict)]
        return PregenWorkflow._available_speakers(provider)

    @staticmethod
    def _speaker_lookup(provider) -> dict[str, dict[str, Any]]:
        try:
            speakers = PregenWorkflow._available_speakers(provider)
        except Exception as exc:
            get_logger().warning("Could not load speech voice catalog for validation: %s", exc)
            return {}
        return {
            str(speaker["voice_type"]): speaker
            for speaker in speakers
            if speaker.get("voice_type")
        }

    @staticmethod
    def _clean_optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _role_voice_speaker(cls, item, speaker_lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        voice_type = cls._clean_optional_text(getattr(item, "voice_type", None))
        if not voice_type:
            return None
        if speaker_lookup:
            return speaker_lookup.get(voice_type)
        return {
            "name": cls._clean_optional_text(getattr(item, "voice_name", None)),
            "voice_type": voice_type,
            "resource_id": cls._clean_optional_text(getattr(item, "voice_resource_id", None)),
        }

    @staticmethod
    def _voice_candidate_priority(emotion: object) -> int:
        return 2 if str(emotion).strip().lower() == "normal" else 1

    @classmethod
    def _bind_role_voice(cls, role: Role, item, speaker: dict[str, Any]) -> None:
        role.voice_name = cls._clean_optional_text(speaker.get("name")) or cls._clean_optional_text(
            getattr(item, "voice_name", None)
        )
        role.voice_type = cls._clean_optional_text(speaker.get("voice_type")) or cls._clean_optional_text(
            getattr(item, "voice_type", None)
        )
        role.voice_resource_id = cls._clean_optional_text(
            speaker.get("resource_id")
        ) or cls._clean_optional_text(getattr(item, "voice_resource_id", None))
        role.voice_model_family = cls._clean_optional_text(speaker.get("model_family"))
        role.voice_selection_reason = cls._clean_optional_text(getattr(item, "voice_selection_reason", None))

    @staticmethod
    def _copy_role_voice_to_audio(role: Role, audio: RoleAudio) -> None:
        if not role.voice_type:
            return
        audio.voice_name = role.voice_name
        audio.voice_type = role.voice_type
        audio.voice_resource_id = role.voice_resource_id
        audio.voice_model_family = role.voice_model_family

    @staticmethod
    def _default_voice_sample_text(role: Role) -> str:
        intro = role.intro.rstrip("。")
        return f"我是{role.name}。{intro}。面对眼前的问题，我会保持冷静，按照自己的判断继续向前。"

    def _ensure_normal_role_audio(self, role: Role) -> None:
        if "normal" in role.audio:
            normal_voice = role.audio["normal"]
            if not normal_voice.sample_text:
                normal_voice.sample_text = self._default_voice_sample_text(role)
            role.voice_summary = normal_voice.desc
            self._copy_role_voice_to_audio(role, normal_voice)
            return

        first_audio = next(iter(role.audio.values()), None)
        if first_audio is not None:
            desc = (
                f"{role.name}的常规音色。参考已有{first_audio.emotion}音色的年龄感、性别感和基础音色，"
                "但情绪保持平稳自然，语速适中，咬字清晰，适合作为后续情绪音色克隆的基础音色。"
            )
        else:
            desc = (
                f"{role.name}的常规音色。{role.intro} "
                "声音自然平稳，语速适中，咬字清晰，情绪克制，适合作为后续 TTS 的基础音色。"
            )
        audio_id = normalize_id(f"{role.id}_audio", "normal")
        role.audio["normal"] = RoleAudio(
            id=audio_id,
            role_id=role.id,
            emotion="normal",
            desc=desc,
            sample_text=self._default_voice_sample_text(role),
        )
        self._copy_role_voice_to_audio(role, role.audio["normal"])
        role.voice_summary = desc

    def _apply_role_voice_design_output(
        self,
        state: ProjectState,
        output: RoleVoiceDesignOutput,
        *,
        speech_provider=None,
    ) -> None:
        roles_by_key = self._role_lookup(state)
        speaker_lookup = self._speaker_lookup(speech_provider) if speech_provider is not None else {}
        unmatched_role_names: list[str] = []
        invalid_voice_types: list[str] = []
        voice_candidates: dict[str, tuple[int, object, dict[str, Any]]] = {}

        for item in output.role_voices:
            role = self._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            speaker = self._role_voice_speaker(item, speaker_lookup)
            if getattr(item, "voice_type", None) and speaker is None:
                invalid_voice_types.append(f"{role.name}:{item.voice_type}")
            if speaker is not None:
                priority = self._voice_candidate_priority(item.emotion)
                current = voice_candidates.get(role.id)
                if current is None or priority > current[0]:
                    voice_candidates[role.id] = (priority, item, speaker)
            audio_id = normalize_id(f"{role.id}_audio", str(item.emotion))
            role.audio[str(item.emotion)] = RoleAudio(
                id=audio_id,
                role_id=role.id,
                emotion=str(item.emotion),
                desc=item.desc,
                sample_text=item.sample_text,
            )
        for role_id, (_, item, speaker) in voice_candidates.items():
            role = state.roles.get(role_id)
            if role is not None:
                self._bind_role_voice(role, item, speaker)
        if unmatched_role_names:
            get_logger().warning(
                "node=role_voice_design ignored unmatched role_name values: %s",
                ", ".join(unmatched_role_names),
            )
        if invalid_voice_types:
            get_logger().warning(
                "node=role_voice_design ignored invalid voice_type values: %s",
                ", ".join(invalid_voice_types),
            )
        for role in state.roles.values():
            self._ensure_normal_role_audio(role)
            for audio in role.audio.values():
                self._copy_role_voice_to_audio(role, audio)

    def _repair_role_voice_design_if_needed(self, project_dir: Path, state: ProjectState, *, speech_provider=None) -> None:
        if all("normal" in role.audio and role.voice_type for role in state.roles.values()):
            return

        path = project_dir / "assets" / "json" / "nodes" / "role_voice_design.json"
        if path.exists():
            output = RoleVoiceDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))
            self._apply_role_voice_design_output(state, output, speech_provider=speech_provider)
            return

        for role in state.roles.values():
            self._ensure_normal_role_audio(role)

    def _validate_script_outline(self, output_episode_count: int, output_duration: int, state: ProjectState) -> None:
        expected_episode_count = self.script_service.episode_count(state)
        expected_duration = self.script_service.episode_duration_seconds(state)
        if output_episode_count != expected_episode_count:
            raise ValueError(
                f"script_outline episode_count must be {expected_episode_count}; got {output_episode_count}"
            )
        if output_duration != expected_duration:
            raise ValueError(
                f"script_outline target_duration_seconds must be {expected_duration}; got {output_duration}"
            )

    def _apply_script_plan_settings(self, state: ProjectState) -> None:
        state.metadata["episode_count"] = self.repo.settings.project.episode_count
        state.metadata["episode_duration_seconds"] = self.repo.settings.project.episode_duration_seconds
        state.metadata["bgm_count"] = self.repo.settings.project.bgm_count
        state.metadata.update(visual_style_metadata(self.repo.settings.project.visual_style))

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "bgm_generation",
        force: bool = False,
        only: str | None = None,
        episode_keys: list[str] | None = None,
    ) -> ProjectState:
        if until not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen stop node: {until}")
        if only is not None and only not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen only node: {only}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        target_nodes = [only] if only else PREGEN_NODES[: PREGEN_NODES.index(until) + 1]
        selected_episode_keys = self._select_episode_keys(state, episode_keys) if episode_keys else None
        if selected_episode_keys and not PREGEN_EPISODE_SCOPED_NODES.intersection(target_nodes):
            raise ValueError(
                "--episodes is not supported for pregen nodes. storyboard_generation now belongs to "
                "the generation workflow; run generation --only storyboard_generation --episodes ..."
            )
        logger.info(
            "workflow=pregen project_id=%s until=%s only=%s force=%s episodes=%s completed=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys or []) or "-",
            ",".join(state.completed_nodes) or "-",
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        if selected_episode_keys is not None:
            self._active_episode_keys = set(selected_episode_keys)
        try:
            for index, node_name in enumerate(target_nodes, start=1):
                if not only and not force and node_name in state.completed_nodes:
                    with log_context(node_name=node_name):
                        logger.info("node %d/%d %s skipped", index, len(target_nodes), node_name)
                    continue
                with log_context(node_name=node_name):
                    logger.info("node %d/%d %s started", index, len(target_nodes), node_name)
                    try:
                        state = await getattr(self, f"_run_{node_name}")(project_dir, state)
                        state.mark_completed(node_name)
                        self.repo.save_state(project_dir, state)
                    except Exception:
                        logger.exception("node %d/%d %s failed", index, len(target_nodes), node_name)
                        raise
                    logger.info(
                        "node %d/%d %s completed current_node=%s",
                        index,
                        len(target_nodes),
                        node_name,
                        state.current_node,
                    )
        finally:
            if selected_episode_keys is not None:
                if previous_active_episode_keys is None:
                    delattr(self, "_active_episode_keys")
                else:
                    self._active_episode_keys = previous_active_episode_keys

        logger.info("workflow=pregen completed project_id=%s current_node=%s", state.project_id, state.current_node)
        return state

    def _select_episode_keys(self, state: ProjectState, episode_keys: list[str] | None) -> list[str]:
        expected_episode_keys = self._expected_episode_keys(state)
        if not episode_keys:
            return expected_episode_keys

        requested = [key for key in episode_keys if key]
        unknown = sorted(set(requested).difference(expected_episode_keys))
        if unknown:
            raise ValueError(f"Unknown episode keys: {', '.join(unknown)}")
        requested_set = set(requested)
        return [key for key in expected_episode_keys if key in requested_set]

    async def _run_script_outline(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_outline provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_outline(state, provider)
        self._validate_script_outline(output.episode_count, output.target_duration_seconds, state)
        self._validate_episode_keys("script_outline.episode_outlines", output.episode_outlines, state)
        state.script.outline = output.outline
        state.script.episode_outlines = output.episode_outlines
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_outline", output)
        return state

    async def _run_script_detail(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_detail provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_detail(state, provider)
        self._validate_episode_keys("script_detail.detailed_script", output.detailed_script, state)
        state.script.detailed_script = output.detailed_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_detail", output)
        return state

    async def _run_script_polish(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_polish provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_polish(state, provider)
        self._validate_episode_keys("script_polish.final_script", output.final_script, state)
        state.script.final_script = output.final_script
        state.script.revision_notes = output.revision_notes
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_polish", output)
        return state

    async def _run_role_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.role_service.role_design(state, provider)
        roles: dict[str, Role] = {}
        for item in output.roles:
            role_id = normalize_id("role", item.name)
            roles[role_id] = Role(
                id=role_id,
                name=item.name,
                intro=item.intro,
                personality=item.personality,
                aliases=item.aliases,
            )
        state.roles = roles
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_design", output)
        return state

    async def _run_role_voice_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        speech_provider = None
        available_voices: list[dict[str, Any]] = []
        try:
            speech_provider = self.router.audio("speech")
            available_voices = self._available_speakers_for_prompt(speech_provider)
        except Exception as exc:
            get_logger().warning("node=role_voice_design could not load speech voice catalog: %s", exc)
        get_logger().info(
            "node=role_voice_design provider=%s model=%s available_voices=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            len(available_voices),
        )
        output = await self.role_service.role_voice_design(
            state,
            provider,
            available_voices=available_voices,
        )
        self._apply_role_voice_design_output(state, output, speech_provider=speech_provider)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_voice_design", output)
        return state

    def _voice_preferred_name(self, state: ProjectState, audio: RoleAudio) -> str:
        digest = hashlib.sha1(f"{state.project_id}:{audio.id}".encode("utf-8")).hexdigest()
        return f"ad_{digest[:13]}"

    def _preview_text(self, role: Role, audio: RoleAudio) -> str:
        return (audio.sample_text or f"我是{role.name}。")[:1024]

    def _synthesis_text(self, provider, audio: RoleAudio, preview_text: str) -> str:
        if not getattr(provider, "is_cosyvoice", False):
            return preview_text

        emotion_tags = {
            "angry": "<|ANGRY|>",
            "sad": "<|SAD|>",
            "happy": "<|HAPPY|>",
            "tense": "<|NEUTRAL|><|1.10|>",
            "whisper": "<|CALM|><|0.80|>",
        }
        return f"{emotion_tags.get(audio.emotion, '')}{preview_text}"

    def _write_preview_audio(
        self,
        project_dir: Path,
        *,
        audio: RoleAudio,
        data: str | None,
        response_format: str | None,
    ) -> str | None:
        if not data:
            return None

        extension = (response_format or "wav").lower()
        extension = {"ogg_opus": "opus"}.get(extension, extension)
        if extension not in {"wav", "mp3", "pcm", "opus", "ogg", "m4a", "aac"}:
            extension = "bin"

        output_dir = project_dir / "assets" / "audios" / "role_voices"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio.id}.{extension}"
        output_path.write_bytes(base64.b64decode(data))
        return str(output_path.relative_to(project_dir)).replace("\\", "/")

    def _absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return str(project_dir / relative_path)

    @staticmethod
    def _project_relative(project_dir: Path, path: Path) -> str:
        return str(path.relative_to(project_dir)).replace("\\", "/")

    @staticmethod
    def _slot_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "slots" / f"{episode_key}.json"

    def _load_storyboard_episode(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput:
        path = self._slot_path(project_dir, episode_key)
        if not path.exists():
            raise FileNotFoundError(f"Storyboard slot file not found: {path}")
        return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_storyboard_episode(self, project_dir: Path, episode: StoryboardEpisodeOutput) -> None:
        self.repo.write_json(self._slot_path(project_dir, episode.episode_key), episode)

    def _iter_storyboard_episodes(
        self,
        project_dir: Path,
        state: ProjectState,
    ) -> list[StoryboardEpisodeOutput]:
        return [
            self._load_storyboard_episode(project_dir, episode_key)
            for episode_key in self._active_episode_keys_in_order(state)
        ]

    def _active_episode_keys_in_order(self, state: ProjectState) -> list[str]:
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        if active_episode_keys is not None:
            return [
                episode_key
                for episode_key in self._expected_episode_keys(state)
                if episode_key in active_episode_keys
            ]
        return self._expected_episode_keys(state)

    @staticmethod
    def _image_asset_path(project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return project_dir / "assets" / "images" / asset_type / f"{asset_id}.png"

    @staticmethod
    def _music_asset_path(project_dir: Path, asset_id: str, audio_format: str | None) -> Path:
        extension = (audio_format or "mp3").lower().lstrip(".")
        if extension not in {"mp3", "wav", "m4a", "aac", "ogg"}:
            extension = "mp3"
        return project_dir / "assets" / "audios" / "bgms" / f"{asset_id}.{extension}"

    @staticmethod
    def _audio_asset_path(project_dir: Path, asset_type: str, asset_id: str, audio_format: str | None) -> Path:
        extension = (audio_format or "mp3").lower().lstrip(".")
        extension = {"ogg_opus": "opus"}.get(extension, extension)
        if extension not in {"wav", "mp3", "pcm", "opus", "ogg", "m4a", "aac"}:
            extension = "bin"
        return project_dir / "assets" / "audios" / asset_type / f"{asset_id}.{extension}"

    @staticmethod
    def _video_asset_path(project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return project_dir / "assets" / "videos" / asset_type / f"{asset_id}.mp4"

    async def _write_first_generated_image(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.image_data:
            data = result.image_data[0]
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not result.image_urls:
            raise ValueError("Image generation result has no image data or URL")

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(result.image_urls[0])
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated image HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

    async def _write_generated_music(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.audio_data:
            data = result.audio_data
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not result.audio_url:
            raise ValueError("Music generation result has no audio data or URL")

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(result.audio_url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated music HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

    async def _write_generated_audio(
        self,
        project_dir: Path,
        output_path: Path,
        *,
        audio_data: str | None = None,
        audio_url: str | None = None,
    ) -> str | None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if audio_data:
            data = audio_data
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not audio_url:
            return None

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(audio_url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated audio HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

    async def _write_generated_video(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str | None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(result, "video_data", None):
            data = result.video_data
            if data.startswith("data:") and ";base64," in data:
                data = data.split(";base64,", 1)[1]
            output_path.write_bytes(base64.b64decode(data))
            return self._project_relative(project_dir, output_path)

        if not result.video_url:
            return None

        async with httpx.AsyncClient(timeout=self.repo.settings.runtime.request_timeout_seconds) as client:
            response = await client.get(result.video_url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download generated video HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return self._project_relative(project_dir, output_path)

    async def _generate_designed_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        preview_text = self._preview_text(role, audio)
        result = await provider.create_voice(
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preferred_name=self._voice_preferred_name(state, audio),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "design",
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=result.preview_audio_data,
            response_format=result.preview_audio_format,
        )
        audio.asset_id = result.voice
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="design",
            voice=result.voice,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=result.provider,
            model=result.model,
            target_model=result.target_model,
            sample_rate=result.preview_audio_sample_rate,
            response_format=result.preview_audio_format,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )

    async def _generate_cloned_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        normal_audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        if not normal_audio.asset_path:
            raise ValueError(f"Cannot clone {role.name}/{audio.emotion}: normal voice preview audio is missing")

        preview_text = self._preview_text(role, audio)
        clone_result = await provider.clone_voice_from_audio(
            source_audio_path=self._absolute_project_path(project_dir, normal_audio.asset_path),
            preferred_name=self._voice_preferred_name(state, audio),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "clone",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
            },
        )
        synthesis_result = await provider.synthesize_speech(
            voice=clone_result.voice,
            text=preview_text,
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "clone",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
                "target_model": clone_result.target_model,
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = clone_result.voice
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="clone",
            voice=clone_result.voice,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=clone_result.provider,
            model=clone_result.model,
            target_model=clone_result.target_model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=clone_result.request_id,
            usage={
                "clone": clone_result.usage,
                "synthesis": synthesis_result.usage,
            },
            raw_response={
                "clone": clone_result.raw_response,
                "synthesis": synthesis_result.raw_response,
            },
        )

    async def _generate_reused_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        normal_audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        if not normal_audio.asset_id:
            raise ValueError(f"Cannot reuse {role.name}/{audio.emotion}: normal voice id is missing")

        preview_text = self._preview_text(role, audio)
        synthesis_result = await provider.synthesize_speech(
            voice=normal_audio.asset_id,
            text=self._synthesis_text(provider, audio, preview_text),
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "reuse",
                "source_audio_id": normal_audio.id,
                "source_audio_path": normal_audio.asset_path,
                "target_model": getattr(provider, "target_model", None),
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        audio.asset_id = normal_audio.asset_id
        audio.asset_path = preview_audio_path
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="reuse",
            voice=normal_audio.asset_id,
            source_audio_id=normal_audio.id,
            source_audio_path=normal_audio.asset_path,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            preview_audio_path=preview_audio_path,
            provider=synthesis_result.provider,
            model=synthesis_result.model,
            target_model=synthesis_result.model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=synthesis_result.request_id,
            usage=synthesis_result.usage,
            raw_response=synthesis_result.raw_response,
        )

    def _role_synthesis_voice(self, provider, role: Role) -> str:
        if role.voice_type:
            return role.voice_type
        resolver = getattr(provider, "resolve_role_voice", None)
        if callable(resolver):
            return str(
                resolver(
                    role_id=role.id,
                    role_name=role.name,
                    role_intro=role.intro,
                    role_voice_summary=role.voice_summary,
                    role_personality=role.personality,
                )
            )
        normal_audio = role.audio.get("normal")
        if normal_audio and normal_audio.asset_id:
            return normal_audio.asset_id
        raise ValueError(f"Cannot synthesize role voice for {role.name}: provider cannot resolve a voice")

    @staticmethod
    def _role_synthesis_resource_id(provider, role: Role, voice: str) -> str | None:
        if role.voice_resource_id:
            return role.voice_resource_id
        resolver = getattr(provider, "resolve_voice_resource_id", None)
        if callable(resolver):
            resource_id = resolver(voice)
            if resource_id:
                return str(resource_id)
        resource_id = getattr(provider, "resource_id", None) or getattr(provider, "model", None)
        return str(resource_id) if resource_id else None

    @staticmethod
    def _role_emotion_synthesis_plan(provider, audio: RoleAudio) -> tuple[str | None, dict[str, Any]]:
        resolver = getattr(provider, "resolve_emotion_plan", None)
        plan = resolver(audio.emotion) if callable(resolver) else {}
        if not isinstance(plan, dict):
            plan = {}

        instruction_value = plan.get("instruction")
        instruction = str(instruction_value).strip() if instruction_value is not None else None
        if instruction == "":
            instruction = None

        params_resolver = getattr(provider, "emotion_params_from_plan", None)
        if callable(params_resolver):
            params = params_resolver(plan)
        else:
            params = {key: value for key, value in plan.items() if key != "instruction"}
        return instruction, dict(params)

    async def _generate_synthesized_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
        voice: str,
        voice_resource_id: str | None = None,
    ) -> RoleVoiceGenerationItem:
        preview_text = self._preview_text(role, audio)
        emotion_instruction, emotion_params = self._role_emotion_synthesis_plan(provider, audio)
        target_model = voice_resource_id or getattr(provider, "model", None)
        synthesis_result = await provider.synthesize_speech(
            voice=voice,
            text=preview_text,
            metadata={
                "node_name": "role_voice_generation",
                "project_id": state.project_id,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": audio.id,
                "emotion": audio.emotion,
                "generation_method": "synthesis",
                "voice_prompt": audio.desc,
                "emotion_instruction": emotion_instruction,
                "emotion_params": emotion_params,
                "resource_id": voice_resource_id,
                "target_model": target_model,
            },
        )
        preview_audio_path = self._write_preview_audio(
            project_dir,
            audio=audio,
            data=synthesis_result.audio_data,
            response_format=synthesis_result.audio_format,
        )
        resolved_voice = synthesis_result.voice or voice
        audio.asset_id = resolved_voice
        audio.voice_name = role.voice_name
        audio.voice_type = resolved_voice
        audio.voice_resource_id = voice_resource_id
        audio.voice_model_family = role.voice_model_family
        audio.asset_path = preview_audio_path
        audio.emotion_instruction = emotion_instruction
        audio.emotion_params = emotion_params
        return RoleVoiceGenerationItem(
            role_id=role.id,
            role_name=role.name,
            emotion=audio.emotion,
            audio_id=audio.id,
            generation_method="synthesis",
            voice=resolved_voice,
            voice_name=role.voice_name,
            voice_resource_id=voice_resource_id,
            voice_model_family=role.voice_model_family,
            voice_selection_reason=role.voice_selection_reason,
            voice_prompt=audio.desc,
            preview_text=preview_text,
            emotion_instruction=emotion_instruction,
            emotion_params=emotion_params,
            preview_audio_path=preview_audio_path,
            provider=synthesis_result.provider,
            model=synthesis_result.model,
            target_model=voice_resource_id or synthesis_result.model,
            sample_rate=synthesis_result.audio_sample_rate,
            response_format=synthesis_result.audio_format,
            request_id=synthesis_result.request_id,
            usage=synthesis_result.usage,
            raw_response=synthesis_result.raw_response,
        )

    def _shot_ref_asset_refs(self, project_dir: Path, state: ProjectState, shot: StoryboardShot) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        layout = state.layouts.get(shot.layout_id)
        if layout and layout.asset_path:
            refs.append(
                AssetRef(
                    id=layout.id,
                    type="image",
                    path=str(project_dir / layout.asset_path),
                    metadata={"asset_type": "layout", "name": layout.name},
                )
            )

        appearance_ids = list(shot.role_appearance_ids)
        seen_appearance_ids = set(appearance_ids)
        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            base_appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if base_appearance and base_appearance.id not in seen_appearance_ids:
                appearance_ids.append(base_appearance.id)
                seen_appearance_ids.add(base_appearance.id)

        for appearance_id in appearance_ids:
            for role in state.roles.values():
                appearance = next(
                    (
                        item
                        for item in role.appearances.values()
                        if item.id == appearance_id or item.name == appearance_id
                    ),
                    None,
                )
                if appearance and appearance.asset_path:
                    refs.append(
                        AssetRef(
                            id=appearance.id,
                            type="image",
                            path=str(project_dir / appearance.asset_path),
                            metadata={
                                "asset_type": "role_appearance",
                                "role_id": role.id,
                                "role_name": role.name,
                                "name": appearance.name,
                            },
                        )
                    )
                    break

        for prop_id in shot.prop_ids:
            prop = state.props.get(prop_id)
            if prop and prop.asset_path:
                refs.append(
                    AssetRef(
                        id=prop.id,
                        type="image",
                        path=str(project_dir / prop.asset_path),
                        metadata={"asset_type": "prop", "name": prop.name},
                    )
                )
        return refs

    def _shot_video_refs(self, project_dir: Path, state: ProjectState, shot: StoryboardShot) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        if shot.ref_frame_asset_path:
            refs.append(
                AssetRef(
                    id=shot.ref_frame_asset_id,
                    type="image",
                    path=str(project_dir / shot.ref_frame_asset_path),
                    metadata={"asset_type": "ref_frame"},
                )
            )
        refs.extend(self._shot_ref_asset_refs(project_dir, state, shot))
        for audio in shot.dialogue_audio_assets:
            if audio.asset_path:
                refs.append(
                    AssetRef(
                        id=audio.asset_id,
                        type="audio",
                        path=str(project_dir / audio.asset_path),
                        metadata={
                            "asset_type": "shot_dialogue_audio",
                            "role_id": audio.role_id,
                            "line_index": audio.line_index,
                        },
                    )
                )
        return refs

    def _shot_ref_frame_prompt(self, state: ProjectState, episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> str:
        layout = state.layouts.get(shot.layout_id)
        role_lines = []
        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role:
                role_lines.append(f"{role.name}: {role.intro}")
        appearance_lines = []
        appearance_ids = list(shot.role_appearance_ids)
        seen_appearance_ids = set(appearance_ids)
        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            base_appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if base_appearance and base_appearance.id not in seen_appearance_ids:
                appearance_ids.append(base_appearance.id)
                seen_appearance_ids.add(base_appearance.id)
        for appearance_id in appearance_ids:
            for role in state.roles.values():
                appearance = next(
                    (
                        item
                        for item in role.appearances.values()
                        if item.id == appearance_id or item.name == appearance_id
                    ),
                    None,
                )
                if appearance:
                    appearance_lines.append(f"{role.name}外观锁定: {appearance.desc}")
                    break
        prop_lines = []
        for prop_id in shot.prop_ids:
            prop = state.props.get(prop_id)
            if prop:
                prop_lines.append(f"{prop.name}: {prop.desc}")

        parts = [
            str(state.metadata.get("visual_style_prompt", "")),
            f"剧集: {episode.episode_key}",
            f"镜头: {shot.title}",
            f"画面内容: {shot.content}",
            "输出规格: 9:16竖屏单帧剧照，适配手机短剧画幅；主体完整，避免横版构图或左右大面积留白。",
            f"镜头角度: {shot.camera_shooting_angle}",
            f"运镜: {shot.camera_movement}",
            f"焦段: {shot.focal_length or '按分镜自然选择'}",
            f"分镜参考帧要求: {shot.ref_frame_prompt}",
        ]
        if layout:
            parts.append(f"场景设定: {layout.name} - {layout.desc}")
        if role_lines:
            parts.append("出场角色: " + "；".join(role_lines))
        if appearance_lines:
            parts.append("人物一致性要求: " + "；".join(appearance_lines))
        if prop_lines:
            parts.append("关键道具: " + "；".join(prop_lines))
        if shot.dialogue:
            parts.append("画面对白气氛: " + " / ".join(shot.dialogue))
        parts.append("生成单帧剧照，必须是同一个镜头里的关键帧；不要添加字幕、水印、文字标识或分镜编号。")
        return "\n".join(item for item in parts if item)

    def _shot_video_prompt(self, state: ProjectState, episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> str:
        layout = state.layouts.get(shot.layout_id)
        bgm = state.bgms.get(shot.bgm_id) if shot.bgm_id else None
        parts = [
            str(state.metadata.get("visual_style_prompt", "")),
            f"剧集: {episode.episode_key}",
            f"镜头标题: {shot.title}",
            f"镜头内容: {shot.content}",
            f"视频动作: {shot.video_prompt}",
            f"镜头角度: {shot.camera_shooting_angle}",
            f"运镜: {shot.camera_movement}",
            f"焦段: {shot.focal_length or '自然电影焦段'}",
            f"时长: {shot.duration_seconds:.2f} 秒",
        ]
        if layout:
            parts.append(f"场景: {layout.name} - {layout.desc}")
        if shot.dialogue:
            parts.append("对白节奏: " + " / ".join(shot.dialogue))
        if bgm:
            parts.append(f"BGM情绪参考: {bgm.name} - {bgm.mood}")
        parts.append("保持人物、场景和道具与参考帧一致；画面自然连续；不要生成字幕、水印、片头片尾或额外文字。")
        return "\n".join(item for item in parts if item)

    def _role_for_dialogue_line(
        self,
        state: ProjectState,
        shot: StoryboardShot,
        line: str,
    ) -> tuple[Role | None, str, str | None]:
        text = str(line).strip()
        speaker_name: str | None = None
        dialogue_text = text
        for separator in ("：", ":"):
            if separator in text:
                prefix, suffix = text.split(separator, 1)
                candidate = prefix.strip()
                if 0 < len(candidate) <= 20:
                    speaker_name = candidate
                    dialogue_text = suffix.strip()
                    break

        lookup = self._role_lookup(state)
        role = self._resolve_role(lookup, speaker_name) if speaker_name else None
        if role is None and len(shot.role_ids) == 1:
            role = state.roles.get(shot.role_ids[0])
        if role is None and speaker_name:
            normalized = normalize_id("role", speaker_name)
            role = state.roles.get(normalized)
        return role, dialogue_text or text, speaker_name

    @staticmethod
    def _shot_dialogue_emotion(role: Role | None, shot: StoryboardShot) -> str:
        if role is None:
            return "normal"
        available = list(role.audio)
        content = f"{shot.title} {shot.content} {' '.join(shot.dialogue)}"
        keyword_map = [
            ("angry", ("怒", "吼", "质问", "逼问", "爆发", "愤")),
            ("sad", ("哭", "低落", "崩溃", "难过", "哽咽", "失落")),
            ("happy", ("笑", "开心", "轻松", "高兴")),
            ("tense", ("紧张", "压低", "慌", "僵", "冷", "对峙", "沉默", "证据")),
            ("whisper", ("耳语", "低声", "悄声")),
        ]
        for emotion, keywords in keyword_map:
            if emotion in available and any(keyword in content for keyword in keywords):
                return emotion
        return "normal" if "normal" in available else (available[0] if available else "normal")

    def _shot_dialogue_role_audio(self, role: Role | None, emotion: str) -> RoleAudio | None:
        if role is None:
            return None
        return role.audio.get(emotion) or role.audio.get("normal") or next(iter(role.audio.values()), None)

    async def _generate_shot_dialogue_audio(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        shot: StoryboardShot,
        line_index: int,
        line: str,
    ) -> tuple[ShotDialogueAudioAsset | None, dict[str, Any] | None]:
        role, dialogue_text, speaker_name = self._role_for_dialogue_line(state, shot, line)
        if role is None:
            return None, {
                "episode_key": episode_key,
                "shot_id": shot.shot_id,
                "line_index": line_index,
                "text": line,
                "reason": "Could not resolve dialogue speaker",
                "speaker_name": speaker_name,
            }

        emotion = self._shot_dialogue_emotion(role, shot)
        role_audio = self._shot_dialogue_role_audio(role, emotion)
        if role_audio is None:
            return None, {
                "episode_key": episode_key,
                "shot_id": shot.shot_id,
                "line_index": line_index,
                "text": line,
                "role_id": role.id,
                "reason": "Resolved role has no audio design",
            }

        voice = self._role_synthesis_voice(provider, role)
        voice_resource_id = self._role_synthesis_resource_id(provider, role, voice)
        emotion_instruction, emotion_params = self._role_emotion_synthesis_plan(provider, role_audio)
        asset_id = normalize_id(f"{shot.shot_id}_dialogue", f"{line_index:03d}_{role.name}")
        result = await provider.synthesize_speech(
            voice=voice,
            text=dialogue_text[:1024],
            metadata={
                "node_name": "shot_dialogue_audio_generation",
                "project_id": state.project_id,
                "episode_key": episode_key,
                "shot_id": shot.shot_id,
                "line_index": line_index,
                "role_id": role.id,
                "role_name": role.name,
                "audio_id": role_audio.id,
                "asset_id": asset_id,
                "emotion": role_audio.emotion,
                "voice_prompt": role_audio.desc,
                "emotion_instruction": emotion_instruction,
                "emotion_params": emotion_params,
                "resource_id": voice_resource_id,
                "target_model": voice_resource_id or getattr(provider, "model", None),
            },
        )
        asset_path = await self._write_generated_audio(
            project_dir,
            self._audio_asset_path(project_dir, "shot_dialogues", asset_id, result.audio_format),
            audio_data=result.audio_data,
        )
        return (
            ShotDialogueAudioAsset(
                asset_id=asset_id,
                role_id=role.id,
                role_name=role.name,
                line_index=line_index,
                text=dialogue_text,
                emotion=role_audio.emotion,
                voice=result.voice or voice,
                voice_name=role.voice_name,
                voice_resource_id=voice_resource_id,
                voice_model_family=role.voice_model_family,
                emotion_instruction=emotion_instruction,
                emotion_params=emotion_params,
                asset_path=asset_path,
                provider=result.provider,
                model=result.model,
                sample_rate=result.audio_sample_rate,
                response_format=result.audio_format,
                request_id=result.request_id,
                usage=result.usage,
                raw_response=result.raw_response,
            ),
            None,
        )

    async def _run_role_voice_synthesis_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
    ) -> ProjectState:
        generated: list[RoleVoiceGenerationItem] = []
        for role in state.roles.values():
            if role.audio.get("normal") is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")
            role_voice = self._role_synthesis_voice(provider, role)
            role_resource_id = self._role_synthesis_resource_id(provider, role, role_voice)
            if not role.voice_type:
                role.voice_type = role_voice
            if role_resource_id and not role.voice_resource_id:
                role.voice_resource_id = role_resource_id
            for audio in role.audio.values():
                self._copy_role_voice_to_audio(role, audio)
                generated.append(
                    await self._generate_synthesized_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        voice=role_voice,
                        voice_resource_id=role_resource_id,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, "role_voice_generation", output)
        return state

    async def _run_role_voice_design_clone_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
    ) -> ProjectState:
        generated: list[RoleVoiceGenerationItem] = []
        for role in state.roles.values():
            normal_audio = role.audio.get("normal")
            if normal_audio is None:
                raise ValueError(f"Cannot generate role voice for {role.name}: missing normal voice design")

            generated.append(
                await self._generate_designed_voice(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    role=role,
                    audio=normal_audio,
                )
            )

            for emotion, audio in role.audio.items():
                if emotion == "normal":
                    continue
                if not getattr(provider, "supports_local_voice_clone", True):
                    generated.append(
                        await self._generate_reused_voice(
                            provider=provider,
                            project_dir=project_dir,
                            state=state,
                            role=role,
                            audio=audio,
                            normal_audio=normal_audio,
                        )
                    )
                    continue
                generated.append(
                    await self._generate_cloned_voice(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        role=role,
                        audio=audio,
                        normal_audio=normal_audio,
                    )
                )

        output = RoleVoiceGenerationOutput(generated_voices=generated)
        self.repo.save_node_output(project_dir, "role_voice_generation", output)
        return state

    async def _run_role_voice_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.audio("speech")
        get_logger().info(
            "node=role_voice_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self._repair_role_voice_design_if_needed(project_dir, state, speech_provider=provider)

        if getattr(provider, "supports_direct_emotion_synthesis", False):
            return await self._run_role_voice_synthesis_generation(
                provider=provider,
                project_dir=project_dir,
                state=state,
            )

        return await self._run_role_voice_design_clone_generation(
            provider=provider,
            project_dir=project_dir,
            state=state,
        )

    async def _run_role_appearance_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_appearance_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.role_appearance_design(state, provider)
        roles_by_key = self._role_lookup(state)
        unmatched_role_names: list[str] = []
        for item in output.appearances:
            role = self._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            appearance_id = normalize_id(f"{role.id}_appearance", item.name)
            role.appearances[item.name] = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
            )
        if unmatched_role_names:
            get_logger().warning(
                "node=role_appearance_design ignored unmatched role_name values: %s",
                ", ".join(unmatched_role_names),
            )
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_appearance_design", output)
        return state

    async def _run_role_appearance_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("role")
        get_logger().info(
            "node=role_appearance_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        appearances = [
            (role, appearance)
            for role in state.roles.values()
            for appearance in role.appearances.values()
        ]
        get_logger().info(
            "node=role_appearance_generation total_images=%d",
            len(appearances),
        )
        for role, appearance in appearances:
            result = await provider.generate_image(
                appearance.prompt,
                metadata={
                    "node_name": "role_appearance_generation",
                    "project_id": state.project_id,
                    "role_id": role.id,
                    "appearance_id": appearance.id,
                    "asset_id": appearance.id,
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "roles", appearance.id),
                result,
            )
            appearance.asset_id = appearance.id
            appearance.asset_path = asset_path
            appearance.provider = result.provider
            appearance.model = result.model
            appearance.request_id = result.request_id
            appearance.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=appearance.id,
                    asset_type="role_appearance",
                    owner_id=role.id,
                    name=f"{role.name}/{appearance.name}",
                    prompt=appearance.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            get_logger().info("%s generated successfully, saved in %s", appearance.id, asset_path)
        self.repo.save_node_output(project_dir, "role_appearance_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_prop_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop")
        get_logger().info(
            "node=prop_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.prop_design(state, provider)
        state.props = {
            normalize_id("prop", item.name): Prop(
                id=normalize_id("prop", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                status=item.status,
            )
            for item in output.props
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "prop_design", output)
        return state

    async def _run_prop_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop")
        get_logger().info(
            "node=prop_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        props = list(state.props.values())
        get_logger().info(
            "node=prop_image_generation total_images=%d",
            len(props),
        )
        for prop in props:
            result = await provider.generate_image(
                prop.prompt,
                metadata={
                    "node_name": "prop_image_generation",
                    "project_id": state.project_id,
                    "prop_id": prop.id,
                    "asset_id": prop.id,
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "props", prop.id),
                result,
            )
            prop.asset_id = prop.id
            prop.asset_path = asset_path
            prop.provider = result.provider
            prop.model = result.model
            prop.request_id = result.request_id
            prop.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=prop.id,
                    asset_type="prop",
                    owner_id=prop.id,
                    name=prop.name,
                    prompt=prop.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            get_logger().info("%s generated successfully, saved in %s", prop.id, asset_path)
        self.repo.save_node_output(project_dir, "prop_image_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_script_compress(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_compress provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.script_compress(state, provider)
        self._validate_episode_keys("script_compress.simple_script", output.simple_script, state)
        state.metadata["simple_script"] = output.simple_script
        state.metadata["global_script"] = output.global_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_compress", output)
        return state

    async def _run_layout_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        get_logger().info(
            "node=layout_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_design(state, provider)
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "layout_design", output)
        return state

    async def _run_layout_dedupe_review(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("layout")
        get_logger().info(
            "node=layout_dedupe_review provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.layout_dedupe_review(state, provider)
        state.layouts = {
            normalize_id("layout", item.name): Layout(
                id=normalize_id("layout", item.name),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                episode_keys=item.episode_keys,
            )
            for item in output.layouts
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "layout_dedupe_review", output)
        return state

    async def _run_layout_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("layout")
        get_logger().info(
            "node=layout_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        layouts = list(state.layouts.values())
        get_logger().info(
            "node=layout_image_generation total_images=%d",
            len(layouts),
        )
        for layout in layouts:
            result = await provider.generate_image(
                layout.prompt,
                metadata={
                    "node_name": "layout_image_generation",
                    "project_id": state.project_id,
                    "layout_id": layout.id,
                    "asset_id": layout.id,
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "layouts", layout.id),
                result,
            )
            layout.asset_id = layout.id
            layout.asset_path = asset_path
            layout.provider = result.provider
            layout.model = result.model
            layout.request_id = result.request_id
            layout.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=layout.id,
                    asset_type="layout",
                    owner_id=layout.id,
                    name=layout.name,
                    prompt=layout.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
            get_logger().info("%s generated successfully, saved in %s", layout.id, asset_path)
        self.repo.save_node_output(project_dir, "layout_image_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_bgm_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("bgm_plan")
        get_logger().info(
            "node=bgm_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.bgm_design(state, provider)
        expected_bgm_count = self.repo.settings.project.bgm_count
        if len(output.bgms) != expected_bgm_count:
            raise ValueError(f"bgm_design must generate exactly {expected_bgm_count} BGM items; got {len(output.bgms)}")
        bgm_ids = [normalize_id("bgm", item.name) for item in output.bgms]
        if len(set(bgm_ids)) != expected_bgm_count:
            raise ValueError("bgm_design generated duplicate BGM names after id normalization")
        state.bgms = {
            bgm_id: BGM(
                id=bgm_id,
                name=item.name,
                mood=item.mood,
                prompt=item.prompt,
                usage_hint=item.usage_hint,
            )
            for bgm_id, item in zip(bgm_ids, output.bgms, strict=True)
        }
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "bgm_design", output)
        return state

    async def _run_bgm_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.music("bgm")
        get_logger().info(
            "node=bgm_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        for bgm in state.bgms.values():
            result = await provider.generate_music(
                bgm.prompt,
                metadata={
                    "node_name": "bgm_generation",
                    "project_id": state.project_id,
                    "bgm_id": bgm.id,
                    "asset_id": bgm.id,
                },
            )
            asset_path = await self._write_generated_music(
                project_dir,
                self._music_asset_path(project_dir, bgm.id, result.audio_format),
                result,
            )
            bgm.asset_id = result.audio_id or bgm.id
            bgm.asset_path = asset_path
            bgm.provider = result.provider
            bgm.model = result.model
            bgm.request_id = result.request_id
            bgm.duration_seconds = result.duration_seconds
            bgm.lyrics = result.lyrics
            bgm.usage = result.usage
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=bgm.id,
                    asset_type="bgm",
                    owner_id=bgm.id,
                    name=bgm.name,
                    prompt=bgm.prompt,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
        self.repo.save_node_output(project_dir, "bgm_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state
