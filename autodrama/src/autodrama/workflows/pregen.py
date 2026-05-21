from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    BGM,
    Layout,
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
    RoleVoiceGenerationOutput,
    ScriptNovelExtractOutput,
    ScriptNovelOutput,
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
    "script_novel",
    "script_novel_extract",
    "role_extract",
    "role_design",
    "role_voice_generation",
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

SCRIPT_NOVEL_EXTRACT_BATCH_SIZE = 5


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

    @staticmethod
    def _script_content_path(project_dir: Path, category: str, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / category / f"{episode_key}.json"

    @staticmethod
    def _script_novel_legacy_episode_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / f"{episode_key}.json"

    @staticmethod
    def _script_novel_legacy_full_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / "novel" / f"{episode_key}.json"

    @staticmethod
    def _script_content_payload(
        *,
        node_name: str,
        episode_key: str,
        content: str,
        dependency_field: str | None = None,
        dependency_path: object | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "node_name": node_name,
            "episode_key": episode_key,
            "content": content,
        }
        if dependency_field and dependency_path:
            payload[dependency_field] = dependency_path
        return payload

    @classmethod
    def _load_script_content_ref(cls, project_dir: Path, value: object) -> str | None:
        if value is False or value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        path = Path(text)
        if not path.is_absolute():
            path = project_dir / text
        if path.exists() and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid script content JSON: {path}")
            for key in ("content", "novel_full", "novel_text", "episode_outline"):
                content = str(payload.get(key) or "").strip()
                if content:
                    return content
            return None

        if text.endswith(".json") or "/" in text or "\\" in text:
            return None
        return text

    def _load_script_contents(
        self,
        project_dir: Path,
        refs: dict[str, object],
        episode_keys: list[str],
        *,
        label: str,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        contents: dict[str, str] = {}
        for episode_key in episode_keys:
            content = self._load_script_content_ref(project_dir, refs.get(episode_key))
            if content:
                contents[episode_key] = content
            elif not allow_missing:
                raise ValueError(f"{label} is missing content for {episode_key}")
        return contents

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
        path = project_dir / "assets" / "json" / "nodes" / "role_extract.json"
        if not path.exists():
            raise FileNotFoundError(
                "role_extract output is missing; run pregen --only role_extract before role_design"
            )
        return RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_existing_role_design_output(self, project_dir: Path) -> RoleDesignOutput | None:
        path = project_dir / "assets" / "json" / "nodes" / "role_design.json"
        if not path.exists():
            return None
        return RoleDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _role_design_item_complete(item: RoleDesignItem) -> bool:
        return bool(str(item.intro or "").strip() and item.appearances and item.voices)

    def _role_episode_keys(self, item: RoleExtractItem, state: ProjectState) -> list[str]:
        expected = set(self._expected_episode_keys(state))
        keys: list[str] = []
        invalid_keys: list[str] = []
        for key in item.episode_keys:
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
        get_logger().warning(
            "role_extract missing episode_keys for %s; role_design will load all novel_full episodes",
            item.name,
        )
        return self._expected_episode_keys(state)

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
        chunks: list[str] = []
        for episode_key in episode_keys:
            text = str(novel_full.get(episode_key) or "").strip()
            if text:
                chunks.append(f"## {episode_key}\n{text}")
        return "\n\n".join(chunks) if chunks else "（暂无，当前是第一章。）"

    @staticmethod
    def _load_script_novel_episode_text(path: Path) -> str | None:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid script_novel episode JSON: {path}")
        text = str(payload.get("content") or payload.get("novel_full") or payload.get("novel_text") or "").strip()
        return text or None

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
        if selected_episode_keys and target_nodes != ["role_design"]:
            raise ValueError(
                "--episodes is only supported for pregen --only role_design. "
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
        self._force_pregen = bool(force)
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
            if previous_force_pregen is None:
                if hasattr(self, "_force_pregen"):
                    delattr(self, "_force_pregen")
            else:
                self._force_pregen = previous_force_pregen

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
        state.script.episode_outlines = {}
        for episode_key, content in output.episode_outlines.items():
            outline_path = self._script_content_path(project_dir, "outlines", episode_key)
            self.repo.write_json(
                outline_path,
                self._script_content_payload(
                    node_name="script_outline",
                    episode_key=episode_key,
                    content=str(content).strip(),
                ),
            )
            state.script.episode_outlines[episode_key] = self._project_relative(project_dir, outline_path)
        state.budget.used_text_calls += 1
        self.repo.save_node_output(
            project_dir,
            "script_outline",
            {
                "logline": output.logline,
                "outline": output.outline,
                "episode_count": output.episode_count,
                "target_duration_seconds": output.target_duration_seconds,
                "episode_outlines": state.script.episode_outlines,
            },
        )
        return state

    async def _run_script_novel(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_novel provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self._expected_episode_keys(state)
        outline_contents = self._load_script_contents(
            project_dir,
            state.script.episode_outlines,
            episode_keys,
            label="script_outline.episode_outlines",
        )
        target_char_count = self.script_service.episode_target_char_count(state)
        force_pregen = bool(getattr(self, "_force_pregen", False))
        if force_pregen:
            state.script.novel_full = {episode_key: False for episode_key in episode_keys}
        else:
            state.script.novel_full = {
                episode_key: state.script.novel_full.get(episode_key) or False
                for episode_key in episode_keys
            }
        state.metadata["script_novel_target_char_count"] = target_char_count
        expected_episode_keys = set(episode_keys)
        state.metadata["script_novel_full_episode_paths"] = (
            {
                key: value
                for key, value in dict(state.metadata.get("script_novel_full_episode_paths") or {}).items()
                if key in expected_episode_keys
            }
            if not force_pregen
            else {}
        )

        novel_contents: dict[str, str] = {}
        for episode_index, episode_key in enumerate(episode_keys, start=1):
            episode_path = self._script_content_path(project_dir, "novel_full", episode_key)
            legacy_full_path = self._script_novel_legacy_full_path(project_dir, episode_key)
            legacy_episode_path = self._script_novel_legacy_episode_path(project_dir, episode_key)
            existing_text = None
            if not force_pregen:
                existing_text = self._load_script_content_ref(project_dir, state.script.novel_full.get(episode_key))
                if not existing_text:
                    existing_text = self._load_script_novel_episode_text(episode_path)
                if not existing_text:
                    existing_text = self._load_script_novel_episode_text(legacy_full_path)
                if not existing_text:
                    existing_text = self._load_script_novel_episode_text(legacy_episode_path)

            if not force_pregen and existing_text:
                if not episode_path.exists():
                    self.repo.write_json(
                        episode_path,
                        self._script_content_payload(
                            node_name="script_novel",
                            episode_key=episode_key,
                            content=existing_text,
                            dependency_field="source_outline_path",
                            dependency_path=state.script.episode_outlines.get(episode_key),
                        ),
                    )
                novel_contents[episode_key] = existing_text
                state.script.novel_full[episode_key] = self._project_relative(project_dir, episode_path)
                state.metadata["script_novel_full_episode_paths"][episode_key] = self._project_relative(project_dir, episode_path)
                self.repo.save_state(project_dir, state)
                get_logger().info(
                    "script_novel %s already exists, skipped; saved in %s",
                    episode_key,
                    self._project_relative(project_dir, episode_path),
                )
                continue

            previous_chapters = self._format_previous_novel_chapters(
                novel_contents,
                episode_keys[: episode_index - 1],
            )
            output = await self.script_service.script_novel_episode(
                state,
                provider,
                episode_key=episode_key,
                episode_outlines=outline_contents,
                current_episode_outline=outline_contents.get(episode_key, ""),
                previous_chapters=previous_chapters,
            )
            if output.episode_key != episode_key:
                raise ValueError(f"script_novel episode_key must be {episode_key}; got {output.episode_key}")
            if output.target_char_count != target_char_count:
                raise ValueError(
                    f"script_novel target_char_count must be {target_char_count}; got {output.target_char_count}"
                )
            novel_full = output.novel_full.strip()
            if not novel_full:
                raise ValueError(f"script_novel novel_full is empty for {episode_key}")

            novel_contents[episode_key] = novel_full
            state.budget.used_text_calls += 1
            self.repo.write_json(
                episode_path,
                self._script_content_payload(
                    node_name="script_novel",
                    episode_key=episode_key,
                    content=novel_full,
                    dependency_field="source_outline_path",
                    dependency_path=state.script.episode_outlines.get(episode_key),
                ),
            )
            state.script.novel_full[episode_key] = self._project_relative(project_dir, episode_path)
            state.metadata["script_novel_full_episode_paths"][episode_key] = self._project_relative(project_dir, episode_path)
            self.repo.save_state(project_dir, state)
            get_logger().info(
                "script_novel %s generated target_chars=%d saved in %s",
                episode_key,
                target_char_count,
                self._project_relative(project_dir, episode_path),
            )

        self._validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        output = ScriptNovelOutput(novel_full=state.script.novel_full)
        self.repo.save_node_output(project_dir, "script_novel", output)
        return state

    async def _run_script_novel_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        get_logger().info(
            "node=script_novel_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self._expected_episode_keys(state)
        self._validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_contents = self._load_script_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_novel.novel_full",
        )
        outline_contents = self._load_script_contents(
            project_dir,
            state.script.episode_outlines,
            episode_keys,
            label="script_outline.episode_outlines",
            allow_missing=True,
        )
        force_pregen = bool(getattr(self, "_force_pregen", False))
        if force_pregen:
            state.script.novel_extract = {episode_key: False for episode_key in episode_keys}
        else:
            state.script.novel_extract = {
                episode_key: state.script.novel_extract.get(episode_key) or False
                for episode_key in episode_keys
            }
        state.metadata["script_novel_extract_batch_size"] = SCRIPT_NOVEL_EXTRACT_BATCH_SIZE

        extract_contents = (
            {}
            if force_pregen
            else self._load_script_contents(
                project_dir,
                state.script.novel_extract,
                episode_keys,
                label="script_novel_extract.novel_extract",
                allow_missing=True,
            )
        )
        for batch_start in range(0, len(episode_keys), SCRIPT_NOVEL_EXTRACT_BATCH_SIZE):
            batch_keys = episode_keys[batch_start : batch_start + SCRIPT_NOVEL_EXTRACT_BATCH_SIZE]
            if not force_pregen and all(extract_contents.get(episode_key) for episode_key in batch_keys):
                get_logger().info(
                    "script_novel_extract batch %s already exists, skipped",
                    ",".join(batch_keys),
                )
                continue

            previous_keys = episode_keys[:batch_start]
            previous_extract = {
                episode_key: extract_contents[episode_key]
                for episode_key in previous_keys
                if extract_contents.get(episode_key)
            }
            extract_hints = {
                episode_key: outline_contents.get(episode_key, "")
                for episode_key in batch_keys
            }
            output = await self.script_service.script_novel_extract_batch(
                state,
                provider,
                batch_episode_keys=batch_keys,
                novel_full=novel_contents,
                previous_extract=previous_extract,
                extract_hints=extract_hints,
            )
            actual_keys = set(output.novel_extract)
            expected_keys = set(batch_keys)
            if actual_keys != expected_keys:
                raise ValueError(
                    "script_novel_extract.novel_extract must contain exactly "
                    f"{', '.join(batch_keys)}; got {', '.join(sorted(actual_keys)) or '-'}"
                )
            for episode_key in batch_keys:
                extracted_text = str(output.novel_extract.get(episode_key) or "").strip()
                if not extracted_text:
                    raise ValueError(f"script_novel_extract novel_extract is empty for {episode_key}")
                episode_path = self._script_content_path(project_dir, "novel_extract", episode_key)
                self.repo.write_json(
                    episode_path,
                    self._script_content_payload(
                        node_name="script_novel_extract",
                        episode_key=episode_key,
                        content=extracted_text,
                        dependency_field="source_novel_full_path",
                        dependency_path=state.script.novel_full.get(episode_key),
                    ),
                )
                extract_contents[episode_key] = extracted_text
                state.script.novel_extract[episode_key] = self._project_relative(project_dir, episode_path)

            state.budget.used_text_calls += 1
            self.repo.save_state(project_dir, state)
            get_logger().info(
                "script_novel_extract batch %s generated saved_count=%d",
                ",".join(batch_keys),
                len(batch_keys),
            )

        self._validate_episode_keys("script_novel_extract.novel_extract", state.script.novel_extract, state)
        output = ScriptNovelExtractOutput(novel_extract=state.script.novel_extract)
        self.repo.save_node_output(project_dir, "script_novel_extract", output)
        return state

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
            return output.roles[0]
        raise ValueError(
            f"role_design must return only {extract_item.name}; got "
            f"{', '.join(item.name for item in output.roles)}"
        )

    def _merge_role_extract_into_design(self, item: RoleDesignItem, extract_item: RoleExtractItem) -> RoleDesignItem:
        item.name = extract_item.name
        item.aliases = self._dedupe_texts([*extract_item.aliases, *item.aliases])
        item.importance = item.importance or extract_item.importance
        item.episode_keys = self._dedupe_texts([*extract_item.episode_keys, *item.episode_keys])
        item.source_chapters = self._dedupe_texts([*extract_item.source_chapters, *item.source_chapters])
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
        state: ProjectState,
        item: RoleDesignItem,
        *,
        speech_provider=None,
    ) -> None:
        if not self._role_design_item_complete(item):
            missing: list[str] = []
            if not str(item.intro or "").strip():
                missing.append("intro")
            if not item.appearances:
                missing.append("appearances")
            if not item.voices:
                missing.append("voices")
            raise ValueError(f"role_design for {item.name} is missing required fields: {', '.join(missing)}")

        role_id = normalize_id("role", item.name)
        existing_role = state.roles.get(role_id)
        role = existing_role or Role(
            id=role_id,
            name=item.name,
            intro=item.intro,
        )
        role.name = item.name
        role.intro = item.intro
        role.personality = item.personality
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

        self._apply_role_voice_design_output(
            state,
            RoleVoiceDesignOutput(role_voices=item.voices),
            speech_provider=speech_provider,
        )
        role = state.roles[role_id]

        for appearance_item in item.appearances:
            appearance_name = str(appearance_item.name or "base").strip() or "base"
            appearance_id = normalize_id(f"{role.id}_appearance", appearance_name)
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
                state.props[prop_id] = Prop(
                    id=prop_id,
                    name=prop_item.name,
                    desc="；".join(part.strip("；") for part in desc_parts if part),
                    prompt=prop_item.prompt,
                    status=prop_item.status,
                    owner_role_id=role.id,
                    owner_role_name=role.name,
                    source="role_design",
                )
                role_bound_prop_ids.append(prop_id)
            role.appearances[appearance_name] = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=appearance_name,
                desc=appearance_item.desc,
                prompt=appearance_item.prompt,
                role_bound_prop_ids=role_bound_prop_ids,
                intro_video_prompt=appearance_item.intro_video_prompt,
            )

    async def _run_role_extract(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        get_logger().info(
            "node=role_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self._expected_episode_keys(state)
        self._validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self._novel_full_contents(project_dir, state, episode_keys)
        output = await self.role_service.role_extract(
            state,
            provider,
            novel_full=novel_full,
        )
        if not output.roles:
            raise ValueError("role_extract must return at least one role")

        expected = set(episode_keys)
        seen_names: set[str] = set()
        for item in output.roles:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError("role_extract returned a role with empty name")
            name_key = self._role_name_key(item.name)
            if name_key in seen_names:
                raise ValueError(f"role_extract returned duplicated role name: {item.name}")
            seen_names.add(name_key)
            item.aliases = self._dedupe_texts(item.aliases)
            item.source_chapters = self._dedupe_texts(item.source_chapters)
            item.appearance_notes = self._dedupe_texts(item.appearance_notes)
            item.episode_keys = self._dedupe_texts(item.episode_keys)
            invalid_episode_keys = sorted(set(item.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"role_extract episode_keys for {item.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not item.episode_keys:
                raise ValueError(f"role_extract must include episode_keys for {item.name}")

        state.metadata["role_extract"] = output.model_dump(mode="json")
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_extract", output)
        return state

    async def _run_role_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        speech_provider = None
        available_voices: list[dict[str, Any]] = []
        try:
            speech_provider = self.router.audio("speech")
            available_voices = self._available_speakers_for_prompt(speech_provider)
        except Exception as exc:
            get_logger().warning("node=role_design could not load speech voice catalog: %s", exc)
        get_logger().info(
            "node=role_design provider=%s model=%s available_voices=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            len(available_voices),
        )
        extract_output = self._load_role_extract_output(project_dir)
        if not extract_output.roles:
            raise ValueError("role_design requires at least one role from role_extract")

        active_episode_keys = self._active_episode_keys_in_order(state) if getattr(self, "_active_episode_keys", None) else []
        target_extract_roles = self._role_design_target_extract_roles(extract_output.roles, state)
        if active_episode_keys:
            get_logger().info(
                "role_design episode-scoped rerun episodes=%s target_roles=%s",
                ",".join(active_episode_keys),
                ",".join(item.name for item in target_extract_roles) or "-",
            )
            if not target_extract_roles:
                get_logger().warning(
                    "role_design found no roles appearing in selected episodes: %s",
                    ",".join(active_episode_keys),
                )

        force_pregen = bool(getattr(self, "_force_pregen", False))
        self._clear_role_design_state(state)

        designed_by_key: dict[str, RoleDesignItem] = {}
        existing_output = self._load_existing_role_design_output(project_dir)
        if existing_output is not None:
            should_keep_existing = not force_pregen or bool(active_episode_keys)
            if should_keep_existing:
                for existing_item in existing_output.roles:
                    if not self._role_design_item_complete(existing_item):
                        continue
                    existing_key = self._role_name_key(existing_item.name)
                    designed_by_key[existing_key] = existing_item
                    self._apply_role_design_item(state, existing_item, speech_provider=speech_provider)

        all_role_extracts = [item.model_dump(mode="json") for item in extract_output.roles]
        for extract_item in target_extract_roles:
            role_key = self._role_name_key(extract_item.name)
            if role_key in designed_by_key and not force_pregen:
                get_logger().info("role_design %s already exists, skipped", extract_item.name)
                continue

            episode_keys = self._role_episode_keys(extract_item, state)
            role_novel_full = self._novel_full_contents(project_dir, state, episode_keys)
            get_logger().info(
                "role_design generating %s from episodes=%s chapters=%s",
                extract_item.name,
                ",".join(episode_keys),
                ",".join(extract_item.source_chapters) or "-",
            )
            output = await self.role_service.role_design(
                state,
                provider,
                role_item=extract_item,
                role_novel_full=role_novel_full,
                all_role_extracts=all_role_extracts,
                existing_role_designs=[
                    item.model_dump(mode="json")
                    for item in self._ordered_role_design_items(extract_output.roles, designed_by_key)
                ],
                available_voices=available_voices,
            )
            item = self._merge_role_extract_into_design(
                self._select_role_design_item(output, extract_item),
                extract_item,
            )
            self._apply_role_design_item(state, item, speech_provider=speech_provider)
            designed_by_key[role_key] = item
            state.budget.used_text_calls += 1
            state.metadata["role_design_generation_mode"] = "per_role_recursive"
            state.metadata["role_design_active_episode_keys"] = active_episode_keys
            state.metadata["role_design_target_role_names"] = [
                item.name for item in target_extract_roles
            ]
            state.metadata["role_design_designed_role_names"] = [
                item.name for item in self._ordered_role_design_items(extract_output.roles, designed_by_key)
            ]
            self.repo.save_node_output(
                project_dir,
                "role_design",
                RoleDesignOutput(roles=self._ordered_role_design_items(extract_output.roles, designed_by_key)),
            )
            self.repo.save_state(project_dir, state)

        final_items = self._ordered_role_design_items(extract_output.roles, designed_by_key)
        state.metadata["role_design_generation_mode"] = "per_role_recursive"
        state.metadata["role_design_active_episode_keys"] = active_episode_keys
        state.metadata["role_design_target_role_names"] = [item.name for item in target_extract_roles]
        state.metadata["role_design_designed_role_names"] = [item.name for item in final_items]
        self.repo.save_node_output(
            project_dir,
            "role_design",
            RoleDesignOutput(roles=final_items),
        )
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
            episode_stories=self._episode_stories(project_dir, state),
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
    def _shot_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "shots" / f"{episode_key}.json"

    def _load_storyboard_episode(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput:
        path = self._shot_path(project_dir, episode_key)
        if not path.exists():
            raise FileNotFoundError(f"Storyboard shot file not found: {path}")
        return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_storyboard_episode(self, project_dir: Path, episode: StoryboardEpisodeOutput) -> None:
        self.repo.write_json(
            self._shot_path(project_dir, episode.episode_key),
            episode.model_dump(mode="json", exclude_none=True),
        )

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
        if shot.ref_frame_asset_path:
            refs.append(
                AssetRef(
                    id=shot.ref_frame_asset_id,
                    type="image",
                    path=str(project_dir / shot.ref_frame_asset_path),
                    metadata={"asset_type": "ref_frame"},
                )
            )
        if self._video_reference_mode(provider) not in {"ref_frame_only", "ref_frame"}:
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
        output = await self.asset_service.role_appearance_design(
            state,
            provider,
            episode_stories=self._episode_stories(project_dir, state),
        )
        roles_by_key = self._role_lookup(state)
        unmatched_role_names: list[str] = []
        for item in output.appearances:
            role = self._resolve_role(roles_by_key, item.role_name)
            if role is None:
                unmatched_role_names.append(item.role_name)
                continue
            appearance_id = normalize_id(f"{role.id}_appearance", item.name)
            role_bound_prop_ids: list[str] = []
            for prop_item in item.role_bound_props:
                prop_id = normalize_id(f"{role.id}_prop", prop_item.name)
                status_key = self._prop_status_key(prop_item.status)
                if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
                    prop_id = f"{prop_id}_{status_key}"
                desc_parts = [prop_item.desc]
                if prop_item.scale_relation:
                    desc_parts.append(f"比例关系：{prop_item.scale_relation}")
                if prop_item.usage:
                    desc_parts.append(f"使用方式：{prop_item.usage}")
                state.props[prop_id] = Prop(
                    id=prop_id,
                    name=prop_item.name,
                    desc="；".join(part.strip("；") for part in desc_parts if part),
                    prompt=prop_item.prompt,
                    status=prop_item.status,
                    owner_role_id=role.id,
                    owner_role_name=role.name,
                    source="role_appearance_design",
                )
                role_bound_prop_ids.append(prop_id)
            role.appearances[item.name] = RoleAppearance(
                id=appearance_id,
                role_id=role.id,
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                role_bound_prop_ids=role_bound_prop_ids,
                intro_video_prompt=item.intro_video_prompt,
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
        try:
            video_provider = self.router.video("role")
        except Exception:
            video_provider = self.router.video("shot")
        get_logger().info(
            "node=role_appearance_generation image_provider=%s image_model=%s video_provider=%s video_model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            getattr(video_provider, "name", "unknown"),
            getattr(video_provider, "model", "-"),
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
                    "asset_type": "role_appearance",
                },
            )
            asset_path = await self._write_first_generated_image(
                project_dir,
                self._image_asset_path(project_dir, "roles", appearance.id),
                result,
            )
            appearance.asset_id = appearance.id
            appearance.asset_path = asset_path
            appearance.design_image_asset_id = appearance.id
            appearance.design_image_asset_path = asset_path
            appearance.provider = result.provider
            appearance.model = result.model
            appearance.request_id = result.request_id
            appearance.usage = result.usage
            for prop_id in appearance.role_bound_prop_ids:
                prop = state.props.get(prop_id)
                if prop is not None:
                    prop.asset_id = appearance.id
                    prop.asset_path = asset_path
                    prop.provider = result.provider
                    prop.model = result.model
                    prop.request_id = result.request_id
                    prop.usage = result.usage
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

            intro_video_asset_id = f"{appearance.id}_intro_video"
            intro_prompt = appearance.intro_video_prompt or (
                f"{role.name}站在洁净、亮度适中的虚空圆台上，圆台缓慢转动。"
                f"人物保持{appearance.desc}的稳定外观，做几个符合身份和性格的常见动作；"
                "如有随身物品，展示佩戴、握持或使用方式。背景干净抽象，无其他人物、无字幕、水印或文字标识。"
            )
            from autodrama.providers.base import AssetRef

            refs = [
                AssetRef(
                    id=appearance.id,
                    type="image",
                    path=str(project_dir / asset_path),
                    metadata={
                        "asset_type": "role_appearance",
                        "role_id": role.id,
                        "role_name": role.name,
                        "reference_for": intro_video_asset_id,
                    },
                )
            ]
            video_result = await video_provider.generate_video(
                intro_prompt,
                refs=refs,
                duration=8,
                wait=True,
                metadata={
                    "node_name": "role_appearance_generation",
                    "project_id": state.project_id,
                    "role_id": role.id,
                    "appearance_id": appearance.id,
                    "asset_id": intro_video_asset_id,
                    "asset_type": "role_appearance_video",
                },
            )
            video_asset_path = await self._write_generated_video(
                project_dir,
                self._video_asset_path(project_dir, "roles", intro_video_asset_id),
                video_result,
            )
            appearance.intro_video_asset_id = intro_video_asset_id
            appearance.intro_video_asset_path = video_asset_path
            generated.append(
                StaticAssetGenerationItem(
                    asset_id=intro_video_asset_id,
                    asset_type="role_appearance_video",
                    owner_id=role.id,
                    name=f"{role.name}/{appearance.name}/intro_video",
                    prompt=intro_prompt,
                    asset_path=video_asset_path,
                    provider=video_result.provider,
                    model=video_result.model,
                    request_id=video_result.request_id,
                    usage=video_result.usage,
                    raw_response=video_result.raw_response,
                )
            )
            get_logger().info("%s generated successfully, saved in %s", intro_video_asset_id, video_asset_path)
        self.repo.save_node_output(project_dir, "role_appearance_generation", StaticAssetGenerationOutput(generated_assets=generated))
        return state

    async def _run_prop_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("prop")
        get_logger().info(
            "node=prop_design provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.asset_service.prop_design(
            state,
            provider,
            episode_stories=self._episode_stories(project_dir, state),
        )
        role_bound_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.source in {"role_design", "role_appearance_design"} or prop.owner_role_id
        }
        global_props = {
            self._prop_asset_id(item.name, item.status): Prop(
                id=self._prop_asset_id(item.name, item.status),
                name=item.name,
                desc=item.desc,
                prompt=item.prompt,
                status=item.status,
                source="prop_design",
            )
            for item in output.props
        }
        state.props = {**role_bound_props, **global_props}
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "prop_design", output)
        return state

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
                metadata={
                    "asset_type": "role_appearance",
                    "role_id": role.id,
                    "role_name": role.name,
                    "reference_for": prop.id,
                },
            )
        ]

    async def _run_prop_image_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("prop")
        get_logger().info(
            "node=prop_image_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[StaticAssetGenerationItem] = []
        props = self._ordered_props_for_generation(list(state.props.values()))
        normal_props_by_base = self._normal_props_by_variant_base(props)
        get_logger().info(
            "node=prop_image_generation total_images=%d",
            len(props),
        )
        for prop in props:
            refs = None
            if getattr(provider, "supports_reference_images", False):
                refs = self._role_bound_prop_reference_refs(project_dir, state, prop)
                if refs is None:
                    refs = self._prop_reference_refs(project_dir, prop, normal_props_by_base)
            result = await provider.generate_image(
                prop.prompt,
                refs=refs,
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
        output = await self.asset_service.script_compress(
            state,
            provider,
            episode_stories=self._episode_stories(project_dir, state),
        )
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
        output = await self.asset_service.layout_design(
            state,
            provider,
            episode_stories=self._episode_stories(project_dir, state),
        )
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
        output = await self.asset_service.bgm_design(
            state,
            provider,
            episode_stories=self._episode_stories(project_dir, state),
        )
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
