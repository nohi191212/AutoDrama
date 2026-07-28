from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from autodrama.config import Settings
from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    RoleExtractItem,
    RoleExtractOutput,
    RoleboardPromptItem,
    RoleboardPromptOutput,
    ShotDialogueAudioAsset,
    ShotManifestEpisodeOutput,
    ShotManifestItem,
)
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.prop_design_repo import PropDesignRepository
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.shot_manifest_repo import ShotManifestRepository
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.roleboard_prompt_repo import RoleboardPromptRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.asset_service import AssetService
from autodrama.services.audio_duration import (
    AudioDurationLimitResult,
    audio_duration_limit_metadata,
    compact_tts_text,
    limit_audio_duration,
    provider_max_generated_audio_duration_seconds,
)
from autodrama.services.director_service import DirectorService
from autodrama.services.media_store import MediaStore
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.nodes import (
    AVAILABLE_PREGEN_NODE_NAMES,
    IMAGE_AUDIT_NODE_NAMES,
    PREGEN_NODE_NAMES,
    build_manual_pregen_nodes,
    build_pregen_nodes,
)
from autodrama.workflows.nodes.bgm_nodes import BGMNodeBase, build_bgm_node_runners
from autodrama.workflows.nodes.script_nodes import (
    ScriptNodeBase,
    build_script_node_runners,
)
from autodrama.workflows.nodes.director_nodes import DirectorNodeBase, build_director_node_runners
from autodrama.workflows.nodes.role_nodes import RoleNodeBase, build_role_node_runners
from autodrama.workflows.nodes.static_asset_nodes import (
    StaticAssetNodeBase,
    build_static_asset_node_runners,
)
from autodrama.workflows.nodes.voice_nodes import VoiceNodeBase, build_voice_node_runners
from autodrama.workflows.router_adapter import adapt_workflow_router
from autodrama.workflows.runner import WorkflowRunner
from autodrama.workflows.selection import normalize_clip_selectors, normalize_shot_selectors, select_episode_keys


PREGEN_NODES = PREGEN_NODE_NAMES
PREGEN_ONLY_NODES = AVAILABLE_PREGEN_NODE_NAMES
EPISODE_SCOPED_PREGEN_ONLY_NODES = {
    "clip_segment",
    "roleboard_prompt",
    "roleboard_image_generation",
    "role_subject_frontal_image_generation",
    "role_subject_video_generation",
    "role_kling_voice_generation",
    "role_subject_element_generation",
    "clip_to_shots",
    "layout_to_background_prompt",
    "shot_background_image_generation",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_manifest_generation",
    "role_voice_select",
    "prop_prompt",
    "prop_image_generation",
    "layout_image_generation",
}
ROLE_SCOPED_PREGEN_ONLY_NODES = {
    "role_voice_select",
    "role_kling_voice_generation",
}
CLIP_SCOPED_PREGEN_ONLY_NODES = {
    "clip_to_shots",
}
SHOT_SCOPED_PREGEN_ONLY_NODES = {
    "layout_to_background_prompt",
    "shot_background_image_generation",
    "shot_background_image_audit",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_keyframe_image_audit",
    "shot_manifest_generation",
}
ASSET_SCOPED_PREGEN_ONLY_NODES = {
    "key_vision_image_audit",
    "roleboard_image_generation",
    "roleboard_image_audit",
    "role_subject_frontal_image_generation",
    "role_subject_frontal_image_audit",
    "prop_image_generation",
    "layout_image_generation",
    "prop_image_audit",
    "layout_image_audit",
}


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
        self.router = adapt_workflow_router(router)
        self.prompts = prompts or PromptStore()
        self.script_service = ScriptService(self.prompts)
        self.director_service = DirectorService(self.prompts)
        self.role_service = RoleService(self.prompts)
        self.asset_service = AssetService(self.prompts)
        self.script_contents = ScriptContentRepository(self.repo, self.layout)
        self.roleboard_prompts = RoleboardPromptRepository(self.repo, self.layout)
        self.prop_designs = PropDesignRepository(self.repo, self.layout)
        self.media_store = MediaStore(
            self.layout,
            timeout_seconds=self.settings.runtime.request_timeout_seconds,
        )
        self.shot_manifests = ShotManifestRepository(self.repo, self.layout)
        self.runner = WorkflowRunner(repo=self.repo, logger=get_logger())

    def _expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.state_episode_keys(state)

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
        return compact_tts_text(
            f"我是{role.name}。{intro}。面对眼前的问题，我会保持冷静。",
            max_chars=40,
        )

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

    def _ensure_role_voice_audio_if_needed(self, state: ProjectState) -> None:
        if all(
            not self._role_needs_voice(role) or ("normal" in role.audio and role.voice_type)
            for role in state.roles.values()
        ):
            return

        for role in state.roles.values():
            if self._role_needs_voice(role):
                self._ensure_normal_role_audio(role)

    def _apply_script_plan_settings(self, state: ProjectState) -> None:
        state.metadata["episode_count"] = self.repo.settings.project.episode_count
        state.metadata["episode_duration_seconds"] = self.repo.settings.project.episode_duration_seconds
        state.metadata["bgm_count"] = self.repo.settings.project.bgm_count
        state.metadata["visual_style_prompt"] = self.repo.settings.generation.visual_style_prompt
        state.metadata["roleboard_style_prompt"] = self.repo.settings.generation.roleboard_style_prompt
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
        return self.roleboard_prompts.load_extract_output(project_dir)

    def _load_existing_roleboard_prompt_output(self, project_dir: Path) -> RoleboardPromptOutput | None:
        return self.roleboard_prompts.load_existing_output(project_dir)

    def _roleboard_prompt_json_path(self, project_dir: Path, role_id: str) -> Path:
        return self.roleboard_prompts.item_path(project_dir, role_id)

    def _roleboard_prompt_relative_path(self, project_dir: Path, role_id: str) -> str:
        return self.roleboard_prompts.item_relative_path(project_dir, role_id)

    @staticmethod
    def _load_roleboard_prompt_item(path: Path) -> RoleboardPromptItem:
        return RoleboardPromptRepository.load_item(path)

    def _load_roleboard_prompt_item_for_role(self, project_dir: Path, role: Role) -> RoleboardPromptItem | None:
        return self.roleboard_prompts.load_item_for_role(project_dir, role)

    @staticmethod
    def _role_needs_voice(role: Role) -> bool:
        role_tier = str(role.role_tier or "primary").strip().lower()
        if role_tier == "functional" and not role.has_dialogue:
            return False
        return True

    def _role_episode_keys(self, item: RoleExtractItem, state: ProjectState) -> list[str]:
        expected = set(self._expected_episode_keys(state))
        keys: list[str] = []
        invalid_keys: list[str] = []
        raw_keys = self._dedupe_texts(item.episode_keys)
        if not raw_keys:
            raise ValueError(
                f"role_finalize missing episode_keys for {item.name}; "
                "roleboard_prompt requires role-scoped episode_keys and will not load all novel_full episodes"
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
                "role_finalize ignored invalid episode_keys for %s: %s",
                item.name,
                ", ".join(invalid_keys),
            )
        if keys:
            return keys
        raise ValueError(
            f"role_finalize episode_keys for {item.name} do not match existing episodes; "
            f"got {', '.join(raw_keys) or '-'}"
        )

    def _roleboard_prompt_target_extract_roles(
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

    def _ordered_roleboard_prompt_items(
        self,
        extract_roles: list[RoleExtractItem],
        prompt_by_key: dict[str, RoleboardPromptItem],
    ) -> list[RoleboardPromptItem]:
        ordered: list[RoleboardPromptItem] = []
        emitted: set[str] = set()
        for extract_item in extract_roles:
            appearance_names = [str(asset.name or "base").strip() or "base" for asset in extract_item.appearance_assets]
            if not appearance_names:
                appearance_names = ["base"]
            base_seen = any(name.casefold() == "base" for name in appearance_names)
            if extract_item.appearance_assets and not base_seen:
                appearance_names.insert(0, "base")
            for appearance_name in appearance_names:
                key = self._role_appearance_key(extract_item.name, appearance_name)
                item = prompt_by_key.get(key)
                if item is None:
                    continue
                ordered.append(item)
                emitted.add(key)
        for key, item in prompt_by_key.items():
            if key not in emitted:
                ordered.append(item)
        return ordered

    def _clear_roleboard_prompt_state(self, state: ProjectState) -> None:
        state.roles = {}
        state.props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if not prop.owner_role_id
        }

    def _apply_roleboard_prompt_item(
        self,
        state: ProjectState,
        item: RoleboardPromptItem,
        *,
        prompt_path: str | None = None,
        preserve_assets: bool = False,
    ) -> None:
        existing_role = state.roles.get(item.role_id)
        existing_appearance = None
        if existing_role is not None:
            existing_appearance = existing_role.appearances.get(item.appearance_name)
        role = existing_role or Role(
            id=item.role_id,
            name=item.role_name,
            intro=item.role_brief or item.role_name,
        )
        role.name = item.role_name
        role.intro = item.role_brief or role.intro
        role.design_path = prompt_path or role.design_path
        role.role_tier = item.role_tier or role.role_tier
        role.has_dialogue = item.has_dialogue
        role.visual_reuse_required = item.visual_reuse_required
        role.episode_keys = self._dedupe_texts([*role.episode_keys, *item.episode_keys])
        role.source_chapters = self._dedupe_texts([*role.source_chapters, *item.source_chapters])
        role.voice_summary = item.voice_profile_prompt or role.voice_summary
        appearance = RoleAppearance(
            id=item.appearance_id,
            role_id=item.role_id,
            name=item.appearance_name,
            asset_role=item.asset_role,
            reference_asset_name=item.reference_asset_name,
            episode_keys=self._dedupe_texts(item.episode_keys),
            source_chapters=self._dedupe_texts(item.source_chapters),
            clothing=item.clothing,
            visual_features=item.visual_features,
            desc=item.appearance_desc,
            prompt=item.roleboard_prompt,
            roleboard_prompt=item.roleboard_prompt,
            roleboard_negative_prompt=item.roleboard_negative_prompt,
            voice_profile_prompt=item.voice_profile_prompt,
        )
        if preserve_assets and existing_appearance is not None:
            appearance.design_image_generation_status = existing_appearance.design_image_generation_status
            appearance.design_image_asset_id = existing_appearance.design_image_asset_id
            appearance.design_image_asset_path = existing_appearance.design_image_asset_path
            appearance.design_image_asset_url = existing_appearance.design_image_asset_url
            appearance.asset_id = existing_appearance.asset_id
            appearance.asset_path = existing_appearance.asset_path
            appearance.asset_url = existing_appearance.asset_url
            appearance.provider = existing_appearance.provider
            appearance.model = existing_appearance.model
            appearance.request_id = existing_appearance.request_id
            appearance.usage = existing_appearance.usage
            appearance.subject_frontal_image_asset_id = existing_appearance.subject_frontal_image_asset_id
            appearance.subject_frontal_image_asset_path = existing_appearance.subject_frontal_image_asset_path
            appearance.subject_frontal_image_asset_url = existing_appearance.subject_frontal_image_asset_url
            appearance.subject_frontal_image_provider = existing_appearance.subject_frontal_image_provider
            appearance.subject_frontal_image_model = existing_appearance.subject_frontal_image_model
            appearance.subject_frontal_image_request_id = existing_appearance.subject_frontal_image_request_id
            appearance.subject_frontal_image_usage = existing_appearance.subject_frontal_image_usage
            appearance.subject_frontal_image_raw_response = existing_appearance.subject_frontal_image_raw_response
            appearance.subject_video_asset_id = existing_appearance.subject_video_asset_id
            appearance.subject_video_asset_path = existing_appearance.subject_video_asset_path
            appearance.subject_video_asset_url = existing_appearance.subject_video_asset_url
            appearance.subject_video_intro_text = existing_appearance.subject_video_intro_text
            appearance.subject_video_provider = existing_appearance.subject_video_provider
            appearance.subject_video_model = existing_appearance.subject_video_model
            appearance.subject_video_task_id = existing_appearance.subject_video_task_id
            appearance.subject_video_task_status = existing_appearance.subject_video_task_status
            appearance.subject_video_request_id = existing_appearance.subject_video_request_id
            appearance.subject_video_usage = existing_appearance.subject_video_usage
            appearance.subject_video_raw_response = existing_appearance.subject_video_raw_response
            appearance.subject_element_provider = existing_appearance.subject_element_provider
            appearance.subject_element_model = existing_appearance.subject_element_model
            appearance.subject_element_reference_type = existing_appearance.subject_element_reference_type
            appearance.subject_element_id = existing_appearance.subject_element_id
            appearance.subject_element_task_id = existing_appearance.subject_element_task_id
            appearance.subject_element_task_status = existing_appearance.subject_element_task_status
            appearance.subject_element_request_id = existing_appearance.subject_element_request_id
            appearance.subject_element_usage = existing_appearance.subject_element_usage
            appearance.subject_element_raw_response = existing_appearance.subject_element_raw_response
        role.appearances[item.appearance_name] = appearance
        state.roles[item.role_id] = role
        if self._role_needs_voice(role):
            self._ensure_normal_role_audio(role)
            if item.voice_profile_prompt and "normal" in role.audio:
                role.audio["normal"].desc = item.voice_profile_prompt
                role.voice_summary = item.voice_profile_prompt
        else:
            role.audio = {}
            role.voice_summary = None

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
        until: str = "shot_manifest_generation",
        force: bool = False,
        only: str | None = None,
        episode_keys: list[str] | None = None,
        role_names: list[str] | None = None,
        clip_selectors: list[str] | None = None,
        shot_selectors: list[str] | None = None,
        asset_ids: list[str] | None = None,
    ) -> ProjectState:

        if only is not None and only not in PREGEN_ONLY_NODES:
            raise ValueError(f"Unsupported pregen only node: {only}")
        if only is None and until not in PREGEN_NODES:
            if until in PREGEN_ONLY_NODES:
                raise ValueError(
                    f"pregen stop node {until} is deferred from the default pregen chain; "
                    f"use pregen --only {until} to run it manually."
                )
            raise ValueError(f"Unsupported pregen stop node: {until}")

        logger = setup_logging(project_dir)
        self.router.set_prompt_audit_project_dir(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        target_nodes = [only] if only else PREGEN_NODES[: PREGEN_NODES.index(until) + 1]
        if only is None and not self.settings.app.enable_image_audit:
            target_nodes = [node_name for node_name in target_nodes if node_name not in IMAGE_AUDIT_NODE_NAMES]
        if only is None and not self.settings.app.enable_llm_audit:
            target_nodes = [node_name for node_name in target_nodes if node_name != "layout_prop_boundary_review"]
        selected_episode_keys = self._select_episode_keys(state, episode_keys) if episode_keys else None
        selected_role_names = self._select_role_names(role_names) if role_names else None
        selected_clip_selectors = normalize_clip_selectors(clip_selectors) if clip_selectors else set()
        selected_shot_selectors = normalize_shot_selectors(shot_selectors) if shot_selectors else set()
        selected_asset_ids = {str(item).strip() for item in asset_ids or [] if str(item).strip()}
        if selected_episode_keys and (len(target_nodes) != 1 or target_nodes[0] not in EPISODE_SCOPED_PREGEN_ONLY_NODES):
            raise ValueError(
                "--episodes is only supported for pregen --only clip_segment, roleboard_prompt, roleboard_image_generation, "
                "clip_to_shots, layout_to_background_prompt, shot_background_image_generation, shot_keyframe_prompt, shot_keyframe_image_generation, shot_manifest_generation, "
                "role_subject_frontal_image_generation, role_kling_voice_generation, role_subject_video_generation, "
                "role_subject_element_generation, role_voice_select, prop_prompt, prop_image_generation, "
                "or layout_image_generation."
            )
        if selected_role_names and (len(target_nodes) != 1 or target_nodes[0] not in ROLE_SCOPED_PREGEN_ONLY_NODES):
            raise ValueError(
                "--roles is only supported for pregen --only role_voice_select or role_kling_voice_generation."
            )
        if selected_clip_selectors and (
            len(target_nodes) != 1
            or target_nodes[0]
            not in CLIP_SCOPED_PREGEN_ONLY_NODES
        ):
            raise ValueError(
                "--clips is only supported for pregen --only clip_to_shots."
            )
        if selected_shot_selectors and (len(target_nodes) != 1 or target_nodes[0] not in SHOT_SCOPED_PREGEN_ONLY_NODES):
            raise ValueError("--shots is only supported for pregen --only layout_to_background_prompt, shot_background_image_generation, shot_keyframe_prompt, shot_keyframe_image_generation, or shot_manifest_generation.")
        if selected_asset_ids and (len(target_nodes) != 1 or target_nodes[0] not in ASSET_SCOPED_PREGEN_ONLY_NODES):
            raise ValueError("--assets is only supported for pregen --only roleboard_image_generation, role_subject_frontal_image_generation, prop_image_generation, layout_image_generation, prop_image_audit, or layout_image_audit.")
        logger.info(
            "workflow=pregen project_id=%s until=%s only=%s force=%s episodes=%s roles=%s clips=%s assets=%s completed=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys or []) or "-",
            ",".join(selected_role_names or []) or "-",
            ",".join(sorted(selected_clip_selectors)) or "-",
            ",".join(sorted(selected_asset_ids)) or "-",
            ",".join(state.completed_nodes) or "-",
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        previous_active_role_names = getattr(self, "_active_role_names", None)
        previous_active_clip_selectors = getattr(self, "_active_clip_selectors", None)
        previous_active_shot_selectors = getattr(self, "_active_shot_selectors", None)
        previous_active_asset_ids = getattr(self, "_active_asset_ids", None)
        previous_force_pregen = getattr(self, "_force_pregen", None)
        previous_run_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="pregen",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
            selected_role_names=selected_role_names,
            clip_selectors=selected_clip_selectors,
            shot_selectors=selected_shot_selectors,
        )
        self._force_pregen = bool(force)
        if selected_episode_keys is not None:
            self._active_episode_keys = set(selected_episode_keys)
        if selected_role_names is not None:
            self._active_role_names = list(selected_role_names)
        if selected_clip_selectors:
            self._active_clip_selectors = set(selected_clip_selectors)
        elif hasattr(self, "_active_clip_selectors"):
            delattr(self, "_active_clip_selectors")
        if selected_shot_selectors:
            self._active_shot_selectors = set(selected_shot_selectors)
        elif hasattr(self, "_active_shot_selectors"):
            delattr(self, "_active_shot_selectors")
        if selected_asset_ids:
            self._active_asset_ids = set(selected_asset_ids)
        elif hasattr(self, "_active_asset_ids"):
            delattr(self, "_active_asset_ids")
        try:
            node_by_name = {
                node.name: node
                for node in [
                    *build_pregen_nodes(self),
                    *build_manual_pregen_nodes(self),
                ]
            }
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
            if selected_role_names is not None:
                if previous_active_role_names is None:
                    delattr(self, "_active_role_names")
                else:
                    self._active_role_names = previous_active_role_names
            if previous_active_clip_selectors is None:
                if hasattr(self, "_active_clip_selectors"):
                    delattr(self, "_active_clip_selectors")
            else:
                self._active_clip_selectors = previous_active_clip_selectors
            if previous_active_shot_selectors is None:
                if hasattr(self, "_active_shot_selectors"):
                    delattr(self, "_active_shot_selectors")
            else:
                self._active_shot_selectors = previous_active_shot_selectors
            if previous_active_asset_ids is None:
                if hasattr(self, "_active_asset_ids"):
                    delattr(self, "_active_asset_ids")
            else:
                self._active_asset_ids = previous_active_asset_ids
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

    @staticmethod
    def _select_role_names(role_names: list[str] | None) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for role_name in role_names or []:
            value = str(role_name).strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            selected.append(value)
            seen.add(key)
        return selected

    def _script_node_runner(self, node_name: str) -> ScriptNodeBase:
        return build_script_node_runners(self)[node_name]

    async def _run_script_import(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_import").run(project_dir, state)

    async def _run_script_detail_expand(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_detail_expand").run(project_dir, state)

    async def _run_script_outline(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_outline").run(project_dir, state)

    async def _run_script_novel(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_novel").run(project_dir, state)

    def _director_node_runner(self, node_name: str) -> DirectorNodeBase:
        return build_director_node_runners(self)[node_name]

    async def _run_script_novel_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._script_node_runner("script_novel_extract").run(project_dir, state)

    @staticmethod
    def _role_name_key(name: object) -> str:
        return str(name or "").strip().casefold()

    @staticmethod
    def _role_appearance_key(role_name: object, appearance_name: object) -> str:
        return f"{str(role_name or '').strip().casefold()}::{str(appearance_name or '').strip().casefold()}"

    def _hydrate_roles_from_design_files(
        self,
        project_dir: Path,
        state: ProjectState,
    ) -> None:
        for role in list(state.roles.values()):
            items = self.roleboard_prompts.load_items_for_role(project_dir, role)
            if not items:
                continue
            for item in items:
                self._apply_roleboard_prompt_item(
                    state,
                    item,
                    prompt_path=role.design_path or self._roleboard_prompt_relative_path(project_dir, role.id),
                    preserve_assets=True,
                )

    def _role_node_runner(self, node_name: str) -> RoleNodeBase:
        return build_role_node_runners(self)[node_name]

    async def _run_role_extract_primary(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_extract_primary").run(project_dir, state)

    async def _run_role_extract_functional(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_extract_functional").run(project_dir, state)

    async def _run_role_finalize(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("role_finalize").run(project_dir, state)

    async def _run_roleboard_prompt(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._role_node_runner("roleboard_prompt").run(project_dir, state)

    def _voice_node_runner(self, node_name: str) -> VoiceNodeBase:
        return build_voice_node_runners(self)[node_name]

    async def _run_role_voice_select(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._voice_node_runner("role_voice_select").run(project_dir, state)

    def _absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return self.layout.absolute_project_path(project_dir, relative_path)

    def _project_relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    def _existing_project_file(self, project_dir: Path, path: str | Path | None) -> str | None:
        return self.layout.existing_project_file(project_dir, path)

    def _shot_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.layout.shot_path(project_dir, episode_key)

    def _load_shot_manifest(self, project_dir: Path, episode_key: str) -> ShotManifestEpisodeOutput:
        return self.shot_manifests.load(project_dir, episode_key)

    def _save_shot_manifest(self, project_dir: Path, episode: ShotManifestEpisodeOutput) -> None:
        self.shot_manifests.save(project_dir, episode)

    def _iter_shot_manifest_episodes(
        self,
        project_dir: Path,
        state: ProjectState,
    ) -> list[ShotManifestEpisodeOutput]:
        return [
            self._load_shot_manifest(project_dir, episode_key)
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

    def _limit_generated_audio_duration(
        self,
        project_dir: Path,
        asset_path: str | None,
        *,
        provider: object,
    ) -> AudioDurationLimitResult:
        max_duration_seconds = provider_max_generated_audio_duration_seconds(provider)
        if not asset_path:
            return AudioDurationLimitResult(
                max_duration_seconds=max_duration_seconds,
                skipped_reason="audio_path_missing",
            )
        path = Path(asset_path)
        if not path.is_absolute():
            path = project_dir / path
        result = limit_audio_duration(
            path,
            max_duration_seconds=max_duration_seconds,
            ffmpeg_path=self.settings.runtime.ffmpeg_path,
        )
        if result.trimmed:
            get_logger().info(
                "trimmed generated audio %s from %.3fs to %.3fs",
                asset_path,
                result.original_duration_seconds or 0.0,
                result.duration_seconds or result.max_duration_seconds or 0.0,
            )
        return result

    async def _write_generated_video(
        self,
        project_dir: Path,
        output_path: Path,
        result,
    ) -> str | None:
        return await self.media_store.write_generated_video(project_dir, output_path, result)

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

    def _shot_ref_asset_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
        *,
        include_layout: bool = True,
        include_roles: bool = True,
        include_props: bool = True,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        layout = state.layouts.get(shot.layout_id)
        if include_layout and layout and layout.asset_path:
            refs.append(
                AssetRef(
                    id=layout.id,
                    type="image",
                    path=str(project_dir / layout.asset_path),
                    url=layout.asset_url,
                    metadata={"asset_type": "layout", "name": layout.name},
                )
            )

        if include_roles:
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
                    if appearance is None:
                        continue
                    asset_path = appearance.asset_path or appearance.design_image_asset_path
                    if asset_path:
                        refs.append(
                            AssetRef(
                                id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                                type="image",
                                path=str(project_dir / asset_path),
                                url=appearance.asset_url or appearance.design_image_asset_url,
                                metadata={
                                    "asset_type": "roleboard",
                                    "role_id": role.id,
                                    "role_name": role.name,
                                    "name": appearance.name,
                                },
                            )
                        )
                        break

        if include_props:
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

    @staticmethod
    def _dialogue_speaker_prefix(line: str) -> str | None:
        text = str(line or "").strip()
        for separator in ("：", ":"):
            if separator in text:
                prefix, _suffix = text.split(separator, 1)
                prefix = prefix.strip()
                if prefix:
                    return prefix
        return None

    @staticmethod
    def _clean_dialogue_speaker_name(candidate: str) -> str:
        cleaned = str(candidate or "").strip()
        for marker in ("（", "("):
            if marker in cleaned:
                cleaned = cleaned.split(marker, 1)[0].strip()
        return cleaned

    @classmethod
    def _dialogue_speaker_is_voiceover(cls, line: str) -> bool:
        prefix = cls._dialogue_speaker_prefix(line)
        if not prefix:
            return False
        lowered = prefix.casefold()
        return any(
            marker in lowered
            for marker in (
                "vo",
                "v.o",
                "voiceover",
                "offscreen",
                "os",
                "o.s",
                "旁白",
                "画外",
                "画外音",
            )
        )

    def _shot_speaking_role_ids(self, state: ProjectState, shot: ShotManifestItem) -> list[str]:
        role_ids: list[str] = []
        seen: set[str] = set()

        def append(role_id: str | None) -> None:
            role_key = str(role_id or "").strip()
            if role_key and role_key not in seen:
                seen.add(role_key)
                role_ids.append(role_key)

        explicit_audio_ids = {str(value).strip() for value in shot.role_audio_ids if str(value).strip()}
        if explicit_audio_ids:
            for role in state.roles.values():
                for audio in role.audio.values():
                    if audio.id in explicit_audio_ids or (audio.asset_id and audio.asset_id in explicit_audio_ids):
                        append(role.id)

        for dialogue_line in shot.dialogue:
            role, _dialogue_text, _speaker_name = self._role_for_dialogue_line(state, shot, dialogue_line)
            if role is not None:
                append(role.id)

        if not role_ids and shot.dialogue and len(shot.role_ids) == 1:
            append(shot.role_ids[0])
        return role_ids

    def _shot_voiceover_speaking_role_ids(self, state: ProjectState, shot: ShotManifestItem) -> list[str]:
        role_ids: list[str] = []
        seen: set[str] = set()
        for dialogue_line in shot.dialogue:
            if not self._dialogue_speaker_is_voiceover(dialogue_line):
                continue
            role, _dialogue_text, _speaker_name = self._role_for_dialogue_line(state, shot, dialogue_line)
            if role is not None and role.id not in seen:
                seen.add(role.id)
                role_ids.append(role.id)
        return role_ids

    @staticmethod
    def _role_ids_for_appearance_ids(state: ProjectState, appearance_ids: list[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for appearance_id in appearance_ids:
            appearance_key = str(appearance_id or "").strip()
            if not appearance_key:
                continue
            for role in state.roles.values():
                if role.id in seen:
                    continue
                if any(
                    appearance.id == appearance_key or appearance.name == appearance_key
                    for appearance in role.appearances.values()
                ):
                    seen.add(role.id)
                    selected.append(role.id)
                    break
        return selected

    def _shot_intro_role_ids(self, state: ProjectState, shot: ShotManifestItem) -> list[str]:
        speaking_role_ids = self._shot_speaking_role_ids(state, shot)
        voiceover_role_ids = set(self._shot_voiceover_speaking_role_ids(state, shot))

        visual_role_ids = self._role_ids_for_appearance_ids(state, list(shot.role_appearance_ids))
        for role_id in visual_role_ids:
            if role_id not in voiceover_role_ids:
                return [role_id]
        if visual_role_ids:
            return [visual_role_ids[0]]
        if speaking_role_ids and speaking_role_ids[0] not in voiceover_role_ids:
            return [speaking_role_ids[0]]
        for role_id in shot.role_ids:
            if role_id not in voiceover_role_ids:
                return [role_id]
        if shot.role_ids:
            return [shot.role_ids[0]]
        if speaking_role_ids:
            return [speaking_role_ids[0]]
        return []

    def _shot_roleboard_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
        *,
        role_ids: set[str] | None = None,
        limit: int = 1,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        selected_role_ids = [role_id for role_id in shot.role_ids if role_ids is None or role_id in role_ids]
        if role_ids is not None:
            for role_id in role_ids:
                if role_id not in selected_role_ids:
                    selected_role_ids.append(role_id)
        explicit_appearance_ids = {str(value).strip() for value in shot.role_appearance_ids if str(value).strip()}
        for role_id in selected_role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearances = [
                appearance
                for appearance in role.appearances.values()
                if appearance.id in explicit_appearance_ids or appearance.name in explicit_appearance_ids
            ]
            if not appearances:
                base_appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
                appearances = [base_appearance] if base_appearance is not None else []
            for appearance in appearances:
                asset_path = appearance.asset_path or appearance.design_image_asset_path
                asset_url = appearance.asset_url or appearance.design_image_asset_url
                asset_id = appearance.asset_id or appearance.design_image_asset_id or appearance.id
                if not (asset_path or asset_url):
                    continue
                refs.append(
                    AssetRef(
                        id=asset_id,
                        type="image",
                        path=str(project_dir / asset_path) if asset_path else None,
                        url=asset_url,
                        metadata={
                            "asset_type": "roleboard",
                            "reference_source": "shot_keyframe_visual_subject_roleboard",
                            "role_id": role.id,
                            "role_name": role.name,
                            "appearance_id": appearance.id,
                            "name": appearance.name,
                        },
                    )
                )
                if len(refs) >= limit:
                    return refs
        return refs

    def _shot_subject_element_refs(
        self,
        state: ProjectState,
        shot: ShotManifestItem,
        *,
        limit: int = 3,
    ) -> list:
        from autodrama.providers.base import AssetRef

        selected_role_ids: list[str] = []
        for source_role_ids in (
            self._shot_intro_role_ids(state, shot),
            self._shot_speaking_role_ids(state, shot),
            self._role_ids_for_appearance_ids(state, list(shot.role_appearance_ids)),
            list(shot.role_ids),
        ):
            for role_id in source_role_ids:
                role_key = str(role_id or "").strip()
                if role_key and role_key not in selected_role_ids:
                    selected_role_ids.append(role_key)

        explicit_appearance_ids = {str(value).strip() for value in shot.role_appearance_ids if str(value).strip()}
        refs: list[AssetRef] = []
        for role_id in selected_role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearances = [
                appearance
                for appearance in role.appearances.values()
                if appearance.id in explicit_appearance_ids or appearance.name in explicit_appearance_ids
            ]
            if not appearances:
                base_appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
                appearances = [base_appearance] if base_appearance is not None else []
            for appearance in appearances:
                if not appearance.subject_element_id:
                    continue
                refs.append(
                    AssetRef(
                        id=f"{appearance.id}_subject_element",
                        type="element",
                        metadata={
                            "asset_type": "role_subject_element",
                            "reference_source": "role_subject_element_generation",
                            "reference_role": "visual_subject_identity",
                            "role_id": role.id,
                            "role_name": role.name,
                            "appearance_id": appearance.id,
                            "appearance_name": appearance.name,
                            "element_id": appearance.subject_element_id,
                            "reference_type": appearance.subject_element_reference_type,
                            "name": appearance.name,
                        },
                    )
                )
                if len(refs) >= limit:
                    return refs
        return refs

    @staticmethod
    def _raw_response_duration_seconds(raw: Any) -> float | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            for key in ("duration", "duration_seconds", "durationSeconds"):
                value = raw.get(key)
                if value is None:
                    continue
                try:
                    duration = float(value)
                except (TypeError, ValueError):
                    continue
                if duration > 0:
                    return duration
            for key in ("raw_response", "output", "data", "result", "content"):
                duration = PregenWorkflow._raw_response_duration_seconds(raw.get(key))
                if duration is not None:
                    return duration
            for item in raw.values():
                duration = PregenWorkflow._raw_response_duration_seconds(item)
                if duration is not None:
                    return duration
        elif isinstance(raw, list):
            for item in raw:
                duration = PregenWorkflow._raw_response_duration_seconds(item)
                if duration is not None:
                    return duration
        return None


    @classmethod
    def _shot_generated_video_duration_seconds(cls, shot: ShotManifestItem) -> float | None:
        duration = cls._raw_response_duration_seconds(shot.video_raw_response)
        if duration is not None:
            return duration
        try:
            value = float(shot.duration_seconds)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _shot_role_audio_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
        *,
        role_ids: set[str] | None = None,
        limit: int = 1,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        explicit_audio_ids = {str(value).strip() for value in shot.role_audio_ids if str(value).strip()}
        seen: set[str] = set()

        def append_audio(role: Role, audio: RoleAudio, *, source: str) -> None:
            if role_ids is not None and role.id not in role_ids:
                return
            if len(refs) >= limit:
                return
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
                        "duration_seconds": audio.duration_seconds,
                        "original_duration_seconds": audio.original_duration_seconds,
                        "duration_limited": audio.duration_limited,
                    },
                )
            )

        if explicit_audio_ids:
            for role in state.roles.values():
                for audio in role.audio.values():
                    if audio.id in explicit_audio_ids or (audio.asset_id and audio.asset_id in explicit_audio_ids):
                        append_audio(role, audio, source="shot_role_audio_ids")
                        if len(refs) >= limit:
                            return refs
            return refs

        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            audio = role.audio.get("normal") or next((item for item in role.audio.values() if item.asset_path), None)
            if audio is not None:
                append_audio(role, audio, source="shot_role_ids")
                if len(refs) >= limit:
                    return refs
        return refs

    @staticmethod
    def _clip_video_ref_asset_type(ref) -> str:
        metadata = getattr(ref, "metadata", {}) or {}
        return str(metadata.get("asset_type") or "").strip()

    @staticmethod
    def _clip_video_ref_duration_seconds(ref) -> float | None:
        metadata = getattr(ref, "metadata", {}) or {}
        for key in ("duration_seconds", "duration"):
            value = metadata.get(key)
            if value is None:
                continue
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                return duration
        return None

    @classmethod
    def _prioritize_clip_video_refs(cls, refs: list, *, provider=None) -> list:
        def image_priority(ref) -> tuple[int, str]:
            asset_type = cls._clip_video_ref_asset_type(ref)
            priority = {
                "shot_keyframe": 0,
                "layout": 1,
                "roleboard": 2,
                "key_vision": 3,
                "previous_shot_last_frame": 6,
                "prop": 7,
            }.get(asset_type, 9)
            return priority, str(getattr(ref, "id", "") or "")

        def video_priority(ref) -> tuple[int, str]:
            asset_type = cls._clip_video_ref_asset_type(ref)
            priority = {
                "previous_shot_video": 0,
                "reference_and_previous_shot_video": 0,
                "reference_video": 1,
            }.get(asset_type, 9)
            return priority, str(getattr(ref, "id", "") or "")

        images = [ref for ref in refs if getattr(ref, "type", None) == "image"]
        videos = [ref for ref in refs if getattr(ref, "type", None) == "video"]
        audios = [ref for ref in refs if getattr(ref, "type", None) == "audio"]
        others = [
            ref
            for ref in refs
            if getattr(ref, "type", None) not in {"image", "video", "audio", "element"}
        ]
        def provider_limit(name: str, default: int) -> int:
            value = getattr(provider, name, default)
            if value is None:
                return default
            return max(0, int(value))

        max_images = provider_limit("max_reference_images", 3)
        max_videos = provider_limit("max_reference_videos", 2)
        max_audio = provider_limit("max_reference_audio", 1)
        max_video_duration = getattr(provider, "max_reference_video_total_duration_seconds", None)
        if max_video_duration is None:
            max_video_duration = 15.2
        try:
            max_video_duration = float(max_video_duration)
        except (TypeError, ValueError):
            max_video_duration = 15.2

        selected_videos = []
        selected_video_duration = 0.0
        for ref in sorted(videos, key=video_priority):
            if len(selected_videos) >= max_videos:
                break
            duration = cls._clip_video_ref_duration_seconds(ref)
            if max_video_duration > 0 and duration is not None:
                if selected_video_duration + duration > max_video_duration:
                    continue
                selected_video_duration += duration
            selected_videos.append(ref)

        selected_images = []
        seen_images: set[tuple[str, str, str, str]] = set()
        for ref in sorted(images, key=image_priority):
            key = (
                cls._clip_video_ref_asset_type(ref),
                str(getattr(ref, "id", "") or ""),
                str(getattr(ref, "path", "") or ""),
                str(getattr(ref, "url", "") or ""),
            )
            if key in seen_images:
                continue
            seen_images.add(key)
            selected_images.append(ref)
            if len(selected_images) >= max_images:
                break

        return [
            *selected_images,
            *selected_videos,
            *audios[:max_audio],
            *others,
        ]

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
    def _video_reference_mode_uses_role_prop_refs(mode: str) -> bool:
        return mode in {
            "role_prop",
            "role_prop_previous_video",
            "role_prop_prev_video",
        }

    @staticmethod
    def _video_reference_mode_uses_layout_roleboard_refs(mode: str) -> bool:
        return mode in {
            "layout_roleboard",
            "scene_roleboard",
            "subject_shot_keyframe_key_vision",
            "subject_shot_keyframe_keyvision",
            "kling_subject",
            "kling_subject_shot_keyframe_key_vision",
            "layout_roleboard_previous_video",
            "layout_roleboard_prev_video",
            "layout_roleboard_context_video",
            "scene_roleboard_previous_video",
            "scene_roleboard_prev_video",
            "scene_roleboard_context_video",
        }

    @staticmethod
    def _video_reference_mode_uses_frame_images(mode: str) -> bool:
        return mode not in {
            "layout_roleboard_previous_video",
            "layout_roleboard_prev_video",
            "layout_roleboard_context_video",
            "scene_roleboard_previous_video",
            "scene_roleboard_prev_video",
            "scene_roleboard_context_video",
        }

    @staticmethod
    def _video_reference_mode_uses_previous_scene_video(mode: str) -> bool:
        return mode in {
            "layout_roleboard_previous_video",
            "layout_roleboard_prev_video",
            "layout_roleboard_context_video",
            "scene_roleboard_previous_video",
            "scene_roleboard_prev_video",
            "scene_roleboard_context_video",
            "role_prop_previous_video",
            "role_prop_prev_video",
            "previous_video",
            "prev_video",
        }

    @staticmethod
    def _video_reference_mode_uses_subject_elements(mode: str, provider=None) -> bool:
        return False

    @staticmethod
    def _previous_shot(episode: ShotManifestEpisodeOutput | None, shot: ShotManifestItem) -> ShotManifestItem | None:
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

    @staticmethod
    def _shots_share_scene(current_shot: ShotManifestItem, previous_shot: ShotManifestItem) -> bool:
        return bool(current_shot.layout_id and current_shot.layout_id == previous_shot.layout_id)

    @staticmethod
    def _clip_video_file_path(project_dir: Path, shot: ShotManifestItem) -> Path | None:
        if not shot.video_asset_path:
            return None
        video_path = Path(shot.video_asset_path)
        if not video_path.is_absolute():
            video_path = project_dir / video_path
        if not video_path.exists() or not video_path.is_file():
            return None
        return video_path

    @staticmethod
    def _is_web_url(value: str | None) -> bool:
        if not isinstance(value, str) or not value:
            return False
        return value.startswith(("http://", "https://"))

    @staticmethod
    def _signed_url_expiry(value: str) -> datetime | None:
        query = parse_qs(urlparse(value).query)
        for key in ("Expires", "expires", "x-expires", "X-Expires"):
            raw_values = query.get(key)
            if not raw_values:
                continue
            try:
                return datetime.fromtimestamp(int(raw_values[0]), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                continue

        tos_dates = query.get("X-Tos-Date") or query.get("x-tos-date")
        tos_expires = query.get("X-Tos-Expires") or query.get("x-tos-expires")
        if tos_dates and tos_expires:
            try:
                signed_at = datetime.strptime(tos_dates[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return signed_at + timedelta(seconds=int(tos_expires[0]))
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def _web_url_is_probably_usable(cls, value: str | None) -> bool:
        if not cls._is_web_url(value):
            return False
        expiry = cls._signed_url_expiry(str(value))
        if expiry is None:
            return True
        return expiry > datetime.now(timezone.utc) + timedelta(minutes=5)

    @classmethod
    def _video_url_from_raw_response(cls, value: Any) -> str | None:
        def usable(raw: Any) -> str | None:
            if isinstance(raw, str) and cls._web_url_is_probably_usable(raw):
                return raw
            return None

        def walk(raw: Any) -> str | None:
            if isinstance(raw, dict):
                for key in ("video_url", "videoUrl"):
                    candidate = raw.get(key)
                    if isinstance(candidate, dict):
                        found = usable(candidate.get("url"))
                    else:
                        found = usable(candidate)
                    if found:
                        return found

                if str(raw.get("type") or "").strip().lower() == "video_url":
                    nested_video = raw.get("video_url")
                    if isinstance(nested_video, dict):
                        found = usable(nested_video.get("url"))
                        if found:
                            return found
                    found = usable(raw.get("url"))
                    if found:
                        return found

                for key in ("output", "data", "result", "content"):
                    found = walk(raw.get(key))
                    if found:
                        return found
                for item in raw.values():
                    found = walk(item)
                    if found:
                        return found
            elif isinstance(raw, list):
                for item in raw:
                    found = walk(item)
                    if found:
                        return found
            return None

        return walk(value)

    @classmethod
    def _clip_video_web_url(cls, shot: ShotManifestItem) -> str | None:
        asset_url = getattr(shot, "video_asset_url", None)
        if cls._web_url_is_probably_usable(asset_url):
            return str(asset_url)
        return cls._video_url_from_raw_response(shot.video_raw_response)

    @staticmethod
    def _video_provider_requires_web_reference_video(provider=None) -> bool:
        return bool(getattr(provider, "reference_video_requires_web_url", False))

    def _clip_video_reference_available(
        self,
        project_dir: Path,
        shot: ShotManifestItem,
        *,
        provider=None,
    ) -> bool:
        if self._video_provider_requires_web_reference_video(provider):
            return self._clip_video_web_url(shot) is not None
        return self._clip_video_file_path(project_dir, shot) is not None or self._clip_video_web_url(shot) is not None

    def _previous_video_shot(
        self,
        project_dir: Path,
        episode: ShotManifestEpisodeOutput | None,
        shot: ShotManifestItem,
        *,
        provider=None,
    ) -> ShotManifestItem | None:
        previous_shot = self._previous_shot(episode, shot)
        if previous_shot is None:
            return None
        return previous_shot if self._clip_video_reference_available(project_dir, previous_shot, provider=provider) else None

    def _nearest_same_scene_video_shot(
        self,
        project_dir: Path,
        episode: ShotManifestEpisodeOutput | None,
        shot: ShotManifestItem,
        *,
        provider=None,
    ) -> ShotManifestItem | None:
        if episode is None:
            return None
        previous_shots = sorted(
            (item for item in episode.shots if int(item.index) < int(shot.index)),
            key=lambda item: int(item.index),
            reverse=True,
        )
        for candidate in previous_shots:
            if not self._shots_share_scene(shot, candidate):
                continue
            if self._clip_video_reference_available(project_dir, candidate, provider=provider):
                return candidate
        return None

    @staticmethod
    def _video_refs_point_to_same_asset(left, right) -> bool:
        if left is None or right is None:
            return False
        left_id = str(left.id or "").strip()
        right_id = str(right.id or "").strip()
        if left_id and right_id and left_id == right_id:
            return True
        left_path = str(left.path or "").strip().replace("\\", "/")
        right_path = str(right.path or "").strip().replace("\\", "/")
        if left_path and right_path and left_path == right_path:
            return True
        left_url = str(left.url or "").strip()
        right_url = str(right.url or "").strip()
        return bool(left_url and right_url and left_url == right_url)

    def _key_vision_ref(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
    ):
        from autodrama.providers.base import AssetRef

        key_vision = state.metadata.get("key_vision_asset")
        if isinstance(key_vision, dict):
            asset_id = str(key_vision.get("asset_id") or state.metadata.get("key_vision_asset_id") or "key_vision_original")
            asset_path = key_vision.get("asset_path") or state.metadata.get("key_vision_asset_path")
            asset_url = key_vision.get("asset_url") or state.metadata.get("key_vision_asset_url")
            name = str(key_vision.get("name") or state.metadata.get("key_vision_name") or "主视觉原图")
        else:
            asset_id = str(state.metadata.get("key_vision_asset_id") or "key_vision_original")
            asset_path = state.metadata.get("key_vision_asset_path")
            asset_url = state.metadata.get("key_vision_asset_url")
            name = str(state.metadata.get("key_vision_name") or "主视觉原图")
        existing = self.layout.existing_project_file(project_dir, str(asset_path)) if asset_path else None
        if not existing and not asset_url:
            return None
        return AssetRef(
            id=asset_id,
            type="image",
            path=str(project_dir / existing) if existing else None,
            url=str(asset_url) if asset_url else None,
            metadata={
                "asset_type": "key_vision",
                "reference_source": "key_vision_image_generation",
                "reference_role": "style_world_reference",
                "shot_id": shot.shot_id,
                "shot_index": shot.index,
                "name": name,
            },
        )

    def _clip_video_asset_ref(
        self,
        project_dir: Path,
        source_shot: ShotManifestItem,
        *,
        asset_type: str,
        reference_source: str,
        reference_role: str,
        current_shot: ShotManifestItem,
        provider=None,
        extra_metadata: dict[str, Any] | None = None,
    ):
        from autodrama.providers.base import AssetRef

        video_url = self._clip_video_web_url(source_shot)
        video_path = self._clip_video_file_path(project_dir, source_shot)
        if self._video_provider_requires_web_reference_video(provider) and not video_url:
            get_logger().warning(
                "skip video reference %s for %s: provider %s requires a non-expired web URL, "
                "but only local/expired video is available",
                source_shot.shot_id,
                current_shot.shot_id,
                getattr(provider, "name", "unknown"),
                extra={"shot_id": current_shot.shot_id, "source_shot_id": source_shot.shot_id},
            )
            return None
        if video_path is None and not video_url:
            return None
        metadata = {
            "asset_type": asset_type,
            "reference_source": reference_source,
            "reference_role": reference_role,
            "source_shot_id": source_shot.shot_id,
            "duration_seconds": self._shot_generated_video_duration_seconds(source_shot),
            "layout_id": current_shot.layout_id,
            "camera_direction_required": False,
            "scene_match_rule": "same layout_id",
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return AssetRef(
            id=source_shot.video_asset_id or source_shot.shot_id,
            type="video",
            path=str(video_path) if video_path is not None else None,
            url=video_url,
            metadata=metadata,
        )

    def _shot_context_video_refs(
        self,
        project_dir: Path,
        shot: ShotManifestItem,
        episode: ShotManifestEpisodeOutput | None,
        provider=None,
    ) -> list:
        previous_shot = self._previous_video_shot(project_dir, episode, shot, provider=provider)
        previous_ref = (
            self._clip_video_asset_ref(
                project_dir,
                previous_shot,
                asset_type="previous_shot_video",
                reference_source="previous_shot_content_continuity",
                reference_role="previous_shot_continuity",
                current_shot=shot,
                provider=provider,
                extra_metadata={
                    "previous_shot_id": previous_shot.shot_id,
                },
            )
            if previous_shot is not None
            else None
        )
        return [previous_ref] if previous_ref is not None else []

    def _clip_video_reference_context(
        self,
        project_dir: Path | None,
        episode: ShotManifestEpisodeOutput | None,
        shot: ShotManifestItem,
        *,
        provider=None,
    ) -> str:
        if project_dir is None:
            return "none"
        scene_shot = self._nearest_same_scene_video_shot(project_dir, episode, shot, provider=provider)
        previous_shot = self._previous_video_shot(project_dir, episode, shot, provider=provider)
        if scene_shot is not None and previous_shot is not None:
            scene_path = self._clip_video_file_path(project_dir, scene_shot)
            previous_path = self._clip_video_file_path(project_dir, previous_shot)
            scene_url = self._clip_video_web_url(scene_shot)
            previous_url = self._clip_video_web_url(previous_shot)
            if scene_shot.shot_id == previous_shot.shot_id or (
                scene_path is not None and previous_path is not None and scene_path == previous_path
            ) or (
                scene_url is not None and previous_url is not None and scene_url == previous_url
            ):
                return "shared_reference_and_previous_video"
            return "separate_reference_and_previous_video"
        if scene_shot is not None:
            return "reference_video_only"
        if previous_shot is not None:
            return "previous_video_only"
        return "none"

    def _clip_video_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: ShotManifestItem,
        provider=None,
        episode: ShotManifestEpisodeOutput | None = None,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        previous_shot = self._previous_shot(episode, shot)
        reference_mode = self._video_reference_mode(provider)
        speaking_role_ids = self._shot_speaking_role_ids(state, shot)
        intro_role_ids = set(self._shot_intro_role_ids(state, shot)[:1])
        audio_role_ids = set(speaking_role_ids[:1])
        if shot.start_frame_source == "previous_shot_last_frame" and self._video_reference_mode_uses_frame_images(reference_mode):
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
        refs.extend(self._shot_roleboard_refs(project_dir, state, shot, role_ids=intro_role_ids, limit=1))
        if self._video_reference_mode_uses_layout_roleboard_refs(reference_mode):
            refs.extend(
                self._shot_ref_asset_refs(
                    project_dir,
                    state,
                    shot,
                    include_layout=True,
                    include_roles=False,
                    include_props=False,
                )
            )
        elif self._video_reference_mode_uses_role_prop_refs(reference_mode):
            refs.extend(
                self._shot_ref_asset_refs(
                    project_dir,
                    state,
                    shot,
                    include_layout=False,
                    include_roles=True,
                    include_props=True,
                )
            )
        else:
            refs.extend(self._shot_ref_asset_refs(project_dir, state, shot, include_roles=False))
        key_vision_ref = self._key_vision_ref(project_dir, state, shot)
        if key_vision_ref is not None:
            refs.append(key_vision_ref)
        context_video_refs = (
            self._shot_context_video_refs(project_dir, shot, episode, provider=provider)
            if self._video_reference_mode_uses_previous_scene_video(reference_mode)
            else []
        )
        dialogue_audio_role_ids: set[str] = set()
        dialogue_audio_count = 0
        for audio in shot.dialogue_audio_assets:
            if audio_role_ids and audio.role_id and audio.role_id not in audio_role_ids:
                continue
            if dialogue_audio_count >= 1:
                continue
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
                            "duration_seconds": audio.duration_seconds,
                            "original_duration_seconds": audio.original_duration_seconds,
                            "duration_limited": audio.duration_limited,
                        },
                    )
                )
                dialogue_audio_count += 1
                if audio.role_id:
                    dialogue_audio_role_ids.add(audio.role_id)
        role_audio_refs = (
            self._shot_role_audio_refs(project_dir, state, shot, role_ids=audio_role_ids, limit=1)
            if audio_role_ids
            else []
        )
        if not shot.role_audio_ids:
            role_audio_refs = [
                ref
                for ref in role_audio_refs
                if not ref.metadata.get("role_id") or ref.metadata.get("role_id") not in dialogue_audio_role_ids
            ]
        refs.extend(role_audio_refs)
        refs.extend(context_video_refs)
        return self._prioritize_clip_video_refs(refs, provider=provider)

    def _role_for_dialogue_line(
        self,
        state: ProjectState,
        shot: ShotManifestItem,
        line: str,
    ) -> tuple[Role | None, str, str | None]:
        text = str(line).strip()
        speaker_name: str | None = None
        dialogue_text = text
        for separator in ("：", ":"):
            if separator in text:
                prefix, suffix = text.split(separator, 1)
                candidate = self._clean_dialogue_speaker_name(prefix)
                if 0 < len(candidate) <= 20:
                    speaker_name = candidate
                    dialogue_text = suffix.strip()
                    break

        lookup = self._role_lookup(state)
        role = self._resolve_role(lookup, speaker_name) if speaker_name else None
        if role is None and speaker_name:
            normalized = normalize_id("role", speaker_name)
            role = state.roles.get(normalized)
        if role is None and not speaker_name and len(shot.role_ids) == 1:
            role = state.roles.get(shot.role_ids[0])
        return role, dialogue_text or text, speaker_name

    @staticmethod
    def _shot_dialogue_emotion(role: Role | None, shot: ShotManifestItem) -> str:
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
        shot: ShotManifestItem,
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
        max_duration_seconds = provider_max_generated_audio_duration_seconds(provider)
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
                "max_generated_audio_duration_seconds": max_duration_seconds,
            },
        )
        asset_path = await self._write_generated_audio(
            project_dir,
            self._audio_asset_path(project_dir, "shot_dialogues", asset_id, result.audio_format),
            audio_data=result.audio_data,
        )
        duration_result = self._limit_generated_audio_duration(project_dir, asset_path, provider=provider)
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
                duration_seconds=duration_result.duration_seconds,
                original_duration_seconds=(
                    duration_result.original_duration_seconds if duration_result.trimmed else None
                ),
                duration_limited=duration_result.trimmed,
                sample_rate=result.audio_sample_rate,
                response_format=result.audio_format,
                request_id=result.request_id,
                usage={**result.usage, **audio_duration_limit_metadata(duration_result)},
                raw_response=result.raw_response,
            ),
            None,
        )

    def _static_asset_node_runner(self, node_name: str) -> StaticAssetNodeBase:
        return build_static_asset_node_runners(self)[node_name]

    async def _run_roleboard_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("roleboard_image_generation").run(project_dir, state)

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
        return await self._run_prop_prompt(project_dir, state)

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

    async def _run_prop_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._run_prop_image_generation(project_dir, state)

    async def _run_prop_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_image_generation").run(project_dir, state)

    async def _run_prop_finalize(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_finalize").run(project_dir, state)

    async def _run_prop_prompt(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("prop_prompt").run(project_dir, state)

    async def _run_layout_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_extract").run(project_dir, state)

    async def _run_layout_finalize(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_finalize").run(project_dir, state)

    async def _run_layout_prop_boundary_review(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_prop_boundary_review").run(project_dir, state)

    async def _run_layout_prompt(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_prompt").run(project_dir, state)

    async def _run_layout_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._static_asset_node_runner("layout_image_generation").run(project_dir, state)

    def _bgm_node_runner(self, node_name: str) -> BGMNodeBase:
        return build_bgm_node_runners(self)[node_name]

    async def _run_bgm_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._bgm_node_runner("bgm_design").run(project_dir, state)

    async def _run_bgm_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        return await self._bgm_node_runner("bgm_generation").run(project_dir, state)
