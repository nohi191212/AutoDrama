from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from autodrama.config import Settings
from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    RoleDesignItem,
    RoleDesignOutput,
    RoleExtractItem,
    RoleExtractOutput,
    RoleVoiceDesignOutput,
    RoleVoiceGenerationItem,
    ShotDialogueAudioAsset,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.prop_design_repo import PropDesignRepository
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.storyboard_repo import StoryboardRepository
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.role_design_repo import RoleDesignRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.media_store import MediaStore
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.services.storyboard_service import StoryboardService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.nodes import PREGEN_NODE_NAMES, build_pregen_nodes
from autodrama.workflows.nodes.bgm_nodes import BGMNodeBase, build_bgm_node_runners
from autodrama.workflows.nodes.script_nodes import (
    ScriptNodeBase,
    build_script_node_runners,
)
from autodrama.workflows.nodes.role_nodes import RoleNodeBase, build_role_node_runners
from autodrama.workflows.nodes.static_asset_nodes import (
    StaticAssetNodeBase,
    build_static_asset_node_runners,
)
from autodrama.workflows.nodes.voice_nodes import VoiceNodeBase, build_voice_node_runners
from autodrama.workflows.runner import WorkflowRunner
from autodrama.workflows.selection import select_episode_keys


PREGEN_NODES = PREGEN_NODE_NAMES
EPISODE_SCOPED_PREGEN_ONLY_NODES = {
    "role_design",
    "voice_select",
    "role_voice_generation",
    "role_full_body_generation",
    "role_multiview_generation",
    "role_intro_video_prompt",
    "role_intro_video_generation",
    "prop_design",
    "prop_generation",
    "layout_image_generation",
}
PREGEN_ONLY_ALIASES = {"prop_image_generation": "prop_generation"}


class PregenWorkflow:
    SAMPLE_TEXT_FORBIDDEN_BRACKETS = frozenset("()（）[]【】{}《》<>")
    SAMPLE_TEXT_FORBIDDEN_PHRASES = ("内心独白", "心理活动", "旁白说明", "舞台提示")

    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self.repo = repo
        self.settings = getattr(repo, "settings", Settings())
        self.layout = getattr(repo, "layout", ProjectLayout(self.settings))
        self.router = router
        self.prompts = prompts or PromptStore()
        self.script_service = ScriptService(self.prompts)
        self.role_service = RoleService(self.prompts)
        self.asset_service = AssetService(self.prompts)
        self.storyboard_service = StoryboardService(self.prompts)
        self.script_contents = ScriptContentRepository(self.repo, self.layout)
        self.role_designs = RoleDesignRepository(self.repo, self.layout)
        self.prop_designs = PropDesignRepository(self.repo, self.layout)
        self.media_store = MediaStore(
            self.layout,
            timeout_seconds=self.settings.runtime.request_timeout_seconds,
        )
        self.storyboards = StoryboardRepository(self.repo, self.layout)
        self.runner = WorkflowRunner(repo=self.repo, logger=get_logger())

    def _expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def _validate_episode_keys(self, label: str, payload: dict[str, object], state: ProjectState) -> None:
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

    @classmethod
    def _validate_voice_sample_text(cls, label: str, sample_text: str | None) -> None:
        text = str(sample_text or "")
        if not text:
            return
        bracket_chars = sorted({char for char in text if char in cls.SAMPLE_TEXT_FORBIDDEN_BRACKETS})
        forbidden_phrases = [phrase for phrase in cls.SAMPLE_TEXT_FORBIDDEN_PHRASES if phrase in text]
        if bracket_chars or forbidden_phrases:
            details: list[str] = []
            if bracket_chars:
                details.append(f"contains bracket characters: {''.join(bracket_chars)}")
            if forbidden_phrases:
                details.append(f"contains forbidden phrases: {', '.join(forbidden_phrases)}")
            raise ValueError(f"{label} must be direct spoken sample text without brackets or inner monologue; {'; '.join(details)}")

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
        preserve_assets: bool = False,
        validate_sample_text: bool = True,
        bind_voice_choice: bool = True,
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
            if not self._role_needs_voice(role):
                continue
            speaker = self._role_voice_speaker(item, speaker_lookup) if bind_voice_choice else None
            if bind_voice_choice and getattr(item, "voice_type", None) and speaker is None:
                invalid_voice_types.append(f"{role.name}:{item.voice_type}")
            if bind_voice_choice and speaker is not None:
                priority = self._voice_candidate_priority(item.emotion)
                current = voice_candidates.get(role.id)
                if current is None or priority > current[0]:
                    voice_candidates[role.id] = (priority, item, speaker)
            audio_id = normalize_id(f"{role.id}_audio", str(item.emotion))
            existing_audio = role.audio.get(str(item.emotion))
            audio = RoleAudio(
                id=audio_id,
                role_id=role.id,
                emotion=str(item.emotion),
                desc=item.desc,
                sample_text=item.sample_text,
            )
            if validate_sample_text:
                self._validate_voice_sample_text(
                    f"role_voice_design for {role.name} voices[{item.emotion or 'normal'}].sample_text",
                    audio.sample_text,
                )
            if preserve_assets and existing_audio is not None:
                audio.generation_status = existing_audio.generation_status
                audio.asset_id = existing_audio.asset_id
                audio.asset_path = existing_audio.asset_path
            role.audio[str(item.emotion)] = audio
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
            if not self._role_needs_voice(role):
                role.voice_summary = None
                role.voice_name = None
                role.voice_type = None
                role.voice_resource_id = None
                role.voice_model_family = None
                role.voice_selection_reason = None
                role.audio = {}
                continue
            self._ensure_normal_role_audio(role)
            for audio in role.audio.values():
                self._copy_role_voice_to_audio(role, audio)

    def _repair_role_voice_design_if_needed(self, project_dir: Path, state: ProjectState, *, speech_provider=None) -> None:
        if all(
            not self._role_needs_voice(role) or ("normal" in role.audio and role.voice_type)
            for role in state.roles.values()
        ):
            return

        path = project_dir / "assets" / "json" / "nodes" / "role_voice_design.json"
        if path.exists():
            output = RoleVoiceDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))
            self._apply_role_voice_design_output(state, output, speech_provider=speech_provider)
            return

        for role in state.roles.values():
            if self._role_needs_voice(role):
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
        state.metadata["role_design_style_prompt"] = self.repo.settings.generation.role_design_style_prompt
        state.metadata["prop_design_style_prompt"] = self.repo.settings.generation.prop_design_style_prompt
        state.metadata["layout_design_style_prompt"] = self.repo.settings.generation.layout_design_style_prompt

    def _script_content_path(self, project_dir: Path, category: str, episode_key: str) -> Path:
        return self.script_contents.content_path(project_dir, category, episode_key)

    def _script_novel_legacy_episode_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.script_contents.novel_legacy_episode_path(project_dir, episode_key)

    def _script_novel_legacy_full_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.script_contents.novel_legacy_full_path(project_dir, episode_key)

    @staticmethod
    def _script_content_payload(
        *,
        node_name: str,
        episode_key: str,
        content: str,
        dependency_field: str | None = None,
        dependency_path: object | None = None,
    ) -> dict[str, object]:
        return ScriptContentRepository.content_payload(
            node_name=node_name,
            episode_key=episode_key,
            content=content,
            dependency_field=dependency_field,
            dependency_path=dependency_path,
        )

    @classmethod
    def _load_script_content_ref(cls, project_dir: Path, value: object) -> str | None:
        return ScriptContentRepository.load_content_ref(project_dir, value)

    def _load_script_contents(
        self,
        project_dir: Path,
        refs: dict[str, object],
        episode_keys: list[str],
        *,
        label: str,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        return self.script_contents.load_contents(
            project_dir,
            refs,
            episode_keys,
            label=label,
            allow_missing=allow_missing,
        )

    def _episode_stories(self, project_dir: Path, state: ProjectState) -> dict[str, str]:
        episode_keys = self._expected_episode_keys(state)
        refs = state.script.novel_extract
        if not any(refs.get(episode_key) for episode_key in episode_keys):
            refs = state.script.novel_full
        return self._load_script_contents(
            project_dir,
            refs,
            episode_keys,
            label="episode_stories",
        )

    def _novel_full_contents(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str] | None = None,
        *,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        selected_keys = episode_keys or self._expected_episode_keys(state)
        return self._load_script_contents(
            project_dir,
            state.script.novel_full,
            selected_keys,
            label="script_novel.novel_full",
            allow_missing=allow_missing,
        )

    @staticmethod
    def _dedupe_texts(values: list[object]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            result.append(text)
            seen.add(text)
        return result

    def _load_role_extract_output(self, project_dir: Path) -> RoleExtractOutput:
        return self.role_designs.load_extract_output(project_dir)

    def _load_existing_role_design_output(self, project_dir: Path) -> RoleDesignOutput | None:
        return self.role_designs.load_existing_output(project_dir)

    def _role_design_json_path(self, project_dir: Path, role_id: str) -> Path:
        return self.role_designs.item_path(project_dir, role_id)

    def _role_design_relative_path(self, project_dir: Path, role_id: str) -> str:
        return self.role_designs.item_relative_path(project_dir, role_id)

    def _save_role_design_item(
        self,
        project_dir: Path,
        *,
        extract_item: RoleExtractItem,
        design_item: RoleDesignItem,
        role: Role,
        bound_props: list[Prop],
    ) -> str:
        return self.role_designs.save_design_item(
            project_dir,
            extract_item=extract_item,
            design_item=design_item,
            role=role,
            bound_props=bound_props,
        )

    @staticmethod
    def _load_role_design_item(path: Path) -> RoleDesignItem:
        return RoleDesignRepository.load_item(path)

    def _load_role_design_item_for_role(self, project_dir: Path, role: Role) -> RoleDesignItem | None:
        return self.role_designs.load_item_for_role(project_dir, role)

    @staticmethod
    def _role_design_item_complete(item: RoleDesignItem) -> bool:
        return bool(
            str(item.intro or "").strip()
            and item.appearances
            and (not PregenWorkflow._role_design_item_needs_voice(item) or item.voices)
        )

    @staticmethod
    def _role_design_item_needs_voice(item: RoleDesignItem) -> bool:
        role_tier = str(item.role_tier or "primary").strip().lower()
        if role_tier == "functional" and not item.has_dialogue:
            return False
        return True

    @staticmethod
    def _role_needs_voice(role: Role) -> bool:
        role_tier = str(role.role_tier or "primary").strip().lower()
        if role_tier == "functional" and not role.has_dialogue:
            return False
        return True

    @staticmethod
    def _role_needs_intro_video(role: Role) -> bool:
        return str(role.role_tier or "primary").strip().lower() == "primary"

    def _role_episode_keys(self, item: RoleExtractItem, state: ProjectState) -> list[str]:
        expected = set(self._expected_episode_keys(state))
        keys: list[str] = []
        invalid_keys: list[str] = []
        raw_keys = self._dedupe_texts(item.episode_keys)
        if not raw_keys:
            raise ValueError(
                f"role_extract missing episode_keys for {item.name}; "
                "role_design requires role-scoped episode_keys and will not load all novel_full episodes"
            )
        for key in raw_keys:
            text = str(key or "").strip()
            if not text:
                continue
            if text not in expected:
                invalid_keys.append(text)
                continue
            if text not in keys:
                keys.append(text)
        if invalid_keys:
            get_logger().warning(
                "role_extract ignored invalid episode_keys for %s: %s",
                item.name,
                ", ".join(invalid_keys),
            )
        if keys:
            return keys
        raise ValueError(
            f"role_extract episode_keys for {item.name} do not match existing episodes; "
            f"got {', '.join(raw_keys) or '-'}"
        )

    def _role_design_target_extract_roles(
        self,
        extract_roles: list[RoleExtractItem],
        state: ProjectState,
    ) -> list[RoleExtractItem]:
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        if not active_episode_keys:
            return extract_roles

        active_set = {str(key) for key in active_episode_keys}
        target_roles: list[RoleExtractItem] = []
        for item in extract_roles:
            role_episode_keys = set(self._role_episode_keys(item, state))
            if role_episode_keys.intersection(active_set):
                target_roles.append(item)
        return target_roles

    @staticmethod
    def _format_previous_novel_chapters(novel_full: dict[str, str], episode_keys: list[str]) -> str:
        return ScriptNodeBase.format_previous_novel_chapters(novel_full, episode_keys)

    @staticmethod
    def _load_script_novel_episode_text(path: Path) -> str | None:
        return ScriptContentRepository.load_episode_text(path)

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "bgm_generation",
        force: bool = False,
        only: str | None = None,
        episode_keys: list[str] | None = None,
    ) -> ProjectState:
        if only is not None:
            only = PREGEN_ONLY_ALIASES.get(only, only)
        if until not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen stop node: {until}")
        if only is not None and only not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen only node: {only}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        target_nodes = [only] if only else PREGEN_NODES[: PREGEN_NODES.index(until) + 1]
        selected_episode_keys = self._select_episode_keys(state, episode_keys) if episode_keys else None
        if selected_episode_keys and (len(target_nodes) != 1 or target_nodes[0] not in EPISODE_SCOPED_PREGEN_ONLY_NODES):
            raise ValueError(
                "--episodes is only supported for pregen --only role_design, voice_select, role_voice_generation, "
                "role_full_body_generation, role_multiview_generation, role_intro_video_prompt, "
                "role_intro_video_generation, prop_design, prop_generation, or layout_image_generation. "
                "Use run generation --only storyboard_generation --episodes ... for storyboard shots."
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
        previous_force_pregen = getattr(self, "_force_pregen", None)
        previous_run_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="pregen",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
        )
        self._force_pregen = bool(force)
        if selected_episode_keys is not None:
            self._active_episode_keys = set(selected_episode_keys)
        try:
            node_by_name = {node.name: node for node in build_pregen_nodes(self)}
            state = await self.runner.run_nodes(
                project_dir,
                state,
                [node_by_name[node_name] for node_name in target_nodes],
                force=force,
                skip_completed=only is None,
            )
        finally:
            if selected_episode_keys is not None:
                if previous_active_episode_keys is None:
                    delattr(self, "_active_episode_keys")
                else:
                    self._active_episode_keys = previous_active_episode_keys
            if previous_force_pregen is None:
                if hasattr(self, "_force_pregen"):
                    delattr(self, "_force_pregen")
            else:
                self._force_pregen = previous_force_pregen
            if previous_run_context is None:
                if hasattr(self, "_run_context"):
                    delattr(self, "_run_context")
            else:
                self._run_context = previous_run_context

        logger.info("workflow=pregen completed project_id=%s current_node=%s", state.project_id, state.current_node)
        return state

    def _select_episode_keys(self, state: ProjectState, episode_keys: list[str] | None) -> list[str]:
        return select_episode_keys(state, episode_keys)

    def _script_node_runner(self, node_name: str) -> ScriptNodeBase:
        return build_script_node_runners(self)[node_name]

    async def _run_script_outline(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_outline").run(project_dir, state)

    async def _run_script_novel(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_novel").run(project_dir, state)

    async def _run_script_novel_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_novel_extract").run(project_dir, state)

    @staticmethod
    def _role_name_key(name: object) -> str:
        return str(name or "").strip().casefold()

    def _select_role_design_item(self, output: RoleDesignOutput, extract_item: RoleExtractItem) -> RoleDesignItem:
        if not output.roles:
            raise ValueError(f"role_design returned no roles for {extract_item.name}")

        expected_keys = {self._role_name_key(extract_item.name)}
        expected_keys.update(self._role_name_key(alias) for alias in extract_item.aliases)
        for item in output.roles:
            if self._role_name_key(item.name) in expected_keys:
                return item
            if any(self._role_name_key(alias) in expected_keys for alias in item.aliases):
                return item

        if len(output.roles) == 1:
            item = output.roles[0]
            raise ValueError(
                f"role_design returned a single mismatched role for {extract_item.name}: "
                f"{item.name or '-'}; refusing to relabel it as {extract_item.name}"
            )
        raise ValueError(
            f"role_design must return only {extract_item.name}; got "
            f"{', '.join(item.name for item in output.roles)}"
        )

    def _validate_role_design_item_identity(
        self,
        item: RoleDesignItem,
        extract_item: RoleExtractItem,
        _extract_roles: list[RoleExtractItem],
    ) -> None:
        expected_key = self._role_name_key(extract_item.name)

        if self._role_name_key(item.name) != expected_key:
            raise ValueError(
                f"role_design for {extract_item.name} returned name={item.name or '-'}; "
                "the design identity must match the target role before merging"
            )

        bad_role_fields: list[str] = []
        for appearance in item.appearances:
            role_key = self._role_name_key(appearance.role_name)
            if role_key != expected_key:
                bad_role_fields.append(f"appearances[{appearance.name or 'base'}].role_name={appearance.role_name}")
        for voice in item.voices:
            role_key = self._role_name_key(voice.role_name)
            if role_key != expected_key:
                bad_role_fields.append(f"voices[{voice.emotion or 'normal'}].role_name={voice.role_name}")
        if bad_role_fields:
            raise ValueError(
                f"role_design for {extract_item.name} contains mismatched role_name fields: "
                f"{'; '.join(bad_role_fields)}"
            )

    def _merge_role_extract_into_design(self, item: RoleDesignItem, extract_item: RoleExtractItem) -> RoleDesignItem:
        item.name = extract_item.name
        item.aliases = self._dedupe_texts([*extract_item.aliases, *item.aliases])
        item.role_tier = extract_item.role_tier
        item.has_dialogue = extract_item.has_dialogue
        item.visual_reuse_required = extract_item.visual_reuse_required
        item.importance = item.importance or getattr(extract_item, "importance", None)
        item.episode_keys = self._dedupe_texts([*extract_item.episode_keys, *item.episode_keys])
        item.source_chapters = self._dedupe_texts([*extract_item.source_chapters, *item.source_chapters])
        if not self._role_design_item_needs_voice(item):
            item.voices = []
        for voice in item.voices:
            voice.role_name = extract_item.name
        for appearance in item.appearances:
            appearance.role_name = extract_item.name
        return item

    def _ordered_role_design_items(
        self,
        extract_roles: list[RoleExtractItem],
        designed_by_key: dict[str, RoleDesignItem],
    ) -> list[RoleDesignItem]:
        ordered: list[RoleDesignItem] = []
        emitted: set[str] = set()
        for extract_item in extract_roles:
            key = self._role_name_key(extract_item.name)
            item = designed_by_key.get(key)
            if item is None:
                continue
            ordered.append(item)
            emitted.add(key)
        for key, item in designed_by_key.items():
            if key not in emitted:
                ordered.append(item)
        return ordered

    def _clear_role_design_state(self, state: ProjectState) -> None:
        state.roles = {}
        state.props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if not prop.owner_role_id and prop.source not in {"role_design", "role_appearance_design"}
        }

    def _apply_role_design_item(
        self,
        project_dir: Path,
        state: ProjectState,
        item: RoleDesignItem,
        *,
        speech_provider=None,
        design_path: str | None = None,
        preserve_assets: bool = False,
        validate_voice_sample_text: bool = True,
    ) -> None:
        if not self._role_design_item_complete(item):
            missing: list[str] = []
            if not str(item.intro or "").strip():
                missing.append("intro")
            if not item.appearances:
                missing.append("appearances")
            if self._role_design_item_needs_voice(item) and not item.voices:
                missing.append("voices")
            raise ValueError(f"role_design for {item.name} is missing required fields: {', '.join(missing)}")

        role_id = normalize_id("role", item.name)
        existing_role = state.roles.get(role_id)
        existing_appearances = dict(existing_role.appearances) if existing_role is not None else {}
        existing_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id == role_id
        }
        role = existing_role or Role(
            id=role_id,
            name=item.name,
            intro=item.intro,
        )
        role.name = item.name
        role.intro = item.intro
        role.design_path = design_path or role.design_path
        role.personality = item.personality
        role.role_tier = item.role_tier or role.role_tier
        role.has_dialogue = item.has_dialogue
        role.visual_reuse_required = item.visual_reuse_required
        role.importance = item.importance
        role.aliases = self._dedupe_texts(item.aliases)
        role.episode_keys = self._dedupe_texts(item.episode_keys)
        role.source_chapters = self._dedupe_texts(item.source_chapters)
        role.relationships = [relationship.model_dump(mode="json") for relationship in item.relationships]
        role.appearances = {}
        state.roles[role_id] = role

        state.props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id != role_id
        }

        if self._role_design_item_needs_voice(item):
            self._apply_role_voice_design_output(
                state,
                RoleVoiceDesignOutput(role_voices=item.voices),
                speech_provider=speech_provider,
                preserve_assets=preserve_assets,
                validate_sample_text=validate_voice_sample_text,
                bind_voice_choice=False,
            )
        else:
            role.voice_summary = None
            role.voice_name = None
            role.voice_type = None
            role.voice_resource_id = None
            role.voice_model_family = None
            role.voice_selection_reason = None
            role.audio = {}
        role = state.roles[role_id]

        for appearance_item in item.appearances:
            appearance_name = str(appearance_item.name or "base").strip() or "base"
            appearance_id = normalize_id(f"{role.id}_appearance", appearance_name)
            existing_appearance = existing_appearances.get(appearance_name)
            role_bound_prop_ids: list[str] = []
            for prop_item in appearance_item.role_bound_props:
                prop_id = normalize_id(f"{role.id}_prop", prop_item.name)
                status_key = self._prop_status_key(prop_item.status)
                if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
                    prop_id = f"{prop_id}_{status_key}"
                desc_parts = [prop_item.desc]
                if prop_item.scale_relation:
                    desc_parts.append(f"比例关系：{prop_item.scale_relation}")
                if prop_item.usage:
                    desc_parts.append(f"使用方式：{prop_item.usage}")
                prop = Prop(
                    id=prop_id,
                    name=prop_item.name,
                    desc="；".join(part.strip("；") for part in desc_parts if part),
                    prompt=prop_item.prompt,
                    status=prop_item.status,
                    episode_keys=role.episode_keys,
                    owner_role_id=role.id,
                    owner_role_name=role.name,
                    source="role_design",
                )
                existing_prop = existing_props.get(prop_id)
                if preserve_assets and existing_prop is not None:
                    prop.asset_id = existing_prop.asset_id
                    prop.asset_path = existing_prop.asset_path
                    prop.asset_url = existing_prop.asset_url
                    prop.provider = existing_prop.provider
                    prop.model = existing_prop.model
                    prop.request_id = existing_prop.request_id
                    prop.usage = existing_prop.usage
                prop.design_path = self._save_prop_design_record(
                    project_dir,
                    prop,
                    prompt=prop_item.prompt,
                    node_name="role_design",
                    extra_content={
                        "scale_relation": prop_item.scale_relation,
                        "usage": prop_item.usage,
                    },
                    extra_payload={
                        "source_role_design_path": design_path or self._role_design_relative_path(project_dir, role.id)
                    },
                )
                state.props[prop_id] = prop
                role_bound_prop_ids.append(prop_id)
            appearance = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=appearance_name,
                desc=appearance_item.desc,
                prompt=appearance_item.prompt,
                full_body_prompt=appearance_item.full_body_prompt,
                role_bound_prop_ids=role_bound_prop_ids,
                intro_video_prompt=appearance_item.intro_video_prompt,
            )
            if preserve_assets and existing_appearance is not None:
                appearance.full_body_image_generation_status = existing_appearance.full_body_image_generation_status
                appearance.design_image_generation_status = existing_appearance.design_image_generation_status
                appearance.intro_video_generation_status = existing_appearance.intro_video_generation_status
                appearance.full_body_image_asset_id = existing_appearance.full_body_image_asset_id
                appearance.full_body_image_asset_path = existing_appearance.full_body_image_asset_path
                appearance.full_body_image_asset_url = existing_appearance.full_body_image_asset_url
                appearance.design_image_asset_id = existing_appearance.design_image_asset_id
                appearance.design_image_asset_path = existing_appearance.design_image_asset_path
                appearance.design_image_asset_url = existing_appearance.design_image_asset_url
                appearance.intro_video_asset_id = existing_appearance.intro_video_asset_id
                appearance.intro_video_asset_path = existing_appearance.intro_video_asset_path
                appearance.asset_id = existing_appearance.asset_id
                appearance.asset_path = existing_appearance.asset_path
                appearance.asset_url = existing_appearance.asset_url
                appearance.full_body_provider = existing_appearance.full_body_provider
                appearance.full_body_model = existing_appearance.full_body_model
                appearance.full_body_request_id = existing_appearance.full_body_request_id
                appearance.full_body_usage = existing_appearance.full_body_usage
                appearance.provider = existing_appearance.provider
                appearance.model = existing_appearance.model
                appearance.request_id = existing_appearance.request_id
                appearance.usage = existing_appearance.usage
            role.appearances[appearance_name] = appearance

    def _hydrate_roles_from_design_files(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        speech_provider=None,
    ) -> None:
        for role in list(state.roles.values()):
            item = self._load_role_design_item_for_role(project_dir, role)
            if item is None:
                continue
            self._apply_role_design_item(
                project_dir,
                state,
                item,
                speech_provider=speech_provider,
                design_path=role.design_path or self._role_design_relative_path(project_dir, role.id),
                preserve_assets=True,
                validate_voice_sample_text=False,
            )

    def _role_node_runner(self, node_name: str) -> RoleNodeBase:
        return build_role_node_runners(self)[node_name]

    async def _run_role_extract_primary(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_extract_primary").run(project_dir, state)

    async def _run_role_extract_functional(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_extract_functional").run(project_dir, state)

    async def _run_role_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_extract").run(project_dir, state)

    async def _run_role_episode_key_audit(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_episode_key_audit").run(project_dir, state)

    async def _run_role_duplicate_audit(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_duplicate_audit").run(project_dir, state)

    async def _run_ambient_entity_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("ambient_entity_extract").run(project_dir, state)

    async def _run_role_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_design").run(project_dir, state)

    def _voice_node_runner(self, node_name: str) -> VoiceNodeBase:
        return build_voice_node_runners(self)[node_name]

    async def _run_role_voice_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._voice_node_runner("role_voice_design").run(project_dir, state)

    async def _run_voice_select(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._voice_node_runner("voice_select").run(project_dir, state)

    def _voice_preferred_name(self, state: ProjectState, audio: RoleAudio) -> str:
        digest = hashlib.sha1(f"{state.project_id}:{audio.id}".encode("utf-8")).hexdigest()
        return f"ad_{digest[:13]}"

    def _preview_text(self, role: Role, audio: RoleAudio) -> str:
        return (audio.sample_text or f"我是{role.name}。")[:1024]

    def _voice_prompt(self, role: Role, audio: RoleAudio) -> str:
        return audio.desc or f"{role.name}的{audio.emotion}音色。{role.intro}"

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
        return self.media_store.write_preview_audio(
            project_dir,
            audio_id=audio.id,
            data=data,
            response_format=response_format,
        )

    def _absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return self.layout.absolute_project_path(project_dir, relative_path)

    def _project_relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    def _existing_project_file(self, project_dir: Path, path: str | Path | None) -> str | None:
        return self.layout.existing_project_file(project_dir, path)

    def _shot_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.layout.shot_path(project_dir, episode_key)

    def _load_storyboard_episode(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput:
        return self.storyboards.load(project_dir, episode_key)

    def _save_storyboard_episode(self, project_dir: Path, episode: StoryboardEpisodeOutput) -> None:
        self.storyboards.save(project_dir, episode)

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
        context = getattr(self, "_run_context", None)
        if context is not None and context.selected_episode_keys is not None:
            selected = context.selected_episode_key_set
            return [
                episode_key
                for episode_key in self._expected_episode_keys(state)
                if episode_key in selected
            ]
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        if active_episode_keys is not None:
            return [
                episode_key
                for episode_key in self._expected_episode_keys(state)
                if episode_key in active_episode_keys
            ]
        return self._expected_episode_keys(state)

    def _image_asset_path(self, project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return self.layout.image_asset_path(project_dir, asset_type, asset_id)

    def _music_asset_path(self, project_dir: Path, asset_id: str, audio_format: str | None) -> Path:
        return self.layout.music_asset_path(project_dir, asset_id, audio_format)

    def _audio_asset_path(self, project_dir: Path, asset_type: str, asset_id: str, audio_format: str | None) -> Path:
        return self.layout.audio_asset_path(project_dir, asset_type, asset_id, audio_format)

    def _video_asset_path(self, project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return self.layout.video_asset_path(project_dir, asset_type, asset_id)

    async def _write_first_generated_image(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        return await self.media_store.write_first_generated_image(project_dir, output_path, result)

    async def _write_generated_music(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str:
        return await self.media_store.write_generated_music(project_dir, output_path, result)

    async def _write_generated_audio(
        self,
        project_dir: Path,
        output_path: Path,
        *,
        audio_data: str | None = None,
        audio_url: str | None = None,
    ) -> str | None:
        return await self.media_store.write_generated_audio(
            project_dir,
            output_path,
            audio_data=audio_data,
            audio_url=audio_url,
        )

    async def _write_generated_video(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str | None:
        return await self.media_store.write_generated_video(project_dir, output_path, result)

    async def _generate_designed_voice(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
        role: Role,
        audio: RoleAudio,
    ) -> RoleVoiceGenerationItem:
        node = self._voice_node_runner("role_voice_generation")
        return await node.generate_designed_voice(
            provider=provider,
            project_dir=project_dir,
            state=state,
            role=role,
            audio=audio,
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
        node = self._voice_node_runner("role_voice_generation")
        return await node.generate_cloned_voice(
            provider=provider,
            project_dir=project_dir,
            state=state,
            role=role,
            audio=audio,
            normal_audio=normal_audio,
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
        node = self._voice_node_runner("role_voice_generation")
        return await node.generate_reused_voice(
            provider=provider,
            project_dir=project_dir,
            state=state,
            role=role,
            audio=audio,
            normal_audio=normal_audio,
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
        node = self._voice_node_runner("role_voice_generation")
        return await node.generate_synthesized_voice(
            provider=provider,
            project_dir=project_dir,
            state=state,
            role=role,
            audio=audio,
            voice=voice,
            voice_resource_id=voice_resource_id,
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
                    url=layout.asset_url,
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
                            url=appearance.asset_url or appearance.design_image_asset_url,
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
                        url=prop.asset_url,
                        metadata={"asset_type": "prop", "name": prop.name},
                    )
                    )
        return refs

    def _shot_anchor_video_refs(self, project_dir: Path, state: ProjectState, shot: StoryboardShot) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
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
                if appearance and appearance.intro_video_asset_path:
                    refs.append(
                        AssetRef(
                            id=appearance.intro_video_asset_id or appearance.id,
                            type="video",
                            path=str(project_dir / appearance.intro_video_asset_path),
                            metadata={
                                "asset_type": "role_intro_video",
                                "role_id": role.id,
                                "role_name": role.name,
                                "name": appearance.name,
                            },
                        )
                    )
                    break
        return refs

    def _shot_role_audio_refs(self, project_dir: Path, state: ProjectState, shot: StoryboardShot) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        explicit_audio_ids = {str(value).strip() for value in shot.role_audio_ids if str(value).strip()}
        seen: set[str] = set()

        def append_audio(role: Role, audio: RoleAudio, *, source: str) -> None:
            audio_key = audio.asset_id or audio.id
            if not audio_key or audio_key in seen or not audio.asset_path:
                return
            path = Path(audio.asset_path)
            if not path.is_absolute():
                path = project_dir / path
            if not path.exists() or not path.is_file():
                return
            seen.add(audio_key)
            refs.append(
                AssetRef(
                    id=audio_key,
                    type="audio",
                    path=str(path),
                    metadata={
                        "asset_type": "role_audio",
                        "reference_source": source,
                        "role_id": role.id,
                        "role_name": role.name,
                        "emotion": audio.emotion,
                        "voice": audio.voice_type,
                        "voice_name": audio.voice_name,
                    },
                )
            )

        if explicit_audio_ids:
            for role in state.roles.values():
                for audio in role.audio.values():
                    if audio.id in explicit_audio_ids or (audio.asset_id and audio.asset_id in explicit_audio_ids):
                        append_audio(role, audio, source="storyboard_role_audio_ids")
            return refs

        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            audio = role.audio.get("normal") or next((item for item in role.audio.values() if item.asset_path), None)
            if audio is not None:
                append_audio(role, audio, source="shot_role_ids")
        return refs

    @staticmethod
    def _video_reference_mode(provider=None) -> str:
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        mode = (
            options.get("video_reference_mode")
            or options.get("seedance_video_reference_mode")
            or options.get("video_refs_mode")
            or "full"
        )
        return str(mode).strip().lower().replace("-", "_")

    @staticmethod
    def _previous_shot(episode: StoryboardEpisodeOutput | None, shot: StoryboardShot) -> StoryboardShot | None:
        if episode is None:
            return None
        previous = [
            item
            for item in episode.shots
            if int(item.index) < int(shot.index)
        ]
        if not previous:
            return None
        return sorted(previous, key=lambda item: int(item.index))[-1]

    def _shot_video_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: StoryboardShot,
        provider=None,
        episode: StoryboardEpisodeOutput | None = None,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        previous_shot = self._previous_shot(episode, shot)
        if shot.start_frame_source == "previous_shot_last_frame":
            if previous_shot is None:
                raise ValueError(
                    f"{shot.shot_id} start_frame_source=previous_shot_last_frame but no previous shot exists"
                )
            if not previous_shot.video_last_frame_asset_path:
                raise ValueError(
                    f"{shot.shot_id} start_frame_source=previous_shot_last_frame but "
                    f"{previous_shot.shot_id} has no saved last frame; generate the previous shot video first"
                )
            last_frame_path = project_dir / previous_shot.video_last_frame_asset_path
            if not last_frame_path.exists() or not last_frame_path.is_file():
                raise ValueError(
                    f"{shot.shot_id} start_frame_source=previous_shot_last_frame but "
                    f"{previous_shot.shot_id} last frame is missing: {previous_shot.video_last_frame_asset_path}"
                )
            refs.append(
                AssetRef(
                    id=f"{previous_shot.video_asset_id or previous_shot.shot_id}_last_frame",
                    type="image",
                    path=str(last_frame_path),
                    metadata={
                        "asset_type": "previous_shot_last_frame",
                        "previous_shot_id": previous_shot.shot_id,
                        "seedance_role": "first_frame",
                    },
                )
            )
            return refs
        if shot.ref_frame_asset_path or shot.ref_frame_asset_url:
            ref_frame_path = str(project_dir / shot.ref_frame_asset_path) if shot.ref_frame_asset_path else None
            refs.append(
                AssetRef(
                    id=shot.ref_frame_asset_id,
                    type="image",
                    path=ref_frame_path,
                    url=shot.ref_frame_asset_url,
                    metadata={
                        "asset_type": "ref_frame",
                        "reference_source": "seedream_url" if shot.ref_frame_asset_url else "local_file",
                    },
                )
            )
        if self._video_reference_mode(provider) not in {"ref_frame_only", "ref_frame"}:
            refs.extend(self._shot_ref_asset_refs(project_dir, state, shot))
            refs.extend(self._shot_anchor_video_refs(project_dir, state, shot))
        dialogue_audio_role_ids: set[str] = set()
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
                if audio.role_id:
                    dialogue_audio_role_ids.add(audio.role_id)
        role_audio_refs = self._shot_role_audio_refs(project_dir, state, shot)
        if not shot.role_audio_ids:
            role_audio_refs = [
                ref
                for ref in role_audio_refs
                if not ref.metadata.get("role_id") or ref.metadata.get("role_id") not in dialogue_audio_role_ids
            ]
        refs.extend(role_audio_refs)
        return refs

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
        content = f"{shot.title} {shot.video_prompt} {' '.join(shot.dialogue)}"
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
        node = self._voice_node_runner("role_voice_generation")
        return await node.run_synthesis_generation(provider=provider, project_dir=project_dir, state=state)

    async def _run_role_voice_design_clone_generation(
        self,
        *,
        provider,
        project_dir: Path,
        state: ProjectState,
    ) -> ProjectState:
        node = self._voice_node_runner("role_voice_generation")
        return await node.run_design_clone_generation(provider=provider, project_dir=project_dir, state=state)

    async def _run_role_voice_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._voice_node_runner("role_voice_generation").run(project_dir, state)

    def _static_asset_node_runner(self, node_name: str) -> StaticAssetNodeBase:
        return build_static_asset_node_runners(self)[node_name]

    async def _run_role_appearance_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_appearance_design").run(project_dir, state)

    async def _run_role_appearance_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_appearance_generation").run(project_dir, state)

    async def _run_role_full_body_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_full_body_generation").run(project_dir, state)

    async def _run_role_multiview_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_multiview_generation").run(project_dir, state)

    async def _run_role_intro_video_prompt(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_intro_video_prompt").run(project_dir, state)

    async def _run_role_intro_video_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("role_intro_video_generation").run(project_dir, state)

    def _prop_design_json_path(self, project_dir: Path, prop_id: str) -> Path:
        return self.prop_designs.item_path(project_dir, prop_id)

    def _prop_design_relative_path(self, project_dir: Path, prop_id: str) -> str:
        return self.prop_designs.item_relative_path(project_dir, prop_id)

    def _prop_episode_keys(self, name: str, episode_keys: list[str], state: ProjectState) -> list[str]:
        expected_keys = self._expected_episode_keys(state)
        expected = set(expected_keys)
        cleaned = self._dedupe_texts(episode_keys)
        invalid = [episode_key for episode_key in cleaned if episode_key not in expected]
        if invalid:
            raise ValueError(
                f"prop_design generated invalid episode_keys for {name}: "
                f"{', '.join(invalid)}; expected one of {', '.join(expected_keys)}"
            )
        if not cleaned:
            raise ValueError(f"prop_design must include episode_keys for {name}")
        selected = set(cleaned)
        return [episode_key for episode_key in expected_keys if episode_key in selected]

    def _save_prop_design_record(
        self,
        project_dir: Path,
        prop: Prop,
        *,
        prompt: str,
        node_name: str,
        extra_content: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        return self.prop_designs.save_record(
            project_dir,
            prop,
            prompt=prompt,
            node_name=node_name,
            extra_content=extra_content,
            extra_payload=extra_payload,
        )

    def _load_prop_design_content(self, project_dir: Path, prop: Prop) -> dict[str, Any]:
        return self.prop_designs.load_content(project_dir, prop)

    def _prop_prompt_for_generation(self, project_dir: Path, prop: Prop) -> str:
        prompt = str(prop.prompt or "").strip()
        if prompt:
            return prompt
        content = self._load_prop_design_content(project_dir, prop)
        prompt = str(content.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Prop {prop.id} is missing prompt; expected it in state or {prop.design_path}")
        prop.prompt = prompt
        return prompt

    def _update_prop_design_image_result(self, project_dir: Path, prop: Prop, result, asset_path: str) -> None:
        self.prop_designs.update_image_result(project_dir, prop, result, asset_path)

    async def _run_prop_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_extract").run(project_dir, state)

    async def _run_prop_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_design").run(project_dir, state)

    @staticmethod
    def _prop_status_key(value: object) -> str:
        status = str(value or "normal").strip().lower()
        return slugify(status, fallback="normal").lower() or "normal"

    @classmethod
    def _prop_asset_id(cls, name: str, status: object) -> str:
        prop_id = normalize_id("prop", name)
        status_key = cls._prop_status_key(status)
        if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
            prop_id = f"{prop_id}_{status_key}"
        return prop_id

    @classmethod
    def _prop_variant_base_name(cls, prop: Prop) -> str:
        name = str(prop.name).strip()
        status_key = cls._prop_status_key(prop.status)
        suffixes = [status_key]
        if status_key != "normal":
            suffixes.append("normal")
        for suffix in suffixes:
            for separator in ("_", "-"):
                marker = f"{separator}{suffix}"
                if name.lower().endswith(marker) and len(name) > len(marker):
                    return name[: -len(marker)].strip() or name
        return name

    @classmethod
    def _prop_variant_base_key(cls, prop: Prop) -> str:
        return normalize_id("prop", cls._prop_variant_base_name(prop))

    @classmethod
    def _ordered_props_for_generation(cls, props: list[Prop]) -> list[Prop]:
        normal_base_keys = {
            cls._prop_variant_base_key(prop)
            for prop in props
            if cls._prop_status_key(prop.status) == "normal"
        }
        indexed = list(enumerate(props))
        ordered = sorted(
            indexed,
            key=lambda item: (
                1
                if cls._prop_status_key(item[1].status) != "normal"
                and cls._prop_variant_base_key(item[1]) in normal_base_keys
                else 0,
                item[0],
            ),
        )
        return [prop for _, prop in ordered]

    @classmethod
    def _normal_props_by_variant_base(cls, props: list[Prop]) -> dict[str, Prop]:
        normal_props: dict[str, Prop] = {}
        for prop in props:
            if cls._prop_status_key(prop.status) != "normal":
                continue
            normal_props.setdefault(cls._prop_variant_base_key(prop), prop)
        return normal_props

    @classmethod
    def _prop_reference_refs(
        cls,
        project_dir: Path,
        prop: Prop,
        normal_props_by_base: dict[str, Prop],
    ) -> list | None:
        if cls._prop_status_key(prop.status) == "normal":
            return None
        normal_prop = normal_props_by_base.get(cls._prop_variant_base_key(prop))
        if normal_prop is None or normal_prop is prop or not normal_prop.asset_path:
            return None
        reference_path = project_dir / normal_prop.asset_path
        if not reference_path.exists() or not reference_path.is_file():
            raise ValueError(
                f"Cannot use normal prop reference for {prop.id}: missing file {normal_prop.asset_path}"
            )

        from autodrama.providers.base import AssetRef

        return [
            AssetRef(
                id=normal_prop.asset_id or normal_prop.id,
                type="image",
                path=str(reference_path),
                url=normal_prop.asset_url,
                metadata={
                    "asset_type": "prop",
                    "name": normal_prop.name,
                    "status": normal_prop.status,
                    "reference_for": prop.id,
                },
            )
        ]

    @staticmethod
    def _role_bound_prop_reference_refs(project_dir: Path, state: ProjectState, prop: Prop) -> list | None:
        if not prop.owner_role_id:
            return None
        role = state.roles.get(prop.owner_role_id)
        if role is None:
            return None
        appearance = next(
            (
                item
                for item in role.appearances.values()
                if prop.id in item.role_bound_prop_ids and item.design_image_asset_path
            ),
            None,
        )
        if appearance is None or not appearance.design_image_asset_path:
            return None
        reference_path = project_dir / appearance.design_image_asset_path
        if not reference_path.exists() or not reference_path.is_file():
            raise ValueError(
                f"Cannot use role appearance reference for {prop.id}: missing file {appearance.design_image_asset_path}"
            )

        from autodrama.providers.base import AssetRef

        return [
            AssetRef(
                id=appearance.design_image_asset_id or appearance.id,
                type="image",
                path=str(reference_path),
                url=appearance.design_image_asset_url or appearance.asset_url,
                metadata={
                    "asset_type": "role_appearance",
                    "role_id": role.id,
                    "role_name": role.name,
                    "reference_for": prop.id,
                },
            )
        ]

    async def _run_prop_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_generation").run(project_dir, state)

    async def _run_prop_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._run_prop_generation(project_dir, state)

    async def _run_layout_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_extract").run(project_dir, state)

    async def _run_layout_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_design").run(project_dir, state)

    async def _run_layout_dedupe_review(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_dedupe_review").run(project_dir, state)

    async def _run_layout_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_image_generation").run(project_dir, state)

    def _bgm_node_runner(self, node_name: str) -> BGMNodeBase:
        return build_bgm_node_runners(self)[node_name]

    async def _run_bgm_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._bgm_node_runner("bgm_design").run(project_dir, state)

    async def _run_bgm_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._bgm_node_runner("bgm_generation").run(project_dir, state)
