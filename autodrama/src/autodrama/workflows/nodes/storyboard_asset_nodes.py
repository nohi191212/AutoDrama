from __future__ import annotations

import asyncio
import json
import re
from math import gcd
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autodrama.core.errors import ProviderBadResponseError
from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    ClipPromptEpisode,
    ClipPromptItem,
    ClipPromptModelOutput,
    ClipPromptOutput,
    ClipStoryboardPromptModelOutput,
    ClipSegmentOutput,
    ClipSegmentNodeOutput,
    ProjectState,
    ClipManifestGenerationEpisodeItem,
    ClipManifestGenerationOutput,
    ClipVideoInput,
    StoryboardEpisodeOutput,
    StoryboardKeyframeGenerationItem,
    StoryboardKeyframeGenerationOutput,
    StoryboardPromptClip,
    StoryboardPromptEpisode,
    StoryboardPromptOutput,
    StoryboardShot,
    StoryboardSheetGenerationItem,
    StoryboardSheetGenerationOutput,
)
from autodrama.providers.base import AssetRef
from autodrama.services.director_service import DirectorService
from autodrama.utils.video_prompts import sanitize_video_prompt_text
from autodrama.workflows.selection import clip_matches_selectors, normalize_clip_selectors
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase
from autodrama.workflows.runner import WorkflowNode

STORYBOARD_ASSET_NODE_NAMES = [
    "clip_prompt",
    "clip_storyboard_prompt",
    "clip_storyboard_image_generation",
    "clip_storyboard_keyframe_generation",
    "clip_manifest_generation",
]
STORYBOARD_IMAGE_PROVIDER_NODE_NAME = "clip_storyboard_image_generation"


class StoryboardAssetNodeBase(StaticAssetNodeBase):
    STORYBOARD_PANEL_COUNT = 12
    STORYBOARD_GRID = "4x3"
    STORYBOARD_PANEL_ASPECT_RATIO = "4:3"
    STORYBOARD_SHEET_SIZE = "3840x2160"
    TARGET_CLIP_SECONDS = 15
    TARGET_SHOT_SECONDS = TARGET_CLIP_SECONDS

    @staticmethod
    def _format_json(value: object) -> str:
        def to_jsonable(item: object) -> object:
            if isinstance(item, BaseModel):
                return item.model_dump(mode="json")
            if isinstance(item, dict):
                return {key: to_jsonable(val) for key, val in item.items()}
            if isinstance(item, list):
                return [to_jsonable(val) for val in item]
            if isinstance(item, tuple):
                return [to_jsonable(val) for val in item]
            return item

        value = to_jsonable(value)
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _ratio_from_value(value: object) -> str | None:
        text = str(value or "").strip().lower().replace("×", "x")
        if not text:
            return None
        if ":" in text:
            left, right = text.split(":", 1)
            try:
                width = int(float(left.strip()))
                height = int(float(right.strip()))
            except ValueError:
                return text
            if width > 0 and height > 0:
                return f"{width}:{height}"
        if "x" in text:
            left, right = text.split("x", 1)
            try:
                width = int(float(left.strip()))
                height = int(float(right.strip()))
            except ValueError:
                return text
            if width > 0 and height > 0:
                divisor = gcd(width, height)
                return f"{width // divisor}:{height // divisor}"
        return text

    @classmethod
    def _ratio_number(cls, ratio: str) -> float | None:
        if ":" not in ratio:
            return None
        left, right = ratio.split(":", 1)
        try:
            width = float(left)
            height = float(right)
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        return width / height

    def final_aspect_ratio(self) -> str:
        node_settings = self.repo.settings.nodes.get("clip_video_generation")
        if node_settings is not None:
            for param_name in ("ratio", "aspect_ratio"):
                ratio = self._ratio_from_value(node_settings.params.get(param_name))
                if ratio:
                    return ratio
        return "9:16"

    @classmethod
    def storyboard_grid(cls) -> str:
        return cls.STORYBOARD_GRID

    def storyboard_node_params(self) -> dict[str, Any]:
        node_settings = self.repo.settings.nodes.get(STORYBOARD_IMAGE_PROVIDER_NODE_NAME)
        return dict(node_settings.params) if node_settings is not None else {}

    def storyboard_panel_aspect_ratio(self) -> str:
        value = self.storyboard_node_params().get("storyboard_panel_aspect_ratio")
        return self._ratio_from_value(value) or self.STORYBOARD_PANEL_ASPECT_RATIO

    def storyboard_sheet_size(self) -> str:
        return str(self.storyboard_node_params().get("size") or self.STORYBOARD_SHEET_SIZE).strip()

    def storyboard_sheet_aspect_ratio(self) -> str:
        return self._ratio_from_value(self.storyboard_sheet_size()) or "16:9"

    def storyboard_sheet_resolution_label(self) -> str:
        size = self.storyboard_sheet_size()
        match = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", size, flags=re.IGNORECASE)
        if match and max(int(match.group(1)), int(match.group(2))) >= 3840:
            return f"{size}（4K）"
        return size

    def active_clip_selectors(self) -> set[str]:
        context = getattr(self.workflow, "_run_context", None)
        if context is not None and getattr(context, "has_clip_selectors", False):
            return normalize_clip_selectors(getattr(context, "clip_selectors", set()))
        return normalize_clip_selectors(getattr(self.workflow, "_active_clip_selectors", set()))

    @staticmethod
    def clip_matches_active_selectors(
        *,
        episode_key: str,
        clip: StoryboardPromptClip,
        clip_index: int,
        selectors: set[str],
    ) -> bool:
        if not selectors:
            return True
        return clip_matches_selectors(episode_key, clip.clip_id, clip_index, selectors)

    def target_episode_keys(self, state: ProjectState) -> list[str]:
        return self.active_episode_keys(state) or self.expected_episode_keys(state)

    def target_clip_count(self, state: ProjectState) -> int:
        duration = self.script_service.episode_duration_seconds(state)
        return max(1, int((duration + self.TARGET_CLIP_SECONDS - 1) // self.TARGET_CLIP_SECONDS))

    def target_shot_count(self, state: ProjectState) -> int:
        return self.target_clip_count(state)

    @staticmethod
    def clip_count_for_episode(episode: StoryboardPromptEpisode) -> int:
        return len(episode.clips)

    @staticmethod
    def shot_count_for_episode(episode: StoryboardPromptEpisode) -> int:
        return len(episode.clips)

    def load_clip_segment_output(self, project_dir: Path) -> ClipSegmentNodeOutput:
        episode_dir = self.layout.node_episode_output_path(project_dir, "clip_segment", "_").parent
        by_episode: dict[str, dict[str, object]] = {}
        if episode_dir.exists():
            for path in sorted(episode_dir.glob("episode_*.json")):
                output = ClipSegmentOutput.model_validate_json(path.read_text(encoding="utf-8"))
                by_episode[path.stem] = dict(output.root)
        if by_episode:
            return ClipSegmentNodeOutput(by_episode)

        legacy_path = self.layout.node_output_path(project_dir, "clip_segment")
        if legacy_path.exists():
            return ClipSegmentNodeOutput.model_validate_json(legacy_path.read_text(encoding="utf-8"))
        raise FileNotFoundError("clip_segment episode outputs are missing; run pregen through clip_segment first")

    def clip_segments_by_episode(self, project_dir: Path) -> dict[str, object]:
        return dict(self.load_clip_segment_output(project_dir).root)

    def load_clip_prompt_output(self, project_dir: Path) -> ClipPromptOutput:
        by_episode: dict[str, ClipPromptEpisode] = {}
        episode_dir = self.layout.node_episode_output_path(project_dir, "clip_prompt", "_").parent
        if episode_dir.exists():
            for episode_path in sorted(episode_dir.glob("episode_*.json")):
                episode = ClipPromptEpisode.model_validate_json(episode_path.read_text(encoding="utf-8"))
                by_episode[episode.episode_key] = episode

        # Legacy aggregate output remains a migration fallback only. Episode files win.
        legacy_path = self.layout.node_output_path(project_dir, "clip_prompt")
        if legacy_path.exists():
            legacy = ClipPromptOutput.model_validate_json(legacy_path.read_text(encoding="utf-8"))
            for episode in legacy.clip_prompts:
                by_episode.setdefault(episode.episode_key, episode)

        if not by_episode:
            raise FileNotFoundError("clip_prompt output is missing; run pregen through clip_prompt first")
        return ClipPromptOutput(clip_prompts=list(by_episode.values()))

    @staticmethod
    def clip_prompts_by_episode(output: ClipPromptOutput) -> dict[str, dict[str, ClipPromptItem]]:
        return {
            episode.episode_key: {clip.clip_id: clip for clip in episode.clips}
            for episode in output.clip_prompts
        }

    def episode_story_context(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> dict[str, str]:
        refs = state.script.novel_extract
        if not any(refs.get(episode_key) for episode_key in episode_keys):
            refs = state.script.novel_full
        return self.script_contents.load_contents(
            project_dir,
            refs,
            episode_keys,
            label="clip_storyboard_prompt.episode_stories",
            allow_missing=True,
        )

    def roleboard_context(self, project_dir: Path, state: ProjectState) -> list[dict[str, object]]:
        context: list[dict[str, object]] = []
        for role in state.roles.values():
            appearances: list[dict[str, object]] = []
            for appearance in role.appearances.values():
                appearances.append(
                    {
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "desc": appearance.desc,
                        "asset_path": appearance.asset_path or appearance.design_image_asset_path,
                        "asset_url": appearance.asset_url or appearance.design_image_asset_url,
                    }
                )
            context.append(
                {
                    "role_id": role.id,
                    "role_name": role.name,
                    "intro": role.intro,
                    "episode_keys": role.episode_keys,
                    "appearances": appearances,
                }
            )
        return context

    def layout_context(self, state: ProjectState) -> list[dict[str, object]]:
        return [
            {
                "layout_id": layout.id,
                "layout_name": layout.name,
                "desc": layout.desc,
                "episode_keys": layout.episode_keys,
                "asset_path": layout.asset_path,
                "asset_url": layout.asset_url,
            }
            for layout in state.layouts.values()
        ]

    def prop_context(self, state: ProjectState) -> list[dict[str, object]]:
        return [
            {
                "prop_id": prop.id,
                "prop_name": prop.name,
                "status": prop.status,
                "desc": prop.desc,
                "episode_keys": prop.episode_keys,
                "owner_role_id": prop.owner_role_id,
                "asset_path": prop.asset_path,
                "asset_url": prop.asset_url,
            }
            for prop in state.props.values()
        ]

    def load_clip_storyboard_prompt_output(self, project_dir: Path) -> StoryboardPromptOutput:
        path = self.layout.node_output_path(project_dir, "clip_storyboard_prompt")
        if not path.exists():
            raise FileNotFoundError("clip_storyboard_prompt output is missing; run pregen through clip_storyboard_prompt first")
        return StoryboardPromptOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_storyboard_sheet_output(self, project_dir: Path) -> StoryboardSheetGenerationOutput:
        path = self.layout.node_output_path(project_dir, "clip_storyboard_image_generation")
        if not path.exists():
            raise FileNotFoundError("clip_storyboard_image_generation output is missing; run pregen through clip_storyboard_image_generation first")
        return StoryboardSheetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_storyboard_keyframe_output(self, project_dir: Path) -> StoryboardKeyframeGenerationOutput:
        path = self.layout.node_output_path(project_dir, "clip_storyboard_keyframe_generation")
        if not path.exists():
            raise FileNotFoundError(
                "clip_storyboard_keyframe_generation output is missing; run pregen through clip_storyboard_keyframe_generation first"
            )
        return StoryboardKeyframeGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def storyboard_asset_id(clip_id: str) -> str:
        return f"{str(clip_id).strip()}_storyboard"

    @staticmethod
    def clip_id_for_episode_index(episode_key: str, index: int) -> str:
        return f"{episode_key}_clip_{index:03d}"

    @staticmethod
    def legacy_shot_id_for_episode_index(episode_key: str, index: int) -> str:
        return f"{episode_key}_shot_{index:03d}"

    @classmethod
    def shot_id_for_episode_index(cls, episode_key: str, index: int) -> str:
        return cls.clip_id_for_episode_index(episode_key, index)

    @staticmethod
    def _normalize_clip_id(value: str, episode_key: str, index: int) -> str:
        text = str(value or "").strip()
        legacy = f"{episode_key}_shot_{index:03d}"
        if not text or text == legacy:
            return f"{episode_key}_clip_{index:03d}"
        return text.replace("_shot_", "_clip_")

    @staticmethod
    def episode_key_from_shot_id(shot_id: str) -> str:
        text = str(shot_id or "").strip()
        if "_clip_" in text:
            return text.rsplit("_clip_", 1)[0]
        if "_shot_" in text:
            return text.rsplit("_shot_", 1)[0]
        return ""

    @staticmethod
    def _sorted_clip_segment_keys(clips: dict[str, object]) -> list[str]:
        return sorted(clips, key=lambda value: int(value) if str(value).isdigit() else 999999)

    @classmethod
    def _expected_clip_segments(
        cls,
        clip_segments_by_episode: dict[str, object],
        episode_keys: list[str],
    ) -> dict[str, dict[str, object]]:
        expected: dict[str, dict[str, object]] = {}
        missing: list[str] = []
        for episode_key in episode_keys:
            clips = clip_segments_by_episode.get(episode_key)
            if clips is None:
                missing.append(episode_key)
                continue
            expected[episode_key] = dict(clips)  # type: ignore[arg-type]
        if missing:
            raise ValueError(f"clip_storyboard_prompt missing clip_segment episode(s): {', '.join(missing)}")
        return expected

    @classmethod
    def _select_expected_clip_segments(
        cls,
        expected_clip_segments: dict[str, dict[str, object]],
        selectors: set[str],
    ) -> tuple[dict[str, dict[str, object]], set[str]]:
        if not selectors:
            return expected_clip_segments, set()

        selected_by_episode: dict[str, dict[str, object]] = {}
        selected_clip_ids: set[str] = set()
        for episode_key, source_clips in expected_clip_segments.items():
            selected_clips: dict[str, object] = {}
            for offset, source_key in enumerate(cls._sorted_clip_segment_keys(source_clips)):
                try:
                    clip_index = int(str(source_key).strip())
                except ValueError:
                    clip_index = offset + 1
                clip_id = cls.clip_id_for_episode_index(episode_key, clip_index)
                if not clip_matches_selectors(episode_key, clip_id, clip_index, selectors):
                    continue
                selected_clips[source_key] = source_clips[source_key]
                selected_clip_ids.add(clip_id)
            selected_by_episode[episode_key] = selected_clips
        return selected_by_episode, selected_clip_ids

    @classmethod
    def _expected_clip_ids_by_episode(
        cls,
        expected_clip_segments: dict[str, dict[str, object]],
    ) -> dict[str, list[str]]:
        ordered: dict[str, list[str]] = {}
        for episode_key, source_clips in expected_clip_segments.items():
            clip_ids: list[str] = []
            for offset, source_key in enumerate(cls._sorted_clip_segment_keys(source_clips)):
                try:
                    clip_index = int(str(source_key).strip())
                except ValueError:
                    clip_index = offset + 1
                clip_ids.append(cls.clip_id_for_episode_index(episode_key, clip_index))
            ordered[episode_key] = clip_ids
        return ordered

    @staticmethod
    def _normalize_panel_ref(value: object) -> str:
        text = str(value or "").strip().upper()
        match = re.search(r"P\s*([0-9]{1,2})", text)
        if not match:
            return text
        index = int(match.group(1))
        return f"P{index:02d}" if 1 <= index <= 12 else text

    @classmethod
    def _normalize_panel_plan(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, item in (value or {}).items():
            panel_ref = cls._normalize_panel_ref(key)
            normalized[panel_ref] = item
        return normalized

    @classmethod
    def _panel_refs_from_prompt(cls, value: str) -> set[str]:
        refs: set[str] = set()
        for match in re.finditer(r"(?<![A-Za-z0-9])P\s*(0?[1-9]|1[0-2])(?![A-Za-z0-9])", value or "", re.IGNORECASE):
            refs.add(cls._normalize_panel_ref(match.group(0)))
        return refs

    @staticmethod
    def _camera_shot_count(clip: StoryboardPromptClip) -> int:
        if clip.camera_shots:
            return len(clip.camera_shots)
        matches = re.findall(r"Camera\s+Shot\s+\d+", clip.video_prompt or "", flags=re.IGNORECASE)
        return len(set(match.lower() for match in matches))

    def validate_clip_storyboard_prompt_output(
        self,
        output: StoryboardPromptOutput,
        *,
        expected_episode_keys: list[str],
        expected_clip_segments: dict[str, dict[str, object]],
        require_complete: bool = True,
    ) -> StoryboardPromptOutput:
        expected = set(expected_episode_keys)
        seen: set[str] = set()
        seen_clips: set[str] = set()
        cleaned: list[StoryboardPromptEpisode] = []
        for episode in output.storyboards:
            episode.episode_key = str(episode.episode_key or "").strip()
            if episode.episode_key not in expected:
                raise ValueError(
                    f"clip_storyboard_prompt returned unexpected episode_key {episode.episode_key!r}; "
                    f"expected one of {', '.join(expected_episode_keys)}"
                )
            if episode.episode_key in seen:
                raise ValueError(f"clip_storyboard_prompt returned duplicate episode_key: {episode.episode_key}")
            seen.add(episode.episode_key)
            source_clips = expected_clip_segments.get(episode.episode_key) or {}
            ordered_source_keys = self._sorted_clip_segment_keys(source_clips)
            expected_by_id: dict[str, tuple[str, int]] = {}
            for offset, source_key in enumerate(ordered_source_keys):
                try:
                    expected_index = int(str(source_key).strip())
                except ValueError:
                    expected_index = offset + 1
                expected_by_id[self.clip_id_for_episode_index(episode.episode_key, expected_index)] = (
                    source_key,
                    expected_index,
                )
            validated_clips: list[StoryboardPromptClip] = []
            returned_clip_ids: set[str] = set()
            for clip in episode.clips:
                candidate_clip_id = str(clip.clip_id or "").strip().replace("_shot_", "_clip_")
                expected_item = expected_by_id.get(candidate_clip_id)
                if expected_item is None:
                    raise ValueError(
                        f"clip_storyboard_prompt returned clip_id outside requested batch for {episode.episode_key}: "
                        f"got {clip.clip_id or '-'}; expected one of {', '.join(expected_by_id) or '-'}"
                    )
                source_key, expected_index = expected_item
                expected_clip_id = self.clip_id_for_episode_index(episode.episode_key, expected_index)
                clip.clip_id = expected_clip_id
                if clip.clip_id in seen_clips:
                    raise ValueError(f"clip_storyboard_prompt returned duplicate clip_id: {clip.clip_id}")
                seen_clips.add(clip.clip_id)
                returned_clip_ids.add(clip.clip_id)
                try:
                    clip.duration_seconds = float(clip.duration_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"clip_storyboard_prompt invalid duration_seconds for {clip.clip_id}") from exc
                if not 8 <= clip.duration_seconds <= 15 and getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt duration_seconds for %s is outside 8-15s guidance: %s",
                        clip.clip_id,
                        clip.duration_seconds,
                    )
                clip.role_ids = self._dedupe_nonempty_texts(clip.role_ids)
                clip.layout_ids = self._dedupe_nonempty_texts(clip.layout_ids)
                clip.prop_ids = self._dedupe_nonempty_texts(clip.prop_ids)
                if not clip.layout_ids:
                    raise ValueError(f"clip_storyboard_prompt layout_ids is empty for {clip.clip_id}")
                clip.video_prompt = sanitize_video_prompt_text(str(clip.video_prompt or "").strip())
                if not clip.video_prompt:
                    raise ValueError(f"clip_storyboard_prompt returned empty video_prompt for {clip.clip_id}")
                source_clip = source_clips[source_key]
                source_text = self._clip_segment_text(source_clip)
                if not clip.clip_text:
                    clip.clip_text = source_text
                if not clip.clip_title:
                    clip.clip_title = f"Clip {expected_index:03d}"
                if not clip.clip_duration_hint:
                    clip.clip_duration_hint = f"{clip.duration_seconds:g}s"
                clip.panel_plan = self._normalize_panel_plan(clip.panel_plan)
                required_panels = {f"P{index:02d}" for index in range(1, self.STORYBOARD_PANEL_COUNT + 1)}
                panel_refs = set(clip.panel_plan) or self._panel_refs_from_prompt(clip.video_prompt)
                missing_panels = sorted(required_panels.difference(panel_refs))
                if missing_panels:
                    raise ValueError(
                        f"clip_storyboard_prompt panel_plan for {clip.clip_id} must cover P01-P12; "
                        f"missing {', '.join(missing_panels)}"
                    )
                camera_shot_count = self._camera_shot_count(clip)
                if camera_shot_count < 1:
                    raise ValueError(f"clip_storyboard_prompt {clip.clip_id} must include at least one Camera Shot")
                if not 2 <= camera_shot_count <= 4 and getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt %s has %d Camera Shot(s); recommended range is 2-4",
                        clip.clip_id,
                        camera_shot_count,
                    )
                validated_clips.append(clip)
            if require_complete:
                missing_clip_ids = sorted(set(expected_by_id).difference(returned_clip_ids))
                if missing_clip_ids:
                    raise ValueError(
                        f"clip_storyboard_prompt must return {len(ordered_source_keys)} clips for "
                        f"{episode.episode_key}; got {len(validated_clips)}; missing: "
                        f"{', '.join(missing_clip_ids)}"
                    )
            episode.clips = validated_clips
            cleaned.append(episode)
        missing = [episode_key for episode_key in expected_episode_keys if episode_key not in seen]
        if missing and require_complete:
            raise ValueError(f"clip_storyboard_prompt missing episode(s): {', '.join(missing)}")
        for episode_key in missing:
            cleaned.append(StoryboardPromptEpisode(episode_key=episode_key, clips=[]))
        return StoryboardPromptOutput(
            storyboards=[
                next(episode for episode in cleaned if episode.episode_key == episode_key)
                for episode_key in expected_episode_keys
            ]
        )

    def merge_clip_storyboard_prompt_outputs(
        self,
        *,
        project_dir: Path,
        generated_output: StoryboardPromptOutput,
        target_episode_keys: list[str],
        all_episode_keys: list[str],
        selected_clip_ids: set[str] | None = None,
        expected_clip_ids_by_episode: dict[str, list[str]] | None = None,
    ) -> StoryboardPromptOutput:
        existing_path = self.layout.node_output_path(project_dir, "clip_storyboard_prompt")
        by_episode: dict[str, StoryboardPromptEpisode] = {}
        if existing_path.exists():
            try:
                existing = StoryboardPromptOutput.model_validate_json(existing_path.read_text(encoding="utf-8"))
                by_episode.update({episode.episode_key: episode for episode in existing.storyboards})
            except Exception as exc:
                self.logger.warning("clip_storyboard_prompt ignored invalid existing output %s: %s", existing_path, exc)
        if selected_clip_ids is None:
            by_episode.update({episode.episode_key: episode for episode in generated_output.storyboards})
        else:
            expected_clip_ids_by_episode = expected_clip_ids_by_episode or {}
            for generated_episode in generated_output.storyboards:
                generated_clips = [
                    clip for clip in generated_episode.clips if clip.clip_id in selected_clip_ids
                ]
                existing_episode = by_episode.get(generated_episode.episode_key)
                if existing_episode is None:
                    if generated_clips:
                        by_episode[generated_episode.episode_key] = StoryboardPromptEpisode(
                            episode_key=generated_episode.episode_key,
                            clips=generated_clips,
                        )
                    continue

                clips_by_id = {clip.clip_id: clip for clip in existing_episode.clips}
                clips_by_id.update({clip.clip_id: clip for clip in generated_clips})
                ordered_clip_ids: list[str] = []
                seen_clip_ids: set[str] = set()
                for clip_id in expected_clip_ids_by_episode.get(generated_episode.episode_key, []):
                    if clip_id in clips_by_id and clip_id not in seen_clip_ids:
                        ordered_clip_ids.append(clip_id)
                        seen_clip_ids.add(clip_id)
                for clip in [*existing_episode.clips, *generated_clips]:
                    if clip.clip_id not in seen_clip_ids:
                        ordered_clip_ids.append(clip.clip_id)
                        seen_clip_ids.add(clip.clip_id)
                by_episode[generated_episode.episode_key] = StoryboardPromptEpisode(
                    episode_key=generated_episode.episode_key,
                    clips=[clips_by_id[clip_id] for clip_id in ordered_clip_ids],
                )
        ordered_keys = all_episode_keys if not set(target_episode_keys).difference(all_episode_keys) else target_episode_keys
        return StoryboardPromptOutput(
            storyboards=[by_episode[episode_key] for episode_key in ordered_keys if episode_key in by_episode]
        )

    @classmethod
    def _dedupe_nonempty_texts(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            text = " ".join(str(value or "").split()).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    def storyboard_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        shot: StoryboardPromptClip,
        *,
        limit: int,
    ) -> list[AssetRef]:
        refs: list[AssetRef] = []

        def append(ref: AssetRef) -> bool:
            if len(refs) >= limit:
                return False
            refs.append(ref)
            return len(refs) < limit

        for layout_id in shot.layout_ids:
            layout = state.layouts.get(layout_id)
            if layout is None:
                continue
            existing = self.layout.existing_project_file(project_dir, layout.asset_path)
            if not existing and not layout.asset_url:
                continue
            if not append(
                AssetRef(
                    id=layout.asset_id or layout.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=layout.asset_url,
                    metadata={
                        "asset_type": "layout",
                        "layout_id": layout.id,
                        "layout_name": layout.name,
                        "reference_for": "storyboard",
                    },
                )
            ):
                return refs

        for role_id in shot.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if appearance is None:
                continue
            asset_path = appearance.asset_path or appearance.design_image_asset_path
            asset_url = appearance.asset_url or appearance.design_image_asset_url
            existing = self.layout.existing_project_file(project_dir, asset_path)
            if not existing and not asset_url:
                continue
            if not append(
                AssetRef(
                    id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=asset_url,
                    metadata={
                        "asset_type": "roleboard",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "reference_for": "storyboard",
                    },
                )
            ):
                return refs

        for prop_id in shot.prop_ids:
            prop = state.props.get(prop_id)
            if prop is None:
                continue
            existing = self.layout.existing_project_file(project_dir, prop.asset_path)
            if not existing and not prop.asset_url:
                continue
            if not append(
                AssetRef(
                    id=prop.asset_id or prop.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=prop.asset_url,
                    metadata={
                        "asset_type": "prop",
                        "prop_id": prop.id,
                        "prop_name": prop.name,
                        "reference_for": "storyboard",
                    },
                )
            ):
                return refs

        return refs[:limit]

    @staticmethod
    def _panel_camera_shot_numbers(clip: StoryboardPromptClip) -> list[int]:
        numbers: list[int] = []
        previous = 1
        for panel_index in range(1, 13):
            panel_ref = f"P{panel_index:02d}"
            panel = clip.panel_plan.get(panel_ref)
            panel_text = panel if isinstance(panel, str) else json.dumps(panel, ensure_ascii=False)
            match = re.search(r"Camera\s+Shot\s*(\d+)", panel_text or "", flags=re.IGNORECASE)
            if match is not None:
                previous = max(1, min(99, int(match.group(1))))
            numbers.append(previous)
        return numbers

    @staticmethod
    def _storyboard_panel_image_content(panel_ref: str, value: object) -> str:
        text = str(value or "").strip()
        text = re.sub(
            rf"^\s*{re.escape(panel_ref)}\s*[（(]\s*Camera\s+Shot\s*\d+\s*[）)]\s*[:：]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"^\s*左上角(?=[^；;。]{0,100}黑色)(?=[^；;。]{0,100}红色)[^；;。]{0,100}[；;。]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"[；;，,]?\s*P(?:0[1-9]|1[0-2])\s*与\s*P(?:0[1-9]|1[0-2])[^。；;]*?红色斜杠[^。；;]*[。；;]?",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"[；;，,]?\s*切镜标记\s*[:：][^。；;]*[。；;]?",
            "",
            text,
        )
        text = re.sub(
            r"(?:黑色|灰色)(?=[^，。；;]{0,20}(?:前推|推进|推近|前进|环绕|上摇|下压|拉焦|对焦|镜头|手持|震颤)[^，。；;]{0,10}箭头)",
            "蓝色",
            text,
        )
        camera_arrow_pattern = re.compile(
            r"(?P<arrow>(?:极轻微|轻微|缓慢|快速|微小)?(?:手持)?"
            r"(?:前推|推进|推近|前进|环绕|上摇|下压|拉焦|对焦|震颤)箭头)"
        )

        def color_camera_arrow(match: re.Match[str]) -> str:
            prefix = text[max(0, match.start() - 20) : match.start()]
            if "蓝色" in prefix:
                return match.group(0)
            return "蓝色" + match.group("arrow")

        text = camera_arrow_pattern.sub(color_camera_arrow, text)
        return re.sub(r"\s+", " ", text).strip(" ；;，,")

    @staticmethod
    def _storyboard_cut_boundary_label(panel_index: int) -> str:
        next_index = panel_index + 1
        if panel_index in {4, 8}:
            position = "两行之间的水平分隔线中央"
        else:
            position = "两格之间的竖直分隔线中央"
        return f"{panel_index:02d}-{next_index:02d}（{position}）"

    def compose_storyboard_image_prompt(self, _episode_key: str, clip: StoryboardPromptClip) -> str:
        sheet_ratio = self.storyboard_sheet_aspect_ratio()
        panel_ratio = self.storyboard_panel_aspect_ratio()
        resolution = self.storyboard_sheet_resolution_label()
        shot_numbers = self._panel_camera_shot_numbers(clip)
        panel_lines: list[str] = []
        for panel_index in range(1, self.STORYBOARD_PANEL_COUNT + 1):
            panel_ref = f"P{panel_index:02d}"
            panel_text = self._storyboard_panel_image_content(panel_ref, clip.panel_plan.get(panel_ref))
            panel_lines.append(f"{panel_index:02d} | {shot_numbers[panel_index - 1]:02d}：{panel_text}")
        cut_boundaries = [
            self._storyboard_cut_boundary_label(panel_index)
            for panel_index in range(1, self.STORYBOARD_PANEL_COUNT)
            if shot_numbers[panel_index - 1] != shot_numbers[panel_index]
        ]
        cut_rule = (
            "只在以下真实切镜边界各画一条红色斜杠：" + "；".join(cut_boundaries) + "。其他分隔线不得画红色斜杠。"
            if cut_boundaries
            else "本 clip 没有真实切镜边界，不画红色斜杠。"
        )
        return "\n".join(
            [
                "生成一张可直接用于影视预演的十二宫格分镜故事板整图。",
                "【版式】",
                f"使用 {sheet_ratio} 横向画布，目标输出 {resolution}；严格 4 列 x 3 行，共 12 个等大宫格，每格保持 {panel_ratio}。宫格之间只有清楚、笔直、等距的黑色分隔线，不设置底部说明带或全局图例。",
                "【画风与彩色制作标记】",
                "主体画面使用黑白铅笔预演风格：轻量粗线、快速手势、简化细节、清楚轮廓和明确空间层次，不画成彩色成片。只允许制作标记使用颜色：蓝色箭头表示镜头运动、变焦或对焦；橙色箭头表示人物或物体动作；紫色波纹箭头表示声音来源或传播；绿色细箭头表示视线或走位；黄色细箭头表示主光方向；红色只用于镜头号和切镜斜杠。标记保持少量、醒目、不遮挡主体，不绘制颜色图例或文字说明。",
                "【编号与切镜】",
                "每格左上角直接绘制且只绘制一组并排双编号：左侧为黑色两位面板号，右侧为红色两位 Camera Shot 号。下方逐格清单每行开头的格式是“黑色面板号 | 红色镜头号”；竖线只用于分隔输入数据，不得画入图中。画面中只绘制两个两位数字，不加字母、文字或其他前缀，例如黑色 01 与红色 01。两个编号都是最终画面内容，不得遗漏或生成第二套。",
                cut_rule,
                "红色切镜斜杠必须居中压在指定分隔线上，单条、清晰、短小，不得进入宫格内部，不得压住人物或关键道具。",
                "【逐格画面】",
                "严格按以下 01-12 顺序绘制，不得漏画、合并或重排。同一 Camera Shot 的连续面板表现同一镜头内的动作、对焦或构图发展，不得误画成新的硬切。",
                *panel_lines,
                "【一致性与禁用项】",
                "保持参考图中的人物外观、服装、场景空间和道具造型一致，不新增剧情事实、角色、场景或道具。环境只保留帮助剧情和空间关系的元素。",
                "除每格唯一的黑色面板号与红色镜头号外，不生成任何可读文字：禁止字幕、对白、拟声词、气泡、制作注释、CUT、颜色图例、水印、logo、文件名、项目名、资产 ID 和二维码。",
            ]
        )


class StoryboardPromptNode(StoryboardAssetNodeBase):
    name = "clip_storyboard_prompt"
    MAX_CLIPS_PER_BATCH = 4
    MAX_MISSING_CLIP_RETRY_ROUNDS = 3
    DEFAULT_BATCH_CONCURRENCY = 3
    MAX_BATCH_CONCURRENCY = 8

    @staticmethod
    def _match_key(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "").strip()).casefold()

    @classmethod
    def batch_generation_concurrency(cls, provider: object) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in (
                "clip_storyboard_prompt_concurrency",
                "clip_storyboard_prompt_batch_concurrency",
                "text_generation_concurrency",
                "max_concurrent_text_calls",
                "concurrency",
            ):
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in (
                "clip_storyboard_prompt_concurrency",
                "clip_storyboard_prompt_batch_concurrency",
                "text_generation_concurrency",
                "max_concurrent_text_calls",
                "concurrency",
            ):
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = cls.DEFAULT_BATCH_CONCURRENCY
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = cls.DEFAULT_BATCH_CONCURRENCY
        return max(1, min(cls.MAX_BATCH_CONCURRENCY, resolved))

    @staticmethod
    def _append_unique(values: list[str], value: object) -> None:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)

    def _clip_segment_batches(self, source_clips: dict[str, object]) -> list[dict[str, object]]:
        ordered_keys = self._sorted_clip_segment_keys(source_clips)
        batches: list[dict[str, object]] = []
        for start in range(0, len(ordered_keys), self.MAX_CLIPS_PER_BATCH):
            batch_keys = ordered_keys[start : start + self.MAX_CLIPS_PER_BATCH]
            batches.append({key: source_clips[key] for key in batch_keys})
        return batches

    @classmethod
    def _clip_segment_values(cls, clip: object, field_name: str) -> list[str]:
        values = getattr(clip, field_name, None)
        if values is None and isinstance(clip, dict):
            values = clip.get(field_name)
        if not isinstance(values, list):
            return []
        return [str(value).strip() for value in values if str(value or "").strip()]

    @classmethod
    def _clip_segment_text(cls, clip: object) -> str:
        if isinstance(clip, dict):
            return str(clip.get("text") or "").strip()
        return str(getattr(clip, "text", "") or "").strip()

    def _batch_segment_names(self, batch_clips: dict[str, object], field_name: str) -> list[str]:
        names: list[str] = []
        for clip in batch_clips.values():
            for name in self._clip_segment_values(clip, field_name):
                self._append_unique(names, name)
        return names

    def _batch_segment_text(self, batch_clips: dict[str, object]) -> str:
        return "\n".join(
            text
            for clip in batch_clips.values()
            if (text := self._clip_segment_text(clip))
        )

    def _episode_window_keys(self, episode_key: str, all_episode_keys: list[str]) -> list[str]:
        if episode_key not in all_episode_keys:
            return [episode_key]
        index = all_episode_keys.index(episode_key)
        start = max(0, index - 1)
        end = min(len(all_episode_keys), index + 2)
        return all_episode_keys[start:end]

    def _episode_window_full_context(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        all_episode_keys: list[str],
    ) -> dict[str, str]:
        window_keys = self._episode_window_keys(episode_key, all_episode_keys)
        contents = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            window_keys,
            label="clip_storyboard_prompt.episode_window_full",
            allow_missing=True,
        )
        if episode_key not in contents:
            contents.update(self.episode_story_context(project_dir, state, [episode_key]))
        return {key: contents[key] for key in window_keys if key in contents}

    def _storyboard_episode_context(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        all_episode_keys: list[str],
    ) -> tuple[str, dict[str, str]]:
        full = self._episode_window_full_context(project_dir, state, episode_key, all_episode_keys)
        current_full = full.get(episode_key, "")
        adjacent_keys = [key for key in self._episode_window_keys(episode_key, all_episode_keys) if key != episode_key]
        adjacent_summaries = self.episode_story_context(project_dir, state, adjacent_keys)
        return current_full, adjacent_summaries

    def _storyboard_visual_tone(self, state: ProjectState) -> str:
        return str(
            self.asset_service.visual_tone(state)
            or state.metadata.get("visual_style_prompt")
            or self.repo.settings.generation.visual_style_prompt
            or ""
        ).strip()

    def _role_ids_for_batch(
        self,
        state: ProjectState,
        *,
        episode_key: str,
        batch_clips: dict[str, object],
    ) -> list[str]:
        by_name: dict[str, str] = {}
        for role in state.roles.values():
            for candidate in [role.id, role.name, normalize_id("role", role.name), *role.aliases]:
                key = self._match_key(candidate)
                if key:
                    by_name.setdefault(key, role.id)

        role_ids: list[str] = []
        for name in self._batch_segment_names(batch_clips, "role_names"):
            role_id = by_name.get(self._match_key(name))
            if role_id:
                self._append_unique(role_ids, role_id)

        batch_text = self._batch_segment_text(batch_clips)
        for role in state.roles.values():
            if role.id in role_ids:
                continue
            if role.episode_keys and episode_key not in role.episode_keys:
                continue
            names = [role.name, *role.aliases]
            if any(name and name in batch_text for name in names):
                self._append_unique(role_ids, role.id)
        return role_ids

    def _layout_ids_for_batch(
        self,
        state: ProjectState,
        *,
        episode_key: str,
        batch_clips: dict[str, object],
    ) -> list[str]:
        by_name: dict[str, str] = {}
        for layout in state.layouts.values():
            for candidate in [layout.id, layout.name, normalize_id("layout", layout.name)]:
                key = self._match_key(candidate)
                if key:
                    by_name.setdefault(key, layout.id)

        layout_ids: list[str] = []
        for name in self._batch_segment_names(batch_clips, "layout_names"):
            layout_id = by_name.get(self._match_key(name))
            if layout_id:
                self._append_unique(layout_ids, layout_id)

        batch_text = self._batch_segment_text(batch_clips)
        for layout in state.layouts.values():
            if layout.id in layout_ids:
                continue
            if layout.episode_keys and episode_key not in layout.episode_keys:
                continue
            if layout.name and layout.name in batch_text:
                self._append_unique(layout_ids, layout.id)
        if not layout_ids:
            episode_layouts = [
                layout.id
                for layout in state.layouts.values()
                if not layout.episode_keys or episode_key in layout.episode_keys
            ]
            for layout_id in episode_layouts:
                self._append_unique(layout_ids, layout_id)
        return layout_ids

    def _prop_ids_for_batch(
        self,
        state: ProjectState,
        *,
        episode_key: str,
        batch_clips: dict[str, object],
    ) -> list[str]:
        by_name: dict[str, str] = {}
        for prop in state.props.values():
            for candidate in [prop.id, prop.name, normalize_id("prop", prop.name)]:
                key = self._match_key(candidate)
                if key:
                    by_name.setdefault(key, prop.id)

        prop_ids: list[str] = []
        for name in self._batch_segment_names(batch_clips, "prop_names"):
            prop_id = by_name.get(self._match_key(name))
            if prop_id:
                self._append_unique(prop_ids, prop_id)

        batch_text = self._batch_segment_text(batch_clips)
        for prop in state.props.values():
            if prop.id in prop_ids:
                continue
            if prop.episode_keys and episode_key not in prop.episode_keys:
                continue
            if prop.name and prop.name in batch_text:
                self._append_unique(prop_ids, prop.id)
        return prop_ids

    def _roleboard_context_for_ids(
        self,
        project_dir: Path,
        state: ProjectState,
        role_ids: list[str],
    ) -> list[dict[str, object]]:
        wanted = set(role_ids)
        return [
            item
            for item in self.roleboard_context(project_dir, state)
            if str(item.get("role_id") or "") in wanted
        ]

    def _layout_context_for_ids(self, state: ProjectState, layout_ids: list[str]) -> list[dict[str, object]]:
        wanted = set(layout_ids)
        return [
            item
            for item in self.layout_context(state)
            if str(item.get("layout_id") or "") in wanted
        ]

    def _prop_context_for_ids(self, state: ProjectState, prop_ids: list[str]) -> list[dict[str, object]]:
        wanted = set(prop_ids)
        return [
            item
            for item in self.prop_context(state)
            if str(item.get("prop_id") or "") in wanted
        ]

    def _clip_storyboard_prompt_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        role_ids: list[str],
        layout_ids: list[str],
        prop_ids: list[str],
    ) -> tuple[list[AssetRef], list[dict[str, object]]]:
        refs: list[AssetRef] = []
        context: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()

        def append(ref: AssetRef, item: dict[str, object]) -> None:
            key = (str(ref.id or ""), str(ref.path or ref.url or ""))
            if key in seen:
                return
            seen.add(key)
            refs.append(ref)
            item["input_slot"] = f"image_{len(refs)}"
            context.append(item)

        for role_id in role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if appearance is None:
                continue
            asset_path = appearance.asset_path or appearance.design_image_asset_path
            asset_url = appearance.asset_url or appearance.design_image_asset_url
            existing = self.layout.existing_project_file(project_dir, asset_path)
            if not existing and not asset_url:
                continue
            append(
                AssetRef(
                    id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=asset_url,
                    metadata={
                        "asset_type": "roleboard",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "reference_for": "clip_storyboard_prompt",
                    },
                ),
                {
                    "asset_type": "roleboard",
                    "role_id": role.id,
                    "role_name": role.name,
                    "appearance_id": appearance.id,
                    "appearance_name": appearance.name,
                    "name": (
                        role.name
                        if appearance.name.strip().casefold() in {"", "base", "default"}
                        else f"{role.name}（{appearance.name}）"
                    ),
                    "description": appearance.desc or role.intro,
                },
            )

        for layout_id in layout_ids:
            layout = state.layouts.get(layout_id)
            if layout is None:
                continue
            existing = self.layout.existing_project_file(project_dir, layout.asset_path)
            if not existing and not layout.asset_url:
                continue
            append(
                AssetRef(
                    id=layout.asset_id or layout.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=layout.asset_url,
                    metadata={
                        "asset_type": "layout",
                        "layout_id": layout.id,
                        "layout_name": layout.name,
                        "reference_for": "clip_storyboard_prompt",
                    },
                ),
                {
                    "asset_type": "layout",
                    "layout_id": layout.id,
                    "layout_name": layout.name,
                    "name": layout.name,
                    "description": layout.desc,
                },
            )
        for prop_id in prop_ids:
            prop = state.props.get(prop_id)
            if prop is None:
                continue
            existing = self.layout.existing_project_file(project_dir, prop.asset_path)
            if not existing and not prop.asset_url:
                continue
            append(
                AssetRef(
                    id=prop.asset_id or prop.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=prop.asset_url,
                    metadata={
                        "asset_type": "prop",
                        "prop_id": prop.id,
                        "prop_name": prop.name,
                        "reference_for": "clip_storyboard_prompt",
                    },
                ),
                {
                    "asset_type": "prop",
                    "prop_id": prop.id,
                    "prop_name": prop.name,
                    "name": prop.name,
                    "description": prop.desc,
                },
            )
        return refs, context

    @staticmethod
    def _reference_image_prompt_prefix(reference_image_context: list[dict[str, object]]) -> str:
        entries: list[str] = []
        for image_index, item in enumerate(reference_image_context, start=1):
            name = re.sub(r"\s+", " ", str(item.get("name") or "").strip())
            description = re.sub(r"\s+", " ", str(item.get("description") or "").strip())
            sentence_match = re.match(r"^(.+?[。！？!?])", description)
            if sentence_match:
                description = sentence_match.group(1)
            if not name or not description:
                continue
            entries.append(f"图{image_index}是【{name}：{description}】")
        return "，".join(entries) + "。" if entries else ""

    @staticmethod
    def _camera_shots_from_storyboard_prompt(prompt: str) -> list[dict[str, str]]:
        section_match = re.search(r"<CAMERA_SHOTS>\s*(.*?)\s*</CAMERA_SHOTS>", prompt or "", re.IGNORECASE | re.DOTALL)
        if not section_match:
            return []
        section = section_match.group(1)
        pattern = re.compile(
            r"(?im)^\s*(Camera\s+Shot\s+\d+)\s*[（(]([^）)]+)[）)]\s*[:：]?\s*"
        )
        matches = list(pattern.finditer(section))
        shots: list[dict[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
            description = section[match.end() : end].strip()
            shots.append(
                {
                    "camera_shot_id": " ".join(match.group(1).split()),
                    "time_range": match.group(2).strip(),
                    "description": description,
                }
            )
        return shots

    @classmethod
    def _panel_plan_from_storyboard_prompt(cls, prompt: str) -> dict[str, str]:
        section_match = re.search(r"<PANEL_PLAN>\s*(.*?)\s*</PANEL_PLAN>", prompt or "", re.IGNORECASE | re.DOTALL)
        if not section_match:
            return {}
        pattern = re.compile(
            r"<P(0[1-9]|1[0-2])\s+camera_shot=[\"'](\d+)[\"']\s*>(.*?)</P\1>",
            re.IGNORECASE | re.DOTALL,
        )
        panels: dict[str, str] = {}
        for match in pattern.finditer(section_match.group(1)):
            panel_ref = f"P{match.group(1)}"
            panels[panel_ref] = f"{panel_ref}（Camera Shot {match.group(2)}）：{match.group(3).strip()}"
        return panels

    @staticmethod
    def _storyboard_prompt_text_for_downstream(prompt: str) -> str:
        text = re.sub(r"</?(?:CAMERA_SHOTS|PANEL_PLAN)>", "\n", prompt or "", flags=re.IGNORECASE)
        text = re.sub(
            r"<P(0[1-9]|1[0-2])\s+camera_shot=[\"'](\d+)[\"']\s*>",
            lambda match: f"\nP{match.group(1)}（Camera Shot {match.group(2)}）：",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"</P(?:0[1-9]|1[0-2])>", "\n", text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _storyboard_prompt_dialogue(source_text: str) -> list[str]:
        matches = re.findall(
            r"「([^」]+)」|『([^』]+)』|“([^”]+)”|‘([^’]+)’|\"([^\"]+)\"",
            source_text or "",
        )
        return [
            fragment.strip()
            for match in matches
            if (fragment := next((value for value in match if value), "")).strip()
        ]

    @staticmethod
    def _normalize_dialogue_for_comparison(value: str) -> str:
        return re.sub(r"[\s，。！？；：、,.!?;:“”‘’\"'「」『』…—-]+", "", value or "")

    @staticmethod
    def _storyboard_time_bounds(value: str) -> tuple[float, float] | None:
        numbers = re.findall(r"\d+(?:\.\d+)?", value or "")
        if len(numbers) < 2:
            return None
        return float(numbers[0]), float(numbers[1])

    def _clip_storyboard_prompt_errors(
        self,
        *,
        prompt: str,
        source_text: str,
        target_duration_seconds: int,
        camera_shots: list[dict[str, str]],
        panel_plan: dict[str, str],
    ) -> list[str]:
        errors: list[str] = []
        if "<CAMERA_SHOTS>" not in prompt or "</CAMERA_SHOTS>" not in prompt:
            errors.append("missing CAMERA_SHOTS markers")
        if "<PANEL_PLAN>" not in prompt or "</PANEL_PLAN>" not in prompt:
            errors.append("missing PANEL_PLAN markers")
        if not 2 <= len(camera_shots) <= 4:
            errors.append(f"camera shot count must be 2-4, got {len(camera_shots)}")
        expected_shot_ids = [f"Camera Shot {index}" for index in range(1, len(camera_shots) + 1)]
        actual_shot_ids = [str(shot.get("camera_shot_id") or "") for shot in camera_shots]
        if actual_shot_ids != expected_shot_ids:
            errors.append("camera shot ids must be sequential from Camera Shot 1")
        required_panels = {f"P{index:02d}" for index in range(1, 13)}
        missing_panels = sorted(required_panels.difference(panel_plan))
        if missing_panels:
            errors.append("missing panels: " + ", ".join(missing_panels))
        raw_panel_refs = re.findall(r"<P(0[1-9]|1[0-2])\b", prompt, re.IGNORECASE)
        if len(raw_panel_refs) != 12 or len(set(ref.upper() for ref in raw_panel_refs)) != 12:
            errors.append("panel tags must contain P01-P12 exactly once")
        bounds = [self._storyboard_time_bounds(str(shot.get("time_range") or "")) for shot in camera_shots]
        if any(item is None for item in bounds):
            errors.append("invalid camera shot time range")
        elif bounds:
            resolved = [item for item in bounds if item is not None]
            if abs(resolved[0][0]) > 0.01:
                errors.append("camera timeline must start at 0")
            for previous, current in zip(resolved, resolved[1:]):
                if abs(previous[1] - current[0]) > 0.01:
                    errors.append("camera timeline must be continuous")
                    break
            if abs(resolved[-1][1] - target_duration_seconds) > 0.01:
                errors.append(
                    f"camera timeline must end at {target_duration_seconds}s, got {resolved[-1][1]:g}s"
                )
        for shot_index in range(1, len(camera_shots) + 1):
            if not any(f"Camera Shot {shot_index}）" in value for value in panel_plan.values()):
                errors.append(f"Camera Shot {shot_index} has no panel")
        panel_shot_refs = {
            int(match.group(1))
            for value in panel_plan.values()
            if (match := re.search(r"Camera Shot (\d+)）", value))
        }
        invalid_panel_shots = sorted(panel_shot_refs.difference(range(1, len(camera_shots) + 1)))
        if invalid_panel_shots:
            errors.append("panels reference unknown Camera Shot: " + ", ".join(map(str, invalid_panel_shots)))
        camera_section = re.search(
            r"<CAMERA_SHOTS>\s*(.*?)\s*</CAMERA_SHOTS>",
            prompt,
            re.IGNORECASE | re.DOTALL,
        )
        spoken_fragments = self._storyboard_prompt_dialogue(camera_section.group(1) if camera_section else "")
        spoken_text = self._normalize_dialogue_for_comparison("".join(spoken_fragments))
        for dialogue in self._storyboard_prompt_dialogue(source_text):
            normalized_dialogue = self._normalize_dialogue_for_comparison(dialogue)
            if normalized_dialogue and normalized_dialogue not in spoken_text:
                errors.append(f"missing dialogue: {dialogue}")
        return errors

    def _materialize_clip_storyboard_prompt_output(
        self,
        output: ClipStoryboardPromptModelOutput,
        *,
        episode_key: str,
        batch_clips: dict[str, object],
        clip_prompt_items: dict[str, ClipPromptItem],
        rejection_reasons: dict[str, str] | None = None,
    ) -> list[StoryboardPromptClip]:
        expected: dict[str, tuple[str, int]] = {}
        for offset, source_key in enumerate(self._sorted_clip_segment_keys(batch_clips)):
            try:
                clip_index = int(str(source_key).strip())
            except ValueError:
                clip_index = offset + 1
            expected[self.clip_id_for_episode_index(episode_key, clip_index)] = (source_key, clip_index)

        materialized: list[StoryboardPromptClip] = []
        accepted_ids: set[str] = set()
        unexpected_ids: list[str] = []
        for result in output.clips:
            clip_id = str(result.clip_id or "").strip().replace("_shot_", "_clip_")
            expected_item = expected.get(clip_id)
            if expected_item is None:
                unexpected_ids.append(clip_id or "-")
                if getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt ignored clip_id outside requested batch: %s; expected one of %s",
                        clip_id or "-",
                        ", ".join(expected) or "-",
                    )
                continue
            if clip_id in accepted_ids:
                if getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt ignored duplicate accepted clip_id: %s",
                        clip_id,
                    )
                continue
            source_key, clip_index = expected_item
            clip_prompt_item = clip_prompt_items.get(clip_id)
            if clip_prompt_item is None:
                raise ValueError(f"clip_storyboard_prompt requires clip_prompt output for: {clip_id}")
            raw_storyboard_prompt = str(result.clip_storyboard_prompt or "").strip()
            storyboard_prompt = sanitize_video_prompt_text(
                self._storyboard_prompt_text_for_downstream(raw_storyboard_prompt)
            )
            if not storyboard_prompt:
                if rejection_reasons is not None:
                    rejection_reasons[clip_id] = "empty clip_storyboard_prompt"
                if getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt rejected %s for single-clip retry: empty prompt",
                        clip_id,
                    )
                continue
            camera_shots = self._camera_shots_from_storyboard_prompt(raw_storyboard_prompt)
            panel_plan = self._panel_plan_from_storyboard_prompt(raw_storyboard_prompt)
            source_text = self._clip_segment_text(batch_clips[source_key])
            errors = self._clip_storyboard_prompt_errors(
                prompt=raw_storyboard_prompt,
                source_text=source_text,
                target_duration_seconds=clip_prompt_item.target_duration_seconds,
                camera_shots=camera_shots,
                panel_plan=panel_plan,
            )
            if errors:
                if rejection_reasons is not None:
                    rejection_reasons[clip_id] = "; ".join(errors)
                if getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "clip_storyboard_prompt rejected %s for single-clip retry: %s",
                        clip_id,
                        "; ".join(errors),
                    )
                continue
            if rejection_reasons is not None:
                rejection_reasons.pop(clip_id, None)
            accepted_ids.add(clip_id)
            materialized_clip = StoryboardPromptClip(
                clip_id=clip_id,
                clip_title=f"Clip {clip_index:03d}",
                clip_duration_hint=f"{clip_prompt_item.target_duration_seconds}s",
                clip_text=source_text,
                duration_seconds=clip_prompt_item.target_duration_seconds,
                role_ids=self._dedupe_nonempty_texts(clip_prompt_item.role_ids),
                layout_ids=self._dedupe_nonempty_texts(clip_prompt_item.layout_ids),
                prop_ids=self._dedupe_nonempty_texts(clip_prompt_item.prop_ids),
                camera_shots=camera_shots,
                panel_plan=panel_plan,
                video_prompt=storyboard_prompt,
                negative_prompt="无字幕、对白气泡、水印、logo、片段编号和无关可读文字。",
            )
            materialized_clip.storyboard_image_prompt = self.compose_storyboard_image_prompt(
                episode_key,
                materialized_clip,
            )
            materialized.append(materialized_clip)
        missing_expected_ids = [clip_id for clip_id in expected if clip_id not in accepted_ids]
        if missing_expected_ids and rejection_reasons is not None:
            if unexpected_ids:
                reason = "unexpected clip_id(s): " + ", ".join(unexpected_ids)
            elif not output.clips:
                reason = "empty clips array"
            else:
                reason = "clip missing from provider response"
            for clip_id in missing_expected_ids:
                rejection_reasons.setdefault(clip_id, reason)
        return materialized

    async def _generate_clip_storyboard_prompt_batch(
        self,
        *,
        provider: object,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        all_episode_keys: list[str],
        final_aspect_ratio: str,
        clip_prompt_items_by_episode: dict[str, dict[str, ClipPromptItem]],
        batch_index: int,
        batch_total: int,
        batch_clips: dict[str, object],
        retry_round: int = 0,
        rejection_reasons: dict[str, str] | None = None,
    ) -> tuple[str, int, list[StoryboardPromptClip]]:
        batch_keys = self._sorted_clip_segment_keys(batch_clips)
        batch_clip_count_by_episode = {episode_key: len(batch_clips)}
        role_ids = self._role_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        layout_ids = self._layout_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        prop_ids = self._prop_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        refs, reference_image_context = self._clip_storyboard_prompt_reference_refs(
            project_dir,
            state,
            role_ids=role_ids,
            layout_ids=layout_ids,
            prop_ids=prop_ids,
        )
        clip_prompt_context: list[dict[str, object]] = []
        missing_clip_prompts: list[str] = []
        for offset, source_key in enumerate(batch_keys):
            try:
                expected_index = int(str(source_key).strip())
            except ValueError:
                expected_index = offset + 1
            clip_id = self.clip_id_for_episode_index(episode_key, expected_index)
            item = clip_prompt_items_by_episode.get(episode_key, {}).get(clip_id)
            if item is None:
                missing_clip_prompts.append(clip_id)
                continue
            clip_prompt_context.append(
                {
                    "clip_id": item.clip_id,
                    "source_clip_key": item.source_clip_key,
                    "target_duration_seconds": item.target_duration_seconds,
                    "role_ids": item.role_ids,
                    "layout_ids": item.layout_ids,
                    "prop_ids": item.prop_ids,
                    "clip_prompt": item.clip_prompt,
                }
            )
        if missing_clip_prompts:
            raise ValueError(
                "clip_storyboard_prompt requires clip_prompt output for: "
                + ", ".join(missing_clip_prompts)
            )
        allowed_clip_ids = [str(item["clip_id"]) for item in clip_prompt_context]
        first_key = batch_keys[0] if batch_keys else "-"
        last_key = batch_keys[-1] if batch_keys else "-"
        self.logger.info(
            "node=clip_storyboard_prompt episode=%s batch=%d/%d clips=%s-%s refs=%d",
            episode_key,
            batch_index,
            batch_total,
            first_key,
            last_key,
            len(refs),
        )
        current_episode_full, adjacent_episode_summaries = self._storyboard_episode_context(
            project_dir,
            state,
            episode_key,
            all_episode_keys,
        )
        prompt_body = self.workflow.prompts.render(
            "clip_storyboard_prompt",
            final_aspect_ratio=final_aspect_ratio,
            storyboard_panel_count=self.STORYBOARD_PANEL_COUNT,
            storyboard_grid=self.storyboard_grid(),
            storyboard_sheet_aspect_ratio=self.storyboard_sheet_aspect_ratio(),
            storyboard_panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            novel_extract=self._format_json(self.episode_story_context(project_dir, state, [episode_key])),
            current_episode_full=current_episode_full,
            adjacent_episode_summaries=self._format_json(adjacent_episode_summaries),
            visual_tone=self._storyboard_visual_tone(state),
            roleboard_context=self._format_json(self._roleboard_context_for_ids(project_dir, state, role_ids)),
            layout_context=self._format_json(self._layout_context_for_ids(state, layout_ids)),
            prop_context=self._format_json(self._prop_context_for_ids(state, prop_ids)),
            clip_prompt_context=self._format_json(clip_prompt_context),
            allowed_clip_ids=self._format_json(allowed_clip_ids),
            clip_segments=self._format_json({episode_key: batch_clips}),
        )
        reference_image_prefix = self._reference_image_prompt_prefix(reference_image_context)
        prompt = f"{reference_image_prefix}\n\n{prompt_body}" if reference_image_prefix else prompt_body
        batch_output = await provider.generate_json(
            prompt,
            ClipStoryboardPromptModelOutput,
            temperature=0.45,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "episode_key": episode_key,
                "expected_keys": [episode_key],
                "expected_clip_counts": batch_clip_count_by_episode,
                "clip_batch_index": batch_index,
                "clip_batch_total": batch_total,
                "clip_batch_keys": batch_keys,
                "expected_clip_ids": allowed_clip_ids,
                "clip_retry_round": retry_round,
                "storyboard_panel_count": self.STORYBOARD_PANEL_COUNT,
                "storyboard_grid": self.storyboard_grid(),
                "storyboard_panel_aspect_ratio": self.storyboard_panel_aspect_ratio(),
            },
            refs=refs,
        )
        state.budget.used_text_calls += 1
        raw_snapshot_key = f"{episode_key}_batch_{batch_index:03d}_of_{batch_total:03d}"
        if retry_round:
            raw_snapshot_key = (
                f"{episode_key}_retry_{retry_round:03d}_batch_{batch_index:03d}_of_{batch_total:03d}"
            )
        raw_snapshot_path = self.layout.node_episode_output_path(
            project_dir,
            "clip_storyboard_prompt_raw",
            raw_snapshot_key,
        )
        self.repo.write_json(raw_snapshot_path, batch_output)
        clips = self._materialize_clip_storyboard_prompt_output(
            batch_output,
            episode_key=episode_key,
            batch_clips=batch_clips,
            clip_prompt_items=clip_prompt_items_by_episode.get(episode_key, {}),
            rejection_reasons=rejection_reasons,
        )
        return episode_key, batch_index, clips

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("storyboard", node_name=self.name)
        self.logger.info(
            "node=clip_storyboard_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        target_episode_keys = self.target_episode_keys(state)
        all_episode_keys = self.expected_episode_keys(state)
        final_aspect_ratio = self.final_aspect_ratio()
        clip_segments_by_episode = self.clip_segments_by_episode(project_dir)
        all_expected_clip_segments = self._expected_clip_segments(clip_segments_by_episode, target_episode_keys)
        clip_selectors = self.active_clip_selectors()
        expected_clip_segments, selected_clip_ids = self._select_expected_clip_segments(
            all_expected_clip_segments,
            clip_selectors,
        )
        if clip_selectors and not selected_clip_ids:
            raise ValueError(
                "clip_storyboard_prompt --clips matched no clips in selected episodes: "
                + ", ".join(sorted(clip_selectors))
            )
        clip_prompt_items_by_episode = self.clip_prompts_by_episode(self.load_clip_prompt_output(project_dir))
        batch_specs: list[tuple[str, int, int, dict[str, object]]] = []
        for episode_key in target_episode_keys:
            source_clips = expected_clip_segments[episode_key]
            batches = self._clip_segment_batches(source_clips)
            for batch_index, batch_clips in enumerate(batches, start=1):
                batch_specs.append((episode_key, batch_index, len(batches), batch_clips))

        concurrency = self.batch_generation_concurrency(provider)
        self.logger.info(
            "node=clip_storyboard_prompt batches=%d concurrency=%d",
            len(batch_specs),
            concurrency,
        )
        print(
            f"[autodrama] clip_storyboard_prompt batches={len(batch_specs)} concurrency={concurrency}",
            flush=True,
        )
        semaphore = asyncio.Semaphore(concurrency)
        rejection_reasons: dict[str, str] = {}

        async def generate_one(
            episode_key: str,
            batch_index: int,
            batch_total: int,
            batch_clips: dict[str, object],
            retry_round: int = 0,
        ) -> tuple[str, int, list[StoryboardPromptClip]]:
            async with semaphore:
                return await self._generate_clip_storyboard_prompt_batch(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    episode_key=episode_key,
                    all_episode_keys=all_episode_keys,
                    final_aspect_ratio=final_aspect_ratio,
                    clip_prompt_items_by_episode=clip_prompt_items_by_episode,
                    batch_index=batch_index,
                    batch_total=batch_total,
                    batch_clips=batch_clips,
                    retry_round=retry_round,
                    rejection_reasons=rejection_reasons,
                )

        tasks = [asyncio.create_task(generate_one(*spec)) for spec in batch_specs]
        try:
            batch_results = list(await asyncio.gather(*tasks)) if tasks else []
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        clips_by_episode_batch = {
            (episode_key, batch_index): clips
            for episode_key, batch_index, clips in batch_results
        }
        generated_by_clip_id: dict[str, dict[str, StoryboardPromptClip]] = {
            episode_key: {}
            for episode_key in target_episode_keys
        }
        for episode_key in target_episode_keys:
            batch_count = len(self._clip_segment_batches(expected_clip_segments[episode_key]))
            for batch_index in range(1, batch_count + 1):
                for clip in clips_by_episode_batch[(episode_key, batch_index)]:
                    generated_by_clip_id[episode_key][clip.clip_id] = clip

        def missing_batch_specs() -> list[tuple[str, int, int, dict[str, object]]]:
            specs: list[tuple[str, int, int, dict[str, object]]] = []
            for episode_key in target_episode_keys:
                source_clips = expected_clip_segments[episode_key]
                for offset, source_key in enumerate(self._sorted_clip_segment_keys(source_clips)):
                    try:
                        expected_index = int(str(source_key).strip())
                    except ValueError:
                        expected_index = offset + 1
                    expected_clip_id = self.clip_id_for_episode_index(episode_key, expected_index)
                    if expected_clip_id in generated_by_clip_id[episode_key]:
                        continue
                    specs.append((episode_key, len(specs) + 1, 0, {source_key: source_clips[source_key]}))
            return specs

        for retry_round in range(1, self.MAX_MISSING_CLIP_RETRY_ROUNDS + 1):
            retry_batch_specs = missing_batch_specs()
            if not retry_batch_specs:
                break
            retry_total = len(retry_batch_specs)
            missing_labels = [
                self.clip_id_for_episode_index(
                    episode_key,
                    int(str(next(iter(batch_clips))).strip()) if str(next(iter(batch_clips))).strip().isdigit() else index,
                )
                for index, (episode_key, _batch_index, _batch_total, batch_clips) in enumerate(
                    retry_batch_specs,
                    start=1,
                )
            ]
            self.logger.warning(
                "node=clip_storyboard_prompt retry_round=%d/%d missing clips: %s",
                retry_round,
                self.MAX_MISSING_CLIP_RETRY_ROUNDS,
                ", ".join(missing_labels),
            )
            print(
                f"[autodrama] clip_storyboard_prompt retry round "
                f"{retry_round}/{self.MAX_MISSING_CLIP_RETRY_ROUNDS} missing clips: "
                + ", ".join(missing_labels),
                flush=True,
            )
            retry_specs = [
                (episode_key, batch_index, retry_total, batch_clips, retry_round)
                for episode_key, batch_index, _batch_total, batch_clips in retry_batch_specs
            ]
            retry_tasks = [asyncio.create_task(generate_one(*spec)) for spec in retry_specs]
            try:
                retry_results = list(await asyncio.gather(*retry_tasks)) if retry_tasks else []
            except Exception:
                for task in retry_tasks:
                    task.cancel()
                await asyncio.gather(*retry_tasks, return_exceptions=True)
                raise
            for episode_key, _batch_index, clips in retry_results:
                for clip in clips:
                    generated_by_clip_id[episode_key][clip.clip_id] = clip

        remaining_specs = missing_batch_specs()
        if remaining_specs:
            remaining_labels: list[str] = []
            for episode_key, _batch_index, _batch_total, batch_clips in remaining_specs:
                source_key = next(iter(batch_clips))
                source_keys = self._sorted_clip_segment_keys(expected_clip_segments[episode_key])
                try:
                    expected_index = int(str(source_key).strip())
                except ValueError:
                    expected_index = source_keys.index(source_key) + 1
                clip_id = self.clip_id_for_episode_index(episode_key, expected_index)
                reason = rejection_reasons.get(clip_id)
                remaining_labels.append(f"{clip_id} ({reason})" if reason else clip_id)
            raise ValueError(
                "clip_storyboard_prompt still missing clip(s) after "
                f"{self.MAX_MISSING_CLIP_RETRY_ROUNDS} single-clip retry rounds: "
                + ", ".join(remaining_labels)
            )

        generated_by_episode: dict[str, list[StoryboardPromptClip]] = {
            episode_key: []
            for episode_key in target_episode_keys
        }
        for episode_key in target_episode_keys:
            source_clips = expected_clip_segments[episode_key]
            for offset, source_key in enumerate(self._sorted_clip_segment_keys(source_clips)):
                try:
                    expected_index = int(str(source_key).strip())
                except ValueError:
                    expected_index = offset + 1
                expected_clip_id = self.clip_id_for_episode_index(episode_key, expected_index)
                clip = generated_by_clip_id[episode_key].get(expected_clip_id)
                if clip is not None:
                    generated_by_episode[episode_key].append(clip)

        output = StoryboardPromptOutput(
            storyboards=[
                StoryboardPromptEpisode(episode_key=episode_key, clips=generated_by_episode[episode_key])
                for episode_key in target_episode_keys
            ]
        )
        output = self.validate_clip_storyboard_prompt_output(
            output,
            expected_episode_keys=target_episode_keys,
            expected_clip_segments=expected_clip_segments,
        )
        merged = self.merge_clip_storyboard_prompt_outputs(
            project_dir=project_dir,
            generated_output=output,
            target_episode_keys=target_episode_keys,
            all_episode_keys=all_episode_keys,
            selected_clip_ids=selected_clip_ids if clip_selectors else None,
            expected_clip_ids_by_episode=self._expected_clip_ids_by_episode(all_expected_clip_segments),
        )
        self.repo.save_node_output(project_dir, self.name, merged)
        return state


class ClipPromptNode(StoryboardPromptNode):
    name = "clip_prompt"

    def _clip_prompt_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        role_ids: list[str],
        layout_ids: list[str],
        prop_ids: list[str],
    ) -> tuple[list[AssetRef], list[dict[str, object]]]:
        refs: list[AssetRef] = []
        context: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()

        def append(ref: AssetRef, item: dict[str, object]) -> None:
            key = (str(ref.id or ""), str(ref.path or ref.url or ""))
            if key in seen:
                return
            seen.add(key)
            refs.append(ref)
            item["input_slot"] = f"image_{len(refs)}"
            context.append(item)

        for role_id in role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if appearance is None:
                continue
            asset_path = appearance.asset_path or appearance.design_image_asset_path
            asset_url = appearance.asset_url or appearance.design_image_asset_url
            existing = self.layout.existing_project_file(project_dir, asset_path)
            if not existing and not asset_url:
                continue
            append(
                AssetRef(
                    id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=asset_url,
                    metadata={
                        "asset_type": "roleboard",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "reference_for": "clip_prompt",
                    },
                ),
                {
                    "asset_type": "roleboard",
                    "role_id": role.id,
                    "role_name": role.name,
                    "appearance_id": appearance.id,
                    "appearance_name": appearance.name,
                },
            )

        for layout_id in layout_ids:
            layout = state.layouts.get(layout_id)
            if layout is None:
                continue
            existing = self.layout.existing_project_file(project_dir, layout.asset_path)
            if not existing and not layout.asset_url:
                continue
            append(
                AssetRef(
                    id=layout.asset_id or layout.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=layout.asset_url,
                    metadata={
                        "asset_type": "layout",
                        "layout_id": layout.id,
                        "layout_name": layout.name,
                        "reference_for": "clip_prompt",
                    },
                ),
                {
                    "asset_type": "layout",
                    "layout_id": layout.id,
                    "layout_name": layout.name,
                },
            )

        for prop_id in prop_ids:
            prop = state.props.get(prop_id)
            if prop is None:
                continue
            existing = self.layout.existing_project_file(project_dir, prop.asset_path)
            if not existing and not prop.asset_url:
                continue
            append(
                AssetRef(
                    id=prop.asset_id or prop.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=prop.asset_url,
                    metadata={
                        "asset_type": "prop",
                        "prop_id": prop.id,
                        "prop_name": prop.name,
                        "reference_for": "clip_prompt",
                    },
                ),
                {
                    "asset_type": "prop",
                    "prop_id": prop.id,
                    "prop_name": prop.name,
                },
            )
        return refs, context

    def _neighbor_clip_context(
        self,
        *,
        source_key: str,
        source_clips: dict[str, object],
    ) -> str:
        ordered_keys = self._sorted_clip_segment_keys(source_clips)
        try:
            index = ordered_keys.index(source_key)
        except ValueError:
            index = 0

        def item_at(offset: int) -> dict[str, object] | None:
            target_index = index + offset
            if target_index < 0 or target_index >= len(ordered_keys):
                return None
            key = ordered_keys[target_index]
            return {
                "clip_key": key,
                "text": self._clip_segment_text(source_clips[key]),
                "role_names": self._clip_segment_values(source_clips[key], "role_names"),
                "layout_names": self._clip_segment_values(source_clips[key], "layout_names"),
                "prop_names": self._clip_segment_values(source_clips[key], "prop_names"),
            }

        return self._format_json(
            {
                "previous_clip": item_at(-1),
                "current_clip": item_at(0),
                "next_clip": item_at(1),
            }
        )

    def _episode_summary_text(self, project_dir: Path, state: ProjectState, episode_key: str) -> str:
        stories = self.episode_story_context(project_dir, state, [episode_key])
        return str(stories.get(episode_key) or "").strip() or "（暂无当前集剧情摘要。）"

    @staticmethod
    def _first_nonempty(*values: object) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _relative_assets_intro(
        self,
        *,
        role_context: list[dict[str, object]],
        layout_context: list[dict[str, object]],
        prop_context: list[dict[str, object]],
    ) -> str:
        sections: list[str] = []

        if role_context:
            lines = ["【角色】"]
            for item in role_context:
                name = self._first_nonempty(item.get("role_name"), item.get("role_id"), "未命名角色")
                appearances = item.get("appearances")
                appearance_desc = ""
                if isinstance(appearances, list) and appearances:
                    first_appearance = appearances[0]
                    if isinstance(first_appearance, dict):
                        appearance_desc = self._first_nonempty(first_appearance.get("desc"))
                intro = self._first_nonempty(item.get("intro"), appearance_desc, "暂无介绍。")
                lines.append(f"{name}：{intro}")
            sections.append("\n".join(lines))

        if layout_context:
            lines = ["【场景】"]
            for item in layout_context:
                name = self._first_nonempty(item.get("layout_name"), item.get("layout_id"), "未命名场景")
                desc = self._first_nonempty(item.get("desc"), "暂无描述。")
                lines.append(f"{name}：{desc}")
            sections.append("\n".join(lines))

        if prop_context:
            lines = ["【道具】"]
            for item in prop_context:
                name = self._first_nonempty(item.get("prop_name"), item.get("prop_id"), "未命名道具")
                status = self._first_nonempty(item.get("status"))
                desc = self._first_nonempty(item.get("desc"), "暂无描述。")
                label = f"{name}（{status}）" if status and status not in name else name
                lines.append(f"{label}：{desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "（当前 clip 无明确角色、场景或道具资产。）"

    def render_clip_prompt_request(
        self,
        *,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        source_key: str,
        source_clip: object,
        source_clips: dict[str, object],
    ) -> tuple[str, list[AssetRef], dict[str, object]]:
        batch_clips = {source_key: source_clip}
        role_ids = self._role_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        layout_ids = self._layout_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        prop_ids = self._prop_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        refs, reference_image_context = self._clip_prompt_reference_refs(
            project_dir,
            state,
            role_ids=role_ids,
            layout_ids=layout_ids,
            prop_ids=prop_ids,
        )
        role_context = self._roleboard_context_for_ids(project_dir, state, role_ids)
        layout_context = self._layout_context_for_ids(state, layout_ids)
        prop_context = self._prop_context_for_ids(state, prop_ids)
        prompt = self.workflow.prompts.render(
            "clip_prompt",
            episode_summary=self._episode_summary_text(project_dir, state, episode_key),
            clip_text=self._clip_segment_text(source_clip),
            visual_tone=self.asset_service.visual_tone(state) or DirectorService.project_context(
                state,
                episode_keys=[episode_key],
            ),
            relative_assets_intro=self._relative_assets_intro(
                role_context=role_context,
                layout_context=layout_context,
                prop_context=prop_context,
            ),
        )
        try:
            clip_index = int(str(source_key).strip())
        except ValueError:
            clip_index = self._sorted_clip_segment_keys(source_clips).index(source_key) + 1
        return prompt, refs, {
            "clip_index": clip_index,
            "clip_id": self.clip_id_for_episode_index(episode_key, clip_index),
            "role_ids": role_ids,
            "layout_ids": layout_ids,
            "prop_ids": prop_ids,
            "role_names": self._clip_segment_values(source_clip, "role_names"),
            "layout_names": self._clip_segment_values(source_clip, "layout_names"),
            "prop_names": self._clip_segment_values(source_clip, "prop_names"),
            "reference_image_context": reference_image_context,
        }

    @staticmethod
    def _validate_target_duration(value: object, *, clip_id: str) -> int:
        try:
            duration = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"clip_prompt returned invalid target_duration_seconds for {clip_id}") from exc
        if not 8 <= duration <= 15:
            raise ValueError(
                f"clip_prompt target_duration_seconds for {clip_id} must be an integer in [8, 15]; "
                f"got {duration}"
            )
        return duration

    def merge_clip_prompt_outputs(
        self,
        *,
        project_dir: Path,
        generated_output: ClipPromptOutput,
        target_episode_keys: list[str],
        all_episode_keys: list[str],
    ) -> ClipPromptOutput:
        by_episode: dict[str, ClipPromptEpisode] = {}
        try:
            existing = self.load_clip_prompt_output(project_dir)
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.logger.warning("clip_prompt ignored invalid existing output: %s", exc)
        else:
            by_episode.update({episode.episode_key: episode for episode in existing.clip_prompts})
        for episode in generated_output.clip_prompts:
            by_episode[episode.episode_key] = episode
        ordered_keys = [key for key in all_episode_keys if key in by_episode]
        for key in by_episode:
            if key not in ordered_keys and key not in target_episode_keys:
                ordered_keys.append(key)
        return ClipPromptOutput(clip_prompts=[by_episode[key] for key in ordered_keys])

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("storyboard", node_name=self.name)
        self.logger.info(
            "node=clip_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        target_episode_keys = self.target_episode_keys(state)
        all_episode_keys = self.expected_episode_keys(state)
        clip_segments_by_episode = self.clip_segments_by_episode(project_dir)
        expected_clip_segments = self._expected_clip_segments(clip_segments_by_episode, target_episode_keys)

        clip_specs: list[tuple[str, str, object, dict[str, object]]] = []
        for episode_key in target_episode_keys:
            source_clips = expected_clip_segments[episode_key]
            for source_key in self._sorted_clip_segment_keys(source_clips):
                clip_specs.append((episode_key, source_key, source_clips[source_key], source_clips))

        concurrency = self.batch_generation_concurrency(provider)
        self.logger.info("node=clip_prompt clips=%d concurrency=%d", len(clip_specs), concurrency)
        print(f"[autodrama] clip_prompt clips={len(clip_specs)} concurrency={concurrency}", flush=True)
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(
            episode_key: str,
            source_key: str,
            source_clip: object,
            source_clips: dict[str, object],
        ) -> tuple[str, ClipPromptItem]:
            prompt, refs, context = self.render_clip_prompt_request(
                project_dir=project_dir,
                state=state,
                episode_key=episode_key,
                source_key=source_key,
                source_clip=source_clip,
                source_clips=source_clips,
            )
            clip_id = str(context["clip_id"])
            async with semaphore:
                result = await provider.generate_json(
                    prompt,
                    ClipPromptModelOutput,
                    temperature=0.45,
                    metadata={
                        "node_name": self.name,
                        "project_id": state.project_id,
                        "episode_key": episode_key,
                        "clip_id": clip_id,
                        "source_clip_key": source_key,
                    },
                    refs=refs,
                )
            state.budget.used_text_calls += 1
            clip_prompt = sanitize_video_prompt_text(str(result.clip_prompt or "").strip())
            if not clip_prompt:
                raise ValueError(f"clip_prompt returned empty clip_prompt for {clip_id}")
            return episode_key, ClipPromptItem(
                episode_key=episode_key,
                clip_id=clip_id,
                clip_index=int(context["clip_index"]),
                source_clip_key=source_key,
                clip_text=self._clip_segment_text(source_clip),
                role_names=list(context["role_names"]),
                layout_names=list(context["layout_names"]),
                prop_names=list(context["prop_names"]),
                role_ids=list(context["role_ids"]),
                layout_ids=list(context["layout_ids"]),
                prop_ids=list(context["prop_ids"]),
                target_duration_seconds=self._validate_target_duration(
                    result.target_duration_seconds,
                    clip_id=clip_id,
                ),
                clip_prompt=clip_prompt,
                reference_image_context=list(context["reference_image_context"]),
                provider=str(getattr(provider, "name", "unknown")),
                model=str(getattr(provider, "model", "") or ""),
            )

        tasks = [asyncio.create_task(generate_one(*spec)) for spec in clip_specs]
        try:
            results = list(await asyncio.gather(*tasks)) if tasks else []
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        clips_by_episode: dict[str, list[ClipPromptItem]] = {episode_key: [] for episode_key in target_episode_keys}
        for episode_key, item in results:
            clips_by_episode.setdefault(episode_key, []).append(item)
        for items in clips_by_episode.values():
            items.sort(key=lambda item: item.clip_index)

        output = ClipPromptOutput(
            clip_prompts=[
                ClipPromptEpisode(episode_key=episode_key, clips=clips_by_episode.get(episode_key, []))
                for episode_key in target_episode_keys
            ]
        )
        merged = self.merge_clip_prompt_outputs(
            project_dir=project_dir,
            generated_output=output,
            target_episode_keys=target_episode_keys,
            all_episode_keys=all_episode_keys,
        )
        generated_by_episode = {episode.episode_key: episode for episode in merged.clip_prompts}
        for episode_key in target_episode_keys:
            episode = generated_by_episode.get(episode_key)
            if episode is not None:
                self.repo.write_json(
                    self.layout.node_episode_output_path(project_dir, self.name, episode_key),
                    episode,
                )
        return state


class StoryboardGenerationNode(StoryboardAssetNodeBase):
    name = "clip_storyboard_image_generation"
    DEFAULT_CONCURRENCY = 5
    MAX_CONCURRENCY = 5

    def load_existing_output(self, project_dir: Path) -> dict[str, StoryboardSheetGenerationItem]:
        path = self.layout.node_output_path(project_dir, self.name)
        if not path.exists():
            return {}
        try:
            output = StoryboardSheetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {item.clip_id: item for item in output.generated_storyboards if item.clip_id}

    @staticmethod
    def required_storyboard_image_prompt(clip: StoryboardPromptClip) -> str:
        prompt = str(clip.storyboard_image_prompt or "").strip()
        if not prompt:
            raise ValueError(
                "clip_storyboard_image_generation requires clip_storyboard_prompt.storyboard_image_prompt "
                f"for {clip.clip_id}; rerun pregen --only clip_storyboard_prompt first"
            )
        return prompt

    @staticmethod
    def exception_summary(exc: BaseException) -> str:
        message = str(exc).strip()
        exception_type = type(exc).__name__
        return f"{exception_type}: {message}" if message else exception_type

    def _resume_storyboard_sheet_from_existing_file(
        self,
        *,
        project_dir: Path,
        provider: object,
        episode: StoryboardPromptEpisode,
        clip: StoryboardPromptClip,
        asset_id: str,
        existing_path: str | None,
        image_prompt: str,
    ) -> StoryboardSheetGenerationItem | None:
        existing_rel = self.layout.existing_project_file(project_dir, existing_path)
        if not existing_rel:
            return None
        return StoryboardSheetGenerationItem(
            episode_key=episode.episode_key,
            clip_id=clip.clip_id,
            asset_id=asset_id,
            prompt=image_prompt,
            duration_seconds=clip.duration_seconds,
            panel_count=self.STORYBOARD_PANEL_COUNT,
            grid=self.storyboard_grid(),
            sheet_aspect_ratio=self.storyboard_sheet_aspect_ratio(),
            panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            size=self.storyboard_sheet_size(),
            asset_path=existing_rel,
            asset_url=None,
            provider=str(getattr(provider, "name", "unknown") or "unknown"),
            model=str(getattr(provider, "model", "") or ""),
            request_id=None,
            usage={},
            raw_response={"resumed_from_existing_file": True},
        )

    @classmethod
    def generation_concurrency(cls, provider: object) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in (
                "clip_storyboard_image_generation_concurrency",
                "image_generation_concurrency",
                "max_concurrent_images",
            ):
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in (
                "clip_storyboard_image_generation_concurrency",
                "image_generation_concurrency",
                "max_concurrent_images",
            ):
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = cls.DEFAULT_CONCURRENCY
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = cls.DEFAULT_CONCURRENCY
        return max(1, min(cls.MAX_CONCURRENCY, resolved))

    @staticmethod
    def _storyboard_dimensions(value: object) -> tuple[int, int] | None:
        text = str(value or "").strip().lower().replace("×", "x")
        match = re.fullmatch(r"(\d+)x(\d+)", text)
        if not match:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        return (width, height) if width > 0 and height > 0 else None

    def _postprocess_storyboard_sheet(
        self,
        *,
        project_dir: Path,
        asset_path: str,
    ) -> dict[str, Any]:
        from PIL import Image, ImageOps

        path = project_dir / asset_path
        if not path.exists():
            raise FileNotFoundError(f"Generated storyboard image is missing: {path}")

        with Image.open(path) as source:
            original_size = source.size
            target_size = self._storyboard_dimensions(self.storyboard_sheet_size()) or original_size
            image = source.convert("RGBA")
            if image.size != target_size:
                image = ImageOps.fit(
                    image,
                    target_size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )

        width, height = image.size
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            image.convert("RGB").save(path, quality=95)
        else:
            image.save(path)
        return {
            "storyboard_postprocess": {
                "original_size": list(original_size),
                "final_size": [width, height],
                "annotations": "generated_by_image_model",
            }
        }

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("storyboard", node_name=STORYBOARD_IMAGE_PROVIDER_NODE_NAME)
        self.logger.info(
            "node=clip_storyboard_image_generation provider=%s model=%s image_binding=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        prompt_output = self.load_clip_storyboard_prompt_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        target_set = set(target_episode_keys)
        target_storyboards = [
            episode
            for episode in prompt_output.storyboards
            if episode.episode_key in target_set
        ]
        missing = sorted(target_set.difference(episode.episode_key for episode in target_storyboards))
        if missing:
            raise ValueError(f"clip_storyboard_image_generation missing clip_storyboard_prompt episode(s): {', '.join(missing)}")

        existing_by_clip = self.load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 12) or 12))
        supports_refs = bool(max_refs and getattr(provider, "supports_reference_images", False))
        generated_by_clip = dict(existing_by_clip)
        clip_selectors = self.active_clip_selectors()

        pending: list[tuple[StoryboardPromptEpisode, StoryboardPromptClip]] = []
        skipped_existing = 0
        selected_clip_count = 0
        for episode in target_storyboards:
            for clip_index, clip in enumerate(episode.clips, start=1):
                if not self.clip_matches_active_selectors(
                    episode_key=episode.episode_key,
                    clip=clip,
                    clip_index=clip_index,
                    selectors=clip_selectors,
                ):
                    continue
                selected_clip_count += 1
                asset_id = self.storyboard_asset_id(clip.clip_id)
                existing_item = existing_by_clip.get(clip.clip_id)
                output_path = self.layout.image_asset_path(project_dir, "storyboards", asset_id)
                existing_file_path = self.layout.existing_project_file(project_dir, output_path)
                if (
                    not force_pregen
                    and (
                        (existing_item is not None and self.layout.existing_project_file(project_dir, existing_item.asset_path))
                        or existing_file_path is not None
                    )
                ):
                    reuse_source = "json" if existing_item is not None else "file"
                    if existing_item is None:
                        image_prompt = self.required_storyboard_image_prompt(clip)
                        existing_item = self._resume_storyboard_sheet_from_existing_file(
                            project_dir=project_dir,
                            provider=provider,
                            episode=episode,
                            clip=clip,
                            asset_id=asset_id,
                            existing_path=existing_file_path,
                            image_prompt=image_prompt,
                        )
                    if existing_item is None:
                        pending.append((episode, clip))
                        continue
                    print(
                        (
                            "[autodrama] clip_storyboard_image_generation skip existing "
                            f"source={reuse_source} clip={clip.clip_id} asset_id={asset_id} path={existing_item.asset_path}"
                        ),
                        flush=True,
                    )
                    self.logger.info("%s already exists, reused from %s", asset_id, existing_item.asset_path)
                    generated_by_clip[clip.clip_id] = existing_item
                    skipped_existing += 1
                    continue
                pending.append((episode, clip))

        if clip_selectors and selected_clip_count <= 0:
            raise ValueError(
                "clip_storyboard_image_generation --clips matched no clips in selected episodes: "
                f"{', '.join(sorted(clip_selectors))}"
            )

        concurrency = self.generation_concurrency(provider)
        print(
            (
                "[autodrama] clip_storyboard_image_generation "
                f"concurrency={concurrency} pending={len(pending)} skipped_existing={skipped_existing} "
                f"selected_clips={selected_clip_count}"
            ),
            flush=True,
        )
        self.logger.info(
            "node=clip_storyboard_image_generation selected_images=%d pending_images=%d concurrency=%d",
            selected_clip_count,
            len(pending),
            concurrency,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(episode: StoryboardPromptEpisode, clip: StoryboardPromptClip) -> StoryboardSheetGenerationItem:
            async with semaphore:
                return await self.generate_storyboard_sheet(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    episode=episode,
                    shot=clip,
                    max_refs=max_refs,
                    supports_refs=supports_refs,
                )

        tasks = [asyncio.create_task(generate_one(episode, clip)) for episode, clip in pending]
        results = list(await asyncio.gather(*tasks, return_exceptions=True)) if tasks else []
        generated_items: list[StoryboardSheetGenerationItem] = []
        failures: list[tuple[StoryboardPromptClip, BaseException]] = []
        for (_episode, clip), result in zip(pending, results, strict=False):
            if isinstance(result, BaseException):
                failures.append((clip, result))
                self.logger.error(
                    "clip_storyboard_image_generation failed clip=%s: %s",
                    clip.clip_id,
                    self.exception_summary(result),
                )
                continue
            generated_items.append(result)
        for item in generated_items:
            generated_by_clip[item.clip_id] = item

        prompt_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        ordered_items = [
            generated_by_clip[clip.clip_id]
            for episode_key in self.expected_episode_keys(state)
            for clip in prompt_by_episode.get(episode_key, StoryboardPromptEpisode(episode_key=episode_key, clips=[])).clips
            if clip.clip_id in generated_by_clip
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StoryboardSheetGenerationOutput(generated_storyboards=ordered_items),
        )
        if failures:
            preview = "; ".join(
                f"{clip.clip_id}: {self.exception_summary(exc)}"
                for clip, exc in failures[:5]
            )
            if len(failures) > 5:
                preview = f"{preview}; ..."
            raise ProviderBadResponseError(
                "clip_storyboard_image_generation failed for "
                f"{len(failures)}/{len(pending)} pending storyboard image(s); "
                f"saved {len(generated_items)} successful new image(s) and "
                f"{len(ordered_items)} total storyboard record(s). "
                "Rerun without --force to skip saved files and continue missing clips. "
                f"Failures: {preview}"
            )
        return state

    async def generate_storyboard_sheet(
        self,
        *,
        provider: object,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardPromptEpisode,
        shot: StoryboardPromptClip,
        max_refs: int,
        supports_refs: bool,
    ) -> StoryboardSheetGenerationItem:
        asset_id = self.storyboard_asset_id(shot.clip_id)
        output_path = self.layout.image_asset_path(project_dir, "storyboards", asset_id)
        refs = self.storyboard_reference_refs(project_dir, state, shot, limit=max_refs) if supports_refs else []
        image_prompt = self.required_storyboard_image_prompt(shot)
        result = await provider.generate_image(
            image_prompt,
            refs=refs,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "episode_key": episode.episode_key,
                "clip_id": shot.clip_id,
                "asset_id": asset_id,
                "asset_type": "storyboard",
                "duration_seconds": shot.duration_seconds,
                "panel_count": self.STORYBOARD_PANEL_COUNT,
                "grid": self.storyboard_grid(),
                "panel_aspect_ratio": self.storyboard_panel_aspect_ratio(),
                "size": self.storyboard_sheet_size(),
                "provider_binding_node": STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
            },
        )
        asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
        provider_asset_url = self.first_image_url(result)
        postprocess_metadata = self._postprocess_storyboard_sheet(
            project_dir=project_dir,
            asset_path=asset_path,
        )
        raw_response = dict(result.raw_response or {})
        raw_response.update(postprocess_metadata)
        if provider_asset_url:
            raw_response["provider_asset_url_before_storyboard_postprocess"] = provider_asset_url
        asset_url = None
        item = StoryboardSheetGenerationItem(
            episode_key=episode.episode_key,
            clip_id=shot.clip_id,
            asset_id=asset_id,
            prompt=image_prompt,
            duration_seconds=shot.duration_seconds,
            panel_count=self.STORYBOARD_PANEL_COUNT,
            grid=self.storyboard_grid(),
            sheet_aspect_ratio=self.storyboard_sheet_aspect_ratio(),
            panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            size=self.storyboard_sheet_size(),
            asset_path=asset_path,
            asset_url=asset_url,
            provider=result.provider,
            model=result.model,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=raw_response,
        )
        self.logger.info("%s generated successfully, saved in %s", asset_id, asset_path)
        print(
            (
                "[autodrama] clip_storyboard_image_generation generated "
                f"clip={shot.clip_id} asset_id={asset_id} path={asset_path}"
            ),
            flush=True,
        )
        return item


class StoryboardKeyframeGenerationNode(StoryboardAssetNodeBase):
    name = "clip_storyboard_keyframe_generation"
    DEFAULT_CONCURRENCY = 4
    MAX_CONCURRENCY = 10

    @staticmethod
    def keyframe_asset_id(clip_id: str, frame_role: str) -> str:
        return f"{str(clip_id).strip()}_{str(frame_role).strip()}_frame"

    def _keyframe_visual_style_prompt(self, state: ProjectState) -> str:
        asset_service = getattr(self, "asset_service", None)
        visual_tone_method = getattr(asset_service, "visual_tone", None)
        service_style = visual_tone_method(state) if callable(visual_tone_method) else None
        settings = getattr(getattr(self, "repo", None), "settings", None)
        generation = getattr(settings, "generation", None)
        configured_style = getattr(generation, "visual_style_prompt", None)
        style = str(
            service_style
            or state.metadata.get("visual_style_prompt")
            or configured_style
            or ""
        ).strip()
        if not style:
            raise ValueError(
                "clip_storyboard_keyframe_generation requires a project visual style prompt; "
                "run key_vision_prompt first or configure generation.visual_style_prompt"
            )
        return style

    def _active_clip_selectors(self) -> set[str]:
        context = getattr(self.workflow, "_run_context", None)
        if context is not None and getattr(context, "has_clip_selectors", False):
            return normalize_clip_selectors(getattr(context, "clip_selectors", set()))
        return normalize_clip_selectors(getattr(self.workflow, "_active_clip_selectors", set()))

    def _clip_matches_active_selectors(
        self,
        *,
        episode_key: str,
        clip: StoryboardPromptClip,
        clip_index: int,
        selectors: set[str],
    ) -> bool:
        if not selectors:
            return True
        return clip_matches_selectors(episode_key, clip.clip_id, clip_index, selectors)

    @staticmethod
    def _dimension_pair_from_value(value: object) -> tuple[int, int] | None:
        text = str(value or "").strip().lower().replace("×", "x")
        if "x" not in text:
            return None
        left, right = text.split("x", 1)
        try:
            width = int(float(left.strip()))
            height = int(float(right.strip()))
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    @staticmethod
    def _ratio_pair_from_value(value: object) -> tuple[int, int] | None:
        text = str(value or "").strip().lower().replace("×", "x")
        if ":" in text:
            left, right = text.split(":", 1)
        elif "x" in text:
            left, right = text.split("x", 1)
        else:
            return None
        try:
            width = int(float(left.strip()))
            height = int(float(right.strip()))
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        divisor = gcd(width, height)
        return width // divisor, height // divisor

    def _keyframe_target_dimensions(self, provider: object) -> tuple[int, int] | None:
        params = self._keyframe_model_params(provider)
        size = params.get("size") or params.get("image_size") or self.final_aspect_ratio()
        resolution = params.get("resolution") or params.get("image_resolution")
        normalizer = getattr(provider, "_normalize_size", None)
        if callable(normalizer):
            try:
                normalized_size = normalizer(size, resolution)
            except TypeError:
                normalized_size = normalizer(size)
            dimensions = self._dimension_pair_from_value(normalized_size)
            if dimensions:
                return dimensions
        dimensions = self._dimension_pair_from_value(size)
        if dimensions:
            return dimensions
        ratio = self._ratio_pair_from_value(size) or self._ratio_pair_from_value(self.final_aspect_ratio())
        if not ratio:
            return None
        ratio_width, ratio_height = ratio
        long_edge = 1920
        if str(resolution or "").strip().lower() in {"4k", "high", "large"}:
            long_edge = 3840
        elif str(resolution or "").strip().lower() in {"1k", "low", "small"}:
            long_edge = 1024
        if ratio_height >= ratio_width:
            height = long_edge
            width = max(1, round(height * ratio_width / ratio_height))
        else:
            width = long_edge
            height = max(1, round(width * ratio_height / ratio_width))
        return width, height

    def _normalize_keyframe_image_file(
        self,
        project_dir: Path,
        asset_path: str | None,
        *,
        provider: object,
    ) -> dict[str, Any]:
        if not asset_path:
            return {}
        target_dimensions = self._keyframe_target_dimensions(provider)
        if target_dimensions is None:
            return {}
        path = Path(asset_path)
        if not path.is_absolute():
            path = project_dir / path
        if not path.exists():
            return {}
        try:
            from PIL import Image
        except ImportError:
            self.logger.warning("Pillow is not installed; keyframe aspect normalization skipped for %s", asset_path)
            return {}

        target_width, target_height = target_dimensions
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            original_width, original_height = image.size
            if original_width <= 0 or original_height <= 0:
                return {}
            original_ratio = original_width / original_height
            target_ratio = target_width / target_height
            ratio_delta = abs(original_ratio - target_ratio)
            if image.size == target_dimensions or ratio_delta <= 0.005:
                return {
                    "keyframe_aspect_normalization": {
                        "applied": False,
                        "original_size": [original_width, original_height],
                        "target_size": [target_width, target_height],
                    }
                }
            if original_ratio > target_ratio:
                crop_height = original_height
                crop_width = max(1, round(crop_height * target_ratio))
            else:
                crop_width = original_width
                crop_height = max(1, round(crop_width / target_ratio))
            left = max(0, (original_width - crop_width) // 2)
            top = max(0, (original_height - crop_height) // 2)
            cropped = image.crop((left, top, left + crop_width, top + crop_height))
            resized = cropped.resize(target_dimensions, Image.Resampling.LANCZOS)
            resized.save(path)
        return {
            "keyframe_aspect_normalization": {
                "applied": True,
                "original_size": [original_width, original_height],
                "crop_box": [left, top, left + crop_width, top + crop_height],
                "target_size": [target_width, target_height],
            }
        }

    def load_existing_output(self, project_dir: Path) -> dict[tuple[str, str, str], StoryboardKeyframeGenerationItem]:
        return self._load_existing_output(project_dir)

    def _load_existing_output(self, project_dir: Path) -> dict[tuple[str, str, str], StoryboardKeyframeGenerationItem]:
        path = self.layout.node_output_path(project_dir, self.name)
        if not path.exists():
            return {}
        try:
            output = StoryboardKeyframeGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {
            (item.episode_key, item.clip_id, str(item.frame_role)): item
            for item in output.generated_keyframes
            if item.episode_key and item.clip_id and item.frame_role
        }

    def _keyframe_model_params(self, provider: object) -> dict[str, Any]:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        if isinstance(params, dict) and params:
            return dict(params)
        node_settings = self.repo.settings.nodes.get(self.name)
        if node_settings is None:
            return {}
        return dict(node_settings.params)

    def _keyframe_template_candidates(self, provider: object) -> list[str]:
        params = self._keyframe_model_params(provider)
        configured = str(params.get("prompt_template") or "").strip()
        if configured:
            configured = configured.removesuffix(".md")
            if "/" not in configured:
                configured = f"storyboard_keyframe/{configured}"
            return [configured]
        provider_name = slugify(str(getattr(provider, "name", "") or ""), fallback="provider").lower()
        model_name = slugify(str(getattr(provider, "model", "") or ""), fallback="model").lower()
        return [
            f"storyboard_keyframe/{provider_name}_{model_name}",
            f"storyboard_keyframe/{provider_name}",
            "storyboard_keyframe/default",
            "storyboard_keyframe/toapi_gpt_image_2",
        ]

    @classmethod
    def generation_concurrency(cls, provider: object) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in (
                "clip_storyboard_keyframe_generation_concurrency",
                "image_generation_concurrency",
                "concurrency",
                "max_concurrent_images",
            ):
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in (
                "clip_storyboard_keyframe_generation_concurrency",
                "image_generation_concurrency",
                "concurrency",
                "max_concurrent_images",
            ):
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = cls.DEFAULT_CONCURRENCY
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = cls.DEFAULT_CONCURRENCY
        return max(1, min(cls.MAX_CONCURRENCY, resolved))

    def _panel_text(self, clip: StoryboardPromptClip, panel_ref: str) -> str:
        panel = clip.panel_plan.get(panel_ref)
        if isinstance(panel, str):
            return panel
        if panel is None:
            return ""
        return self._format_json(panel)

    def _render_keyframe_prompt(
        self,
        *,
        provider: object,
        state: ProjectState,
        episode_key: str,
        clip: StoryboardPromptClip,
        storyboard_sheet: StoryboardSheetGenerationItem,
        frame_role: str,
        panel_ref: str,
    ) -> tuple[str, str]:
        prompts = getattr(self.workflow, "prompts", None)
        if prompts is None:
            raise ValueError("clip_storyboard_keyframe_generation requires workflow prompt store")
        frame_role_label = "start_frame" if frame_role == "start" else "end_frame"
        last_error: Exception | None = None
        for template_name in self._keyframe_template_candidates(provider):
            try:
                return (
                    prompts.render(
                        template_name,
                        title=state.title,
                        episode_key=episode_key,
                        clip_id=clip.clip_id,
                        frame_role=frame_role,
                        frame_role_label=frame_role_label,
                        panel_ref=panel_ref,
                        panel_text=self._panel_text(clip, panel_ref),
                        clip_title=clip.clip_title or "",
                        clip_text=clip.clip_text or "",
                        clip_duration_hint=clip.clip_duration_hint or f"{clip.duration_seconds:g}s",
                        camera_shots_json=self._format_json(clip.camera_shots),
                        panel_plan_json=self._format_json(clip.panel_plan),
                        video_prompt=clip.video_prompt,
                        negative_prompt=clip.negative_prompt or "",
                        source_storyboard_asset_id=storyboard_sheet.asset_id,
                        source_storyboard_asset_path=storyboard_sheet.asset_path or "",
                        source_storyboard_asset_url=storyboard_sheet.asset_url or "",
                        visual_style_prompt=self._keyframe_visual_style_prompt(state),
                        final_aspect_ratio=self.final_aspect_ratio(),
                        provider_name=getattr(provider, "name", "unknown"),
                        model_name=getattr(provider, "model", "-"),
                    ).strip(),
                    template_name,
                )
            except FileNotFoundError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise FileNotFoundError("No storyboard keyframe prompt template candidates were available")

    def _storyboard_ref(
        self,
        project_dir: Path,
        storyboard_sheet: StoryboardSheetGenerationItem,
        *,
        frame_role: str,
        panel_ref: str,
    ) -> list[AssetRef]:
        existing = self.layout.existing_project_file(project_dir, storyboard_sheet.asset_path)
        if not existing and not storyboard_sheet.asset_url:
            return []
        return [
            AssetRef(
                id=storyboard_sheet.asset_id,
                type="image",
                path=str(project_dir / existing) if existing else None,
                url=storyboard_sheet.asset_url,
                metadata={
                    "asset_type": "storyboard",
                    "reference_for": "storyboard_keyframe",
                    "clip_id": storyboard_sheet.clip_id,
                    "frame_role": frame_role,
                    "panel_ref": panel_ref,
                },
            )
        ]

    def _keyframe_key_vision_ref(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        frame_role: str,
        panel_ref: str,
    ) -> AssetRef:
        asset = state.metadata.get("key_vision_asset")
        if isinstance(asset, dict):
            asset_id = asset.get("asset_id") or state.metadata.get("key_vision_asset_id")
            asset_path = asset.get("asset_path") or state.metadata.get("key_vision_asset_path")
            asset_url = asset.get("asset_url") or state.metadata.get("key_vision_asset_url")
            name = asset.get("name") or state.metadata.get("key_vision_name") or "主视觉原图"
        else:
            asset_id = state.metadata.get("key_vision_asset_id")
            asset_path = state.metadata.get("key_vision_asset_path")
            asset_url = state.metadata.get("key_vision_asset_url")
            name = state.metadata.get("key_vision_name") or "主视觉原图"
        if not asset_path and not asset_url:
            raise FileNotFoundError(
                "clip_storyboard_keyframe_generation requires the key vision style reference; "
                "run key_vision_image_generation first"
            )

        path: str | None = None
        if asset_path:
            existing = self.layout.existing_project_file(project_dir, str(asset_path))
            if existing is None:
                if not asset_url:
                    raise FileNotFoundError(
                        "clip_storyboard_keyframe_generation key vision image is missing: "
                        f"{asset_path}"
                    )
            else:
                path = str(project_dir / existing)
        return AssetRef(
            id=str(asset_id or "key_vision_original"),
            type="image",
            path=path,
            url=str(asset_url) if asset_url else None,
            metadata={
                "asset_type": "key_vision",
                "name": str(name),
                "reference_for": "storyboard_keyframe",
                "reference_role": "style_world_reference",
                "frame_role": frame_role,
                "panel_ref": panel_ref,
            },
        )

    def _keyframe_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        clip: StoryboardPromptClip,
        storyboard_sheet: StoryboardSheetGenerationItem,
        *,
        frame_role: str,
        panel_ref: str,
        limit: int,
    ) -> list[AssetRef]:
        refs: list[AssetRef] = []
        if limit <= 0:
            return refs
        if limit < 2:
            raise ValueError(
                "clip_storyboard_keyframe_generation requires at least 2 reference image slots "
                "for the storyboard composition reference and key vision style reference"
            )

        def append(ref: AssetRef) -> bool:
            if len(refs) >= limit:
                return False
            refs.append(ref)
            return len(refs) < limit

        for ref in self._storyboard_ref(project_dir, storyboard_sheet, frame_role=frame_role, panel_ref=panel_ref):
            if not append(ref):
                return refs

        if not append(
            self._keyframe_key_vision_ref(
                project_dir,
                state,
                frame_role=frame_role,
                panel_ref=panel_ref,
            )
        ):
            return refs

        for role_id in clip.role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearance = role.appearances.get("base") or next(iter(role.appearances.values()), None)
            if appearance is None:
                continue
            asset_path = appearance.asset_path or appearance.design_image_asset_path
            asset_url = appearance.asset_url or appearance.design_image_asset_url
            existing = self.layout.existing_project_file(project_dir, asset_path)
            if not existing and not asset_url:
                continue
            if not append(
                AssetRef(
                    id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=asset_url,
                    metadata={
                        "asset_type": "roleboard",
                        "role_id": role.id,
                        "role_name": role.name,
                        "appearance_id": appearance.id,
                        "appearance_name": appearance.name,
                        "reference_for": "storyboard_keyframe",
                        "clip_id": clip.clip_id,
                        "frame_role": frame_role,
                        "panel_ref": panel_ref,
                    },
                )
            ):
                return refs

        for layout_id in clip.layout_ids:
            layout = state.layouts.get(layout_id)
            if layout is None:
                continue
            existing = self.layout.existing_project_file(project_dir, layout.asset_path)
            if not existing and not layout.asset_url:
                continue
            if not append(
                AssetRef(
                    id=layout.asset_id or layout.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=layout.asset_url,
                    metadata={
                        "asset_type": "layout",
                        "layout_id": layout.id,
                        "layout_name": layout.name,
                        "reference_for": "storyboard_keyframe",
                        "clip_id": clip.clip_id,
                        "frame_role": frame_role,
                        "panel_ref": panel_ref,
                    },
                )
            ):
                return refs

        for prop_id in clip.prop_ids:
            prop = state.props.get(prop_id)
            if prop is None:
                continue
            existing = self.layout.existing_project_file(project_dir, prop.asset_path)
            if not existing and not prop.asset_url:
                continue
            if not append(
                AssetRef(
                    id=prop.asset_id or prop.id,
                    type="image",
                    path=str(project_dir / existing) if existing else None,
                    url=prop.asset_url,
                    metadata={
                        "asset_type": "prop",
                        "prop_id": prop.id,
                        "prop_name": prop.name,
                        "reference_for": "storyboard_keyframe",
                        "clip_id": clip.clip_id,
                        "frame_role": frame_role,
                        "panel_ref": panel_ref,
                    },
                )
            ):
                return refs

        return refs

    def _resume_keyframe_from_existing_file(
        self,
        *,
        project_dir: Path,
        provider: object,
        episode_key: str,
        clip: StoryboardPromptClip,
        storyboard_sheet: StoryboardSheetGenerationItem,
        frame_role: str,
        panel_ref: str,
        asset_id: str,
        existing_path: str | None,
        prompt: str,
        prompt_template: str,
    ) -> StoryboardKeyframeGenerationItem | None:
        existing_rel = self.layout.existing_project_file(project_dir, existing_path)
        if not existing_rel:
            return None
        return StoryboardKeyframeGenerationItem(
            episode_key=episode_key,
            clip_id=clip.clip_id,
            frame_role=frame_role,
            panel_ref=panel_ref,
            source_storyboard_asset_id=storyboard_sheet.asset_id,
            prompt=prompt,
            asset_id=asset_id,
            asset_path=existing_rel,
            asset_url=None,
            provider=str(getattr(provider, "name", "unknown") or "unknown"),
            model=str(getattr(provider, "model", "") or ""),
            request={"prompt_template": prompt_template, "resumed_from_existing_file": True},
            response={"resumed_from_existing_file": True},
            usage={},
            request_id=None,
        )

    async def generate_keyframe(
        self,
        *,
        provider: object,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        clip: StoryboardPromptClip,
        storyboard_sheet: StoryboardSheetGenerationItem,
        frame_role: str,
        panel_ref: str,
        max_refs: int,
        supports_refs: bool,
    ) -> StoryboardKeyframeGenerationItem:
        asset_id = self.keyframe_asset_id(clip.clip_id, frame_role)
        output_path = self.layout.image_asset_path(project_dir, "storyboard_keyframes", asset_id)
        prompt, prompt_template = self._render_keyframe_prompt(
            provider=provider,
            state=state,
            episode_key=episode_key,
            clip=clip,
            storyboard_sheet=storyboard_sheet,
            frame_role=frame_role,
            panel_ref=panel_ref,
        )
        refs = (
            self._keyframe_reference_refs(
                project_dir,
                state,
                clip,
                storyboard_sheet,
                frame_role=frame_role,
                panel_ref=panel_ref,
                limit=max_refs,
            )
            if supports_refs and max_refs > 0
            else []
        )
        result, prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
            provider=provider,
            state=state,
            node_name=self.name,
            asset_id=asset_id,
            prompt=prompt,
            refs=refs,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "episode_key": episode_key,
                "clip_id": clip.clip_id,
                "frame_role": frame_role,
                "panel_ref": panel_ref,
                "asset_id": asset_id,
                "asset_type": "storyboard_keyframe",
                "source_storyboard_asset_id": storyboard_sheet.asset_id,
                "prompt_template": prompt_template,
            },
            context={
                "asset_type": "storyboard_keyframe",
                "episode_key": episode_key,
                "clip_id": clip.clip_id,
                "frame_role": frame_role,
                "panel_ref": panel_ref,
            },
        )
        asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
        normalization_metadata = self._normalize_keyframe_image_file(
            project_dir,
            asset_path,
            provider=provider,
        )
        asset_url = self.first_image_url(result)
        raw_response = dict(result.raw_response or {})
        raw_response.update(normalization_metadata)
        item = StoryboardKeyframeGenerationItem(
            episode_key=episode_key,
            clip_id=clip.clip_id,
            frame_role=frame_role,
            panel_ref=panel_ref,
            source_storyboard_asset_id=storyboard_sheet.asset_id,
            prompt=prompt,
            asset_id=asset_id,
            asset_path=asset_path,
            asset_url=asset_url,
            provider=result.provider,
            model=result.model,
            request={
                "prompt_template": prompt_template,
                "refs": [ref.model_dump(mode="json") for ref in refs],
                **normalization_metadata,
            },
            response=raw_response,
            usage=result.usage,
            request_id=result.request_id,
        )
        self.logger.info("%s generated successfully, saved in %s", asset_id, asset_path)
        print(
            (
                "[autodrama] clip_storyboard_keyframe_generation generated "
                f"clip={clip.clip_id} frame_role={frame_role} asset_id={asset_id} path={asset_path}"
            ),
            flush=True,
        )
        return item

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("storyboard", node_name=self.name)
        self.logger.info(
            "node=clip_storyboard_keyframe_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prompt_output = self.load_clip_storyboard_prompt_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        target_set = set(target_episode_keys)
        prompt_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        sheet_by_clip = {item.clip_id: item for item in sheet_output.generated_storyboards}
        existing_by_key = self._load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 1) or 1))
        supports_refs = bool(max_refs and getattr(provider, "supports_reference_images", False))
        if not supports_refs or max_refs < 2:
            raise ValueError(
                "clip_storyboard_keyframe_generation requires an image provider with at least 2 "
                "reference image slots so every keyframe can use both the storyboard and key vision"
            )
        generated_by_key = dict(existing_by_key)
        clip_selectors = self._active_clip_selectors()
        selected_clip_count = 0

        pending: list[tuple[str, StoryboardPromptClip, StoryboardSheetGenerationItem, str, str]] = []
        skipped_existing = 0
        for episode_key in target_episode_keys:
            episode = prompt_by_episode.get(episode_key)
            if episode is None:
                raise ValueError(f"clip_storyboard_keyframe_generation missing clip_storyboard_prompt episode: {episode_key}")
            for clip_index, clip in enumerate(episode.clips, start=1):
                if not self._clip_matches_active_selectors(
                    episode_key=episode_key,
                    clip=clip,
                    clip_index=clip_index,
                    selectors=clip_selectors,
                ):
                    continue
                selected_clip_count += 1
                storyboard_sheet = sheet_by_clip.get(clip.clip_id)
                if storyboard_sheet is None:
                    raise ValueError(f"clip_storyboard_keyframe_generation missing clip_storyboard_image_generation image for {clip.clip_id}")
                frame_specs = [("end", "P12")]
                if clip_index == 1:
                    frame_specs.insert(0, ("start", "P01"))
                for frame_role, panel_ref in frame_specs:
                    asset_id = self.keyframe_asset_id(clip.clip_id, frame_role)
                    key = (episode_key, clip.clip_id, frame_role)
                    existing_item = existing_by_key.get(key)
                    output_path = self.layout.image_asset_path(project_dir, "storyboard_keyframes", asset_id)
                    existing_file_path = self.layout.existing_project_file(project_dir, output_path)
                    if (
                        not force_pregen
                        and (
                            (
                                existing_item is not None
                                and self.layout.existing_project_file(project_dir, existing_item.asset_path)
                            )
                            or existing_file_path is not None
                        )
                    ):
                        reuse_source = "json" if existing_item is not None else "file"
                        if existing_item is None:
                            keyframe_prompt, prompt_template = self._render_keyframe_prompt(
                                provider=provider,
                                state=state,
                                episode_key=episode_key,
                                clip=clip,
                                storyboard_sheet=storyboard_sheet,
                                frame_role=frame_role,
                                panel_ref=panel_ref,
                            )
                            existing_item = self._resume_keyframe_from_existing_file(
                                project_dir=project_dir,
                                provider=provider,
                                episode_key=episode_key,
                                clip=clip,
                                storyboard_sheet=storyboard_sheet,
                                frame_role=frame_role,
                                panel_ref=panel_ref,
                                asset_id=asset_id,
                                existing_path=existing_file_path,
                                prompt=keyframe_prompt,
                                prompt_template=prompt_template,
                            )
                        if existing_item is None:
                            pending.append((episode_key, clip, storyboard_sheet, frame_role, panel_ref))
                            continue
                        print(
                            (
                                "[autodrama] clip_storyboard_keyframe_generation skip existing "
                                f"source={reuse_source} clip={clip.clip_id} frame_role={frame_role} "
                                f"asset_id={asset_id} path={existing_item.asset_path}"
                            ),
                            flush=True,
                        )
                        generated_by_key[key] = existing_item
                        skipped_existing += 1
                        continue
                    pending.append((episode_key, clip, storyboard_sheet, frame_role, panel_ref))

        if clip_selectors and selected_clip_count <= 0:
            raise ValueError(
                "clip_storyboard_keyframe_generation --clips matched no clips in selected episodes: "
                f"{', '.join(sorted(clip_selectors))}"
            )

        concurrency = self.generation_concurrency(provider)
        print(
            (
                "[autodrama] clip_storyboard_keyframe_generation "
                f"concurrency={concurrency} pending={len(pending)} skipped_existing={skipped_existing} "
                f"selected_clips={selected_clip_count}"
            ),
            flush=True,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(
            episode_key: str,
            clip: StoryboardPromptClip,
            storyboard_sheet: StoryboardSheetGenerationItem,
            frame_role: str,
            panel_ref: str,
        ) -> StoryboardKeyframeGenerationItem:
            async with semaphore:
                return await self.generate_keyframe(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    episode_key=episode_key,
                    clip=clip,
                    storyboard_sheet=storyboard_sheet,
                    frame_role=frame_role,
                    panel_ref=panel_ref,
                    max_refs=max_refs,
                    supports_refs=supports_refs,
                )

        tasks = [asyncio.create_task(generate_one(*item)) for item in pending]
        results = list(await asyncio.gather(*tasks, return_exceptions=True)) if tasks else []
        generated_items: list[StoryboardKeyframeGenerationItem] = []
        failures: list[tuple[str, str, str, BaseException]] = []
        for (episode_key, clip, _storyboard_sheet, frame_role, _panel_ref), result in zip(pending, results, strict=False):
            if isinstance(result, BaseException):
                failures.append((episode_key, clip.clip_id, frame_role, result))
                self.logger.error(
                    "clip_storyboard_keyframe_generation failed clip=%s frame_role=%s: %s",
                    clip.clip_id,
                    frame_role,
                    result,
                )
                continue
            generated_items.append(result)
        for item in generated_items:
            generated_by_key[(item.episode_key, item.clip_id, str(item.frame_role))] = item

        ordered_items: list[StoryboardKeyframeGenerationItem] = []
        for episode in prompt_output.storyboards:
            if episode.episode_key not in target_set and not any(
                key[0] == episode.episode_key for key in generated_by_key
            ):
                continue
            for clip_index, clip in enumerate(episode.clips, start=1):
                frame_specs = [("end", "P12")]
                if clip_index == 1:
                    frame_specs.insert(0, ("start", "P01"))
                for frame_role, _panel_ref in frame_specs:
                    key = (episode.episode_key, clip.clip_id, frame_role)
                    if key in generated_by_key:
                        ordered_items.append(generated_by_key[key])

        self.repo.save_node_output(
            project_dir,
            self.name,
            StoryboardKeyframeGenerationOutput(generated_keyframes=ordered_items),
        )
        if failures:
            preview = "; ".join(
                f"{episode_key}/{clip_id}/{frame_role}: {exc}"
                for episode_key, clip_id, frame_role, exc in failures[:5]
            )
            if len(failures) > 5:
                preview = f"{preview}; ..."
            raise ProviderBadResponseError(
                "clip_storyboard_keyframe_generation failed for "
                f"{len(failures)}/{len(pending)} pending keyframe image(s); "
                f"saved {len(generated_items)} successful new keyframe(s) and "
                f"{len(ordered_items)} total keyframe record(s). "
                "Rerun without --force to skip saved files and continue missing clips. "
                f"Failures: {preview}"
            )
        return state


class ClipManifestGenerationNode(StoryboardAssetNodeBase):
    name = "clip_manifest_generation"

    @staticmethod
    def _clean_text(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _dedupe_texts(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for value in values:
            text = cls._clean_text(value)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _keyframes_by_clip_role(
        keyframe_output: StoryboardKeyframeGenerationOutput,
    ) -> dict[tuple[str, str, str], StoryboardKeyframeGenerationItem]:
        return {
            (item.episode_key, item.clip_id, str(item.frame_role)): item
            for item in keyframe_output.generated_keyframes
        }

    @classmethod
    def _require_keyframe(
        cls,
        keyframes: dict[tuple[str, str, str], StoryboardKeyframeGenerationItem],
        *,
        episode_key: str,
        clip_id: str,
        frame_role: str,
    ) -> StoryboardKeyframeGenerationItem:
        item = keyframes.get((episode_key, clip_id, frame_role))
        if item is None:
            raise ValueError(
                "clip_manifest_generation missing clip_storyboard_keyframe_generation "
                f"{frame_role}_frame for {episode_key}/{clip_id}"
            )
        return item

    @classmethod
    def _dialogue_text(cls, line: str) -> tuple[str | None, str]:
        text = cls._clean_text(line)
        for separator in ("：", ":"):
            if separator in text:
                speaker, body = text.split(separator, 1)
                speaker = cls._clean_text(speaker)
                body = cls._clean_text(body)
                if speaker and body:
                    return speaker, body
        return None, text

    @classmethod
    def _dialogue_body_in_prompt(cls, prompt: str, dialogue_body: str) -> bool:
        body = cls._clean_text(dialogue_body).strip("“”\"'。！？!?，,；;：: ")
        prompt_text = cls._clean_text(prompt)
        return bool(body) and body in prompt_text

    @classmethod
    def _ensure_dialogue_in_video_prompt(cls, prompt: str, dialogue: list[str]) -> tuple[str, list[str]]:
        original_prompt = cls._clean_text(prompt)
        prompt = sanitize_video_prompt_text(original_prompt)
        additions: list[str] = []
        warnings: list[str] = []
        if prompt != original_prompt:
            warnings.append("video_prompt aspect-ratio/provider placeholders were removed")
        for line in dialogue:
            speaker, body = cls._dialogue_text(line)
            if not body or cls._dialogue_body_in_prompt(prompt, body):
                continue
            if speaker:
                additions.append(f"{speaker}说：“{body}”。说话时口型清晰匹配这句台词。")
            else:
                additions.append(f"画面内说话者说：“{body}”。说话时口型清晰匹配这句台词。")
            warnings.append(f"dialogue text was appended to video_prompt: {body}")
        if additions:
            prompt = (prompt.rstrip("。") + "。" if prompt else "") + " ".join(additions)
        if dialogue and "字幕" not in prompt:
            prompt = (prompt.rstrip("。") + "。" if prompt else "") + "画面不出现字幕、对白气泡、可读文字、水印、logo、片段编号或无关商标。"
        return prompt, warnings

    @classmethod
    def _lookup_keys(cls, *values: object) -> set[str]:
        keys: set[str] = set()
        for value in values:
            text = cls._clean_text(value)
            if not text:
                continue
            keys.add(text)
            keys.add(text.casefold())
        return keys

    def _role_lookup(self, state: ProjectState) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for role in state.roles.values():
            for key in self._lookup_keys(role.id, role.name, normalize_id("role", role.name), *role.aliases):
                lookup.setdefault(key, role.id)
        return lookup

    def _prop_lookup(self, state: ProjectState) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for prop in state.props.values():
            for key in self._lookup_keys(prop.id, prop.name, normalize_id("prop", prop.name)):
                lookup.setdefault(key, prop.id)
        return lookup

    def _layout_lookup(self, state: ProjectState) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for layout in state.layouts.values():
            for key in self._lookup_keys(layout.id, layout.name, normalize_id("layout", layout.name)):
                lookup.setdefault(key, layout.id)
        return lookup

    @classmethod
    def _resolve_ids(
        cls,
        *,
        explicit_ids: list[str],
        names: list[str],
        lookup: dict[str, str],
        text: str,
        fallback_prefix: str | None = None,
    ) -> list[str]:
        resolved: list[str] = []
        for value in explicit_ids:
            cleaned = cls._clean_text(value)
            if not cleaned:
                continue
            resolved.append(lookup.get(cleaned) or lookup.get(cleaned.casefold()) or cleaned)
        for value in names:
            cleaned = cls._clean_text(value)
            if not cleaned:
                continue
            fallback = cleaned
            if fallback_prefix and not cleaned.startswith(f"{fallback_prefix}_"):
                fallback = normalize_id(fallback_prefix, cleaned)
            resolved.append(lookup.get(cleaned) or lookup.get(cleaned.casefold()) or fallback)
        if not resolved:
            for key, value in lookup.items():
                if key and key in text and value not in resolved:
                    resolved.append(value)
        return cls._dedupe_texts(resolved)

    @staticmethod
    def _first_appearance_ids(state: ProjectState, role_ids: list[str]) -> list[str]:
        appearance_ids: list[str] = []
        for role_id in role_ids:
            role = state.roles.get(role_id)
            if role is None:
                continue
            appearance = next(iter(role.appearances.values()), None)
            if appearance is not None:
                appearance_ids.append(appearance.id)
        return appearance_ids

    def _layout_id_for_clip(
        self,
        *,
        state: ProjectState,
        clip_prompt: StoryboardPromptClip,
        lookup: dict[str, str],
        text: str,
        episode_key: str,
    ) -> str:
        for layout_id in clip_prompt.layout_ids:
            explicit = self._clean_text(layout_id)
            if explicit:
                return lookup.get(explicit) or lookup.get(explicit.casefold()) or explicit
        for key, value in lookup.items():
            if key and key in text:
                return value
        if len(state.layouts) == 1:
            return next(iter(state.layouts))
        return normalize_id("layout", episode_key)

    @classmethod
    def _layout_id_for_shot(
        cls,
        *,
        state: ProjectState,
        shot_prompt: StoryboardPromptClip,
        lookup: dict[str, str],
        text: str,
        episode_key: str,
    ) -> str:
        self = cls
        # Kept as a compatibility alias for older call sites.
        for layout_id in shot_prompt.layout_ids:
            explicit = self._clean_text(layout_id)
            if explicit:
                return lookup.get(explicit) or lookup.get(explicit.casefold()) or explicit
        for key, value in lookup.items():
            if key and key in text:
                return value
        if len(state.layouts) == 1:
            return next(iter(state.layouts))
        return normalize_id("layout", episode_key)

    @classmethod
    def _prompt_clip_text(cls, clip_prompt: StoryboardPromptClip) -> str:
        parts = [
            clip_prompt.clip_id,
            " ".join(clip_prompt.role_ids),
            " ".join(clip_prompt.layout_ids),
            " ".join(clip_prompt.prop_ids),
            clip_prompt.video_prompt,
        ]
        return " ".join(cls._clean_text(part) for part in parts if cls._clean_text(part))

    @classmethod
    def _prompt_shot_text(cls, shot_prompt: StoryboardPromptClip) -> str:
        return cls._prompt_clip_text(shot_prompt)

    def _video_prompt_for_clip(self, clip_prompt: StoryboardPromptClip) -> tuple[str, list[str]]:
        original_prompt = self._clean_text(clip_prompt.video_prompt)
        prompt = sanitize_video_prompt_text(original_prompt)
        warnings: list[str] = []
        if prompt != original_prompt:
            warnings.append("video_prompt aspect-ratio/provider placeholders were removed")
        if "字幕" not in prompt or "水印" not in prompt:
            prompt = (
                (prompt.rstrip("。") + "。" if prompt else "")
                + "画面不出现字幕、对白气泡、可读文字、水印、logo、片段编号或无关商标。"
            )
        return prompt, warnings

    def _video_prompt_for_shot(self, shot_prompt: StoryboardPromptClip) -> tuple[str, list[str]]:
        return self._video_prompt_for_clip(shot_prompt)

    def _clip_video_model_params(self, provider: object) -> dict[str, Any]:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        if isinstance(params, dict) and params:
            return dict(params)
        node_settings = self.repo.settings.nodes.get("clip_video_generation")
        if node_settings is None:
            return {}
        return dict(node_settings.params)

    def _clip_video_template_candidates(self, provider: object) -> list[str]:
        params = self._clip_video_model_params(provider)
        configured = str(params.get("prompt_template") or params.get("clip_video_prompt_template") or "").strip()
        if configured:
            return [configured.removesuffix(".md")]
        provider_name = slugify(str(getattr(provider, "name", "") or ""), fallback="provider").lower()
        model_name = slugify(str(getattr(provider, "model", "") or ""), fallback="model").lower()
        return [
            f"clip_video/{provider_name}_{model_name}",
            f"clip_video/{provider_name}",
            "clip_video/default",
        ]

    def _render_clip_video_prompt_template(
        self,
        *,
        provider: object,
        episode_key: str,
        shot_id: str,
        duration_seconds: float,
        video_prompt: str,
        clip_video_inputs: list[ClipVideoInput],
        is_first_clip: bool,
        start_frame_source_clip_id: str,
        end_frame_source_clip_id: str,
    ) -> tuple[str, str]:
        prompts = getattr(self.workflow, "prompts", None)
        if prompts is None:
            raise ValueError("clip_manifest_generation requires workflow prompt store to render final_video_prompt")
        params = self._clip_video_model_params(provider)
        negative_rules = params.get("negative_rules") or []
        if isinstance(negative_rules, str):
            negative_rules = [negative_rules]
        if not isinstance(negative_rules, list):
            negative_rules = []
        clean_negative_rules = [self._clean_text(rule) for rule in negative_rules if self._clean_text(rule)]
        if not clean_negative_rules:
            binding = getattr(provider, "model_binding", None)
            model_id = getattr(binding, "model_id", None)
            node_settings = self.repo.settings.nodes.get("clip_video_generation")
            if not model_id and node_settings is not None:
                model_id = node_settings.model
            model_label = str(model_id or getattr(provider, "model", "unknown"))
            raise ValueError(
                "clip_manifest_generation requires model-bound "
                f"nodes.clip_video_generation.params.negative_rules for {model_label}; "
                "put provider/model-specific negative rules under the same clip_video_generation node config."
            )
        negative_rules_text = "\n".join(f"- {rule}" for rule in clean_negative_rules)

        input_rows = [
            {
                "slot": item.slot,
                "asset_type": item.asset_type,
                "label": item.label,
                "asset_id": item.asset_id,
                "asset_path": item.asset_path,
                "asset_url": item.asset_url,
                "required": item.required,
                "metadata": item.metadata,
            }
            for item in clip_video_inputs
        ]
        slots_by_type: dict[str, list[str]] = {}
        for item in clip_video_inputs:
            slots_by_type.setdefault(str(item.asset_type), []).append(item.slot)
        clip_start_frame_slot = ", ".join(slots_by_type.get("clip_start_frame", [])) or "无"
        clip_end_frame_slot = ", ".join(slots_by_type.get("clip_end_frame", [])) or "无"
        storyboard_slot = ", ".join(slots_by_type.get("storyboard", [])) or "无"
        if is_first_clip:
            clip_continuity_instructions = (
                f"{clip_start_frame_slot} 是当前 clip 的首帧，视频必须从它自然开始；"
                f"{clip_end_frame_slot} 是当前 clip 的尾帧，视频必须最终收束到它；"
                f"{storyboard_slot} 是当前 clip 的十二宫格 storyboard，用于执行中段 Camera Shot 运动和切镜节奏。"
            )
        else:
            clip_continuity_instructions = (
                f"{clip_start_frame_slot} 是上一条 clip（{start_frame_source_clip_id}）的尾帧，"
                f"只用于当前视频开头的连续性，视频必须从 {clip_start_frame_slot} 开始；"
                "开头必须立刻硬切到当前 clip 的 P01 / 第一宫格内容，不要做丝滑变形过渡；"
                f"硬切后严格按照 {storyboard_slot} 的十二宫格 storyboard 和 Camera Shot 计划推进；"
                f"最终收束到 {clip_end_frame_slot} 当前 clip（{end_frame_source_clip_id}）尾帧。"
            )

        last_error: Exception | None = None
        for template_name in self._clip_video_template_candidates(provider):
            try:
                return (
                    prompts.render(
                        template_name,
                        episode_key=episode_key,
                        clip_id=shot_id,
                        shot_id=shot_id,
                        duration_seconds=duration_seconds,
                        video_prompt=video_prompt,
                        clip_video_inputs_json=json.dumps(input_rows, ensure_ascii=False, indent=2),
                        clip_start_frame_slot=clip_start_frame_slot,
                        clip_end_frame_slot=clip_end_frame_slot,
                        storyboard_input_slot=storyboard_slot,
                        roleboard_input_slots=", ".join(slots_by_type.get("roleboard", [])) or "无",
                        layout_input_slots=", ".join(slots_by_type.get("layout", [])) or "无",
                        prop_input_slots=", ".join(slots_by_type.get("prop", [])) or "无",
                        is_first_clip=str(is_first_clip).lower(),
                        requires_initial_hard_cut=str(not is_first_clip).lower(),
                        start_frame_source_clip_id=start_frame_source_clip_id,
                        end_frame_source_clip_id=end_frame_source_clip_id,
                        clip_continuity_instructions=clip_continuity_instructions,
                        negative_rules=negative_rules_text,
                        provider_name=getattr(provider, "name", "unknown"),
                        model_name=getattr(provider, "model", "-"),
                    ).strip(),
                    template_name,
                )
            except FileNotFoundError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise FileNotFoundError("No shot video prompt template candidates were available")

    @staticmethod
    def _provider_max_reference_images(provider: object) -> int:
        max_images = int(getattr(provider, "max_reference_images", 99) or 99)
        binding = getattr(provider, "model_binding", None)
        if binding is not None:
            params = getattr(binding, "params", {}) or {}
            if isinstance(params, dict) and params.get("max_reference_images") is not None:
                max_images = int(params.get("max_reference_images") or max_images)
            spec = getattr(binding, "spec", None)
            limits = getattr(spec, "limits", {}) if spec is not None else {}
            if isinstance(limits, dict) and limits.get("max_reference_images") is not None:
                max_images = min(max_images, int(limits.get("max_reference_images") or max_images))
        return max_images

    def _limit_clip_video_inputs_for_provider(
        self,
        *,
        inputs: list[ClipVideoInput],
        provider: object,
        clip_id: str,
    ) -> tuple[list[ClipVideoInput], list[str]]:
        max_images = self._provider_max_reference_images(provider)
        if len(inputs) <= max_images:
            return inputs, []
        if max_images < 3:
            raise ValueError(
                f"clip_manifest_generation requires at least 3 image refs for {clip_id} "
                f"(clip_start_frame, clip_end_frame, storyboard); provider supports max_reference_images={max_images}"
            )
        kept = inputs[:max_images]
        omitted = inputs[max_images:]
        warnings = [
            (
                f"clip_video_inputs truncated to provider max_reference_images={max_images}; "
                "omitted "
                + ", ".join(f"{item.slot}:{item.asset_type}" for item in omitted)
            )
        ]
        return kept, warnings

    @staticmethod
    def _append_clip_video_input(
        inputs: list[ClipVideoInput],
        *,
        asset_type: str,
        asset_id: str | None,
        asset_path: str | None,
        asset_url: str | None,
        source_node: str,
        label: str,
        required: bool = True,
        **metadata: Any,
    ) -> None:
        order = len(inputs) + 1
        inputs.append(
            ClipVideoInput(
                slot=f"image_{order}",
                asset_type=asset_type,
                asset_id=asset_id,
                asset_path=asset_path,
                asset_url=asset_url,
                source_node=source_node,
                label=label,
                required=required,
                order=order,
                role_id=metadata.get("role_id"),
                role_name=metadata.get("role_name"),
                appearance_id=metadata.get("appearance_id"),
                appearance_name=metadata.get("appearance_name"),
                layout_id=metadata.get("layout_id"),
                layout_name=metadata.get("layout_name"),
                prop_id=metadata.get("prop_id"),
                prop_name=metadata.get("prop_name"),
                metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
            )
        )

    def _clip_video_inputs_for_shot(
        self,
        *,
        state: ProjectState,
        start_frame: StoryboardKeyframeGenerationItem,
        end_frame: StoryboardKeyframeGenerationItem,
        storyboard_sheet: StoryboardSheetGenerationItem,
        role_ids: list[str],
        role_appearance_ids: list[str],
        layout_ids: list[str],
        prop_ids: list[str],
        is_first_clip: bool,
        start_frame_source_clip_id: str,
    ) -> tuple[list[ClipVideoInput], list[str]]:
        inputs: list[ClipVideoInput] = []
        warnings: list[str] = []
        self._append_clip_video_input(
            inputs,
            asset_type="clip_start_frame",
            asset_id=start_frame.asset_id,
            asset_path=start_frame.asset_path,
            asset_url=start_frame.asset_url,
            source_node="clip_storyboard_keyframe_generation",
            label=(
                "当前 clip 的首帧关键帧"
                if is_first_clip
                else f"上一 clip {start_frame_source_clip_id} 的尾帧，作为当前 clip 开头连续性起点"
            ),
            required=True,
            clip_id=start_frame.clip_id,
            frame_role=start_frame.frame_role,
            panel_ref=start_frame.panel_ref,
            source_clip_id=start_frame_source_clip_id,
        )
        self._append_clip_video_input(
            inputs,
            asset_type="clip_end_frame",
            asset_id=end_frame.asset_id,
            asset_path=end_frame.asset_path,
            asset_url=end_frame.asset_url,
            source_node="clip_storyboard_keyframe_generation",
            label="当前 clip 的尾帧关键帧",
            required=True,
            clip_id=end_frame.clip_id,
            frame_role=end_frame.frame_role,
            panel_ref=end_frame.panel_ref,
            source_clip_id=end_frame.clip_id,
        )
        self._append_clip_video_input(
            inputs,
            asset_type="storyboard",
            asset_id=storyboard_sheet.asset_id,
            asset_path=storyboard_sheet.asset_path,
            asset_url=storyboard_sheet.asset_url,
            source_node="clip_storyboard_image_generation",
            label="当前 clip 的 12 宫格故事板整图",
            required=True,
            clip_id=storyboard_sheet.clip_id,
        )

        explicit_appearance_ids = {str(value).strip() for value in role_appearance_ids if str(value).strip()}
        for role_id in role_ids:
            role = state.roles.get(role_id)
            if role is None:
                warnings.append(f"missing role for shot video input: {role_id}")
                self._append_clip_video_input(
                    inputs,
                    asset_type="roleboard",
                    asset_id=None,
                    asset_path=None,
                    asset_url=None,
                    source_node="roleboard_image_generation",
                    label=f"missing roleboard {role_id}",
                    role_id=role_id,
                )
                continue
            appearances = [
                appearance
                for appearance in role.appearances.values()
                if appearance.id in explicit_appearance_ids or appearance.name in explicit_appearance_ids
            ]
            if not appearances:
                base = role.appearances.get("base") or next(iter(role.appearances.values()), None)
                appearances = [base] if base is not None else []
            if not appearances:
                warnings.append(f"{role_id}: role has no appearance for shot video input")
                self._append_clip_video_input(
                    inputs,
                    asset_type="roleboard",
                    asset_id=None,
                    asset_path=None,
                    asset_url=None,
                    source_node="roleboard_image_generation",
                    label=f"{role.name} roleboard missing",
                    role_id=role.id,
                    role_name=role.name,
                )
                continue
            for appearance in appearances:
                asset_path = appearance.asset_path or appearance.design_image_asset_path
                asset_url = appearance.asset_url or appearance.design_image_asset_url
                if not (asset_path or asset_url):
                    warnings.append(f"{role_id}/{appearance.id}: roleboard image is missing")
                self._append_clip_video_input(
                    inputs,
                    asset_type="roleboard",
                    asset_id=appearance.asset_id or appearance.design_image_asset_id or appearance.id,
                    asset_path=asset_path,
                    asset_url=asset_url,
                    source_node="roleboard_image_generation",
                    label=f"{role.name} {appearance.name} 人物三视图/角色身份板",
                    role_id=role.id,
                    role_name=role.name,
                    appearance_id=appearance.id,
                    appearance_name=appearance.name,
                )

        for layout_id in layout_ids:
            layout = state.layouts.get(layout_id)
            if layout is None:
                warnings.append(f"missing layout for shot video input: {layout_id}")
                self._append_clip_video_input(
                    inputs,
                    asset_type="layout",
                    asset_id=None,
                    asset_path=None,
                    asset_url=None,
                    source_node="layout_image_generation",
                    label=f"missing layout {layout_id}",
                    layout_id=layout_id,
                )
                continue
            if not (layout.asset_path or layout.asset_url):
                warnings.append(f"{layout_id}: layout image is missing")
            self._append_clip_video_input(
                inputs,
                asset_type="layout",
                asset_id=layout.asset_id or layout.id,
                asset_path=layout.asset_path,
                asset_url=layout.asset_url,
                source_node="layout_image_generation",
                label=f"{layout.name} 场景三视图",
                layout_id=layout.id,
                layout_name=layout.name,
            )

        for prop_id in prop_ids:
            prop = state.props.get(prop_id)
            if prop is None:
                warnings.append(f"missing prop for shot video input: {prop_id}")
                self._append_clip_video_input(
                    inputs,
                    asset_type="prop",
                    asset_id=None,
                    asset_path=None,
                    asset_url=None,
                    source_node="prop_image_generation",
                    label=f"missing prop {prop_id}",
                    prop_id=prop_id,
                    required=False,
                )
                continue
            if not (prop.asset_path or prop.asset_url):
                warnings.append(f"{prop_id}: prop image is missing")
            self._append_clip_video_input(
                inputs,
                asset_type="prop",
                asset_id=prop.asset_id or prop.id,
                asset_path=prop.asset_path,
                asset_url=prop.asset_url,
                source_node="prop_image_generation",
                label=f"{prop.name} 道具三视图/道具参考图",
                prop_id=prop.id,
                prop_name=prop.name,
                required=False,
            )
        return inputs, warnings

    @staticmethod
    def _preserve_dynamic_fields(new_shot: StoryboardShot, existing: StoryboardShot | None) -> None:
        if existing is None:
            return
        new_shot.dialogue_audio_assets = list(existing.dialogue_audio_assets)
        new_shot.shot_bgm_assets = list(existing.shot_bgm_assets)
        new_shot.video_asset_id = existing.video_asset_id
        new_shot.video_asset_path = existing.video_asset_path
        new_shot.video_provider = existing.video_provider
        new_shot.video_model = existing.video_model
        new_shot.video_task_id = existing.video_task_id
        new_shot.video_task_status = existing.video_task_status
        new_shot.video_request_id = existing.video_request_id
        new_shot.video_last_frame_asset_path = existing.video_last_frame_asset_path
        new_shot.video_usage = dict(existing.video_usage)
        new_shot.video_raw_response = dict(existing.video_raw_response)
        new_shot.solidified_asset_ids = list(existing.solidified_asset_ids)

    @classmethod
    def _manifest_rebuild_clip_ids(
        cls,
        storyboard: StoryboardPromptEpisode,
        selected_clip_ids: set[str],
        existing_episode: StoryboardEpisodeOutput | None,
    ) -> set[str]:
        ordered_clip_ids = [cls._clean_text(clip.clip_id) for clip in storyboard.clips]
        rebuild_clip_ids = set(selected_clip_ids)

        # A clip's end keyframe is the next clip's start frame. Refresh the
        # direct successor so its manifest does not retain a stale URL/ref.
        for index, clip_id in enumerate(ordered_clip_ids[:-1]):
            if clip_id in selected_clip_ids:
                rebuild_clip_ids.add(ordered_clip_ids[index + 1])

        # A partial run must still leave a complete episode manifest. Build
        # any clips that do not yet exist instead of emitting a partial file.
        existing_clip_ids = {
            cls._clean_text(clip.clip_id)
            for clip in (existing_episode.clips if existing_episode else [])
        }
        rebuild_clip_ids.update(clip_id for clip_id in ordered_clip_ids if clip_id not in existing_clip_ids)
        return rebuild_clip_ids

    @classmethod
    def _merge_partial_episode_manifest(
        cls,
        *,
        storyboard: StoryboardPromptEpisode,
        existing_episode: StoryboardEpisodeOutput | None,
        rebuilt_episode: StoryboardEpisodeOutput,
    ) -> StoryboardEpisodeOutput:
        existing_by_id = {
            cls._clean_text(clip.clip_id): clip
            for clip in (existing_episode.clips if existing_episode else [])
        }
        rebuilt_by_id = {cls._clean_text(clip.clip_id): clip for clip in rebuilt_episode.clips}
        clips: list[StoryboardShot] = []
        for clip_prompt in storyboard.clips:
            clip_id = cls._clean_text(clip_prompt.clip_id)
            clip = rebuilt_by_id.get(clip_id) or existing_by_id.get(clip_id)
            if clip is None:
                raise ValueError(
                    "clip_manifest_generation could not preserve or rebuild "
                    f"{storyboard.episode_key}/{clip_id}"
                )
            clips.append(clip)
        return StoryboardEpisodeOutput(episode_key=storyboard.episode_key, clips=clips)

    def _build_episode_manifest(
        self,
        *,
        project_dir: Path,
        state: ProjectState,
        storyboard: StoryboardPromptEpisode,
        storyboard_sheets: dict[str, StoryboardSheetGenerationItem],
        keyframes: dict[tuple[str, str, str], StoryboardKeyframeGenerationItem],
        existing_episode: StoryboardEpisodeOutput | None,
        included_clip_ids: set[str] | None = None,
    ) -> tuple[StoryboardEpisodeOutput, list[str]]:
        role_lookup = self._role_lookup(state)
        prop_lookup = self._prop_lookup(state)
        layout_lookup = self._layout_lookup(state)
        existing_by_id = {clip.clip_id: clip for clip in (existing_episode.clips if existing_episode else [])}
        existing_by_index = {clip.index: clip for clip in (existing_episode.clips if existing_episode else [])}
        episode_warnings: list[str] = []
        clips: list[StoryboardShot] = []
        video_provider = self.router.video("shot", node_name="clip_video_generation")

        for clip_index, clip_prompt in enumerate(storyboard.clips, start=1):
            clip_id = self._clean_text(clip_prompt.clip_id)
            if included_clip_ids is not None and clip_id not in included_clip_ids:
                continue
            is_first_clip = clip_index == 1
            previous_clip_id = self._clean_text(storyboard.clips[clip_index - 2].clip_id) if not is_first_clip else clip_id
            prompt_text = self._prompt_clip_text(clip_prompt)
            role_ids = self._resolve_ids(
                explicit_ids=list(clip_prompt.role_ids or []),
                names=[],
                lookup=role_lookup,
                text=prompt_text,
                fallback_prefix="role",
            )
            prop_ids = self._resolve_ids(
                explicit_ids=list(clip_prompt.prop_ids or []),
                names=[],
                lookup=prop_lookup,
                text=prompt_text,
                fallback_prefix="prop",
            )
            layout_ids = self._resolve_ids(
                explicit_ids=list(clip_prompt.layout_ids or []),
                names=[],
                lookup=layout_lookup,
                text=prompt_text,
                fallback_prefix="layout",
            )
            if not layout_ids:
                layout_ids = [
                    self._layout_id_for_clip(
                        state=state,
                        clip_prompt=clip_prompt,
                        lookup=layout_lookup,
                        text=prompt_text,
                        episode_key=storyboard.episode_key,
                    )
                ]
            layout_id = layout_ids[0] if layout_ids else normalize_id("layout", storyboard.episode_key)
            role_appearance_ids = self._first_appearance_ids(state, role_ids)
            role_audio_ids: list[str] = []
            dialogue: list[str] = []
            video_prompt, prompt_warnings = self._video_prompt_for_clip(clip_prompt)
            episode_warnings.extend(f"{clip_id}: {warning}" for warning in prompt_warnings)
            storyboard_sheet = storyboard_sheets.get(clip_id)
            if storyboard_sheet is None:
                raise ValueError(f"clip_manifest_generation missing clip_storyboard_image_generation image for {clip_id}")
            current_end_frame = self._require_keyframe(
                keyframes,
                episode_key=storyboard.episode_key,
                clip_id=clip_id,
                frame_role="end",
            )
            if is_first_clip:
                start_frame = self._require_keyframe(
                    keyframes,
                    episode_key=storyboard.episode_key,
                    clip_id=clip_id,
                    frame_role="start",
                )
                start_frame_source_clip_id = clip_id
            else:
                start_frame = self._require_keyframe(
                    keyframes,
                    episode_key=storyboard.episode_key,
                    clip_id=previous_clip_id,
                    frame_role="end",
                )
                start_frame_source_clip_id = previous_clip_id
            clip_video_inputs, input_warnings = self._clip_video_inputs_for_shot(
                state=state,
                start_frame=start_frame,
                end_frame=current_end_frame,
                storyboard_sheet=storyboard_sheet,
                role_ids=role_ids,
                role_appearance_ids=role_appearance_ids,
                layout_ids=layout_ids,
                prop_ids=prop_ids,
                is_first_clip=is_first_clip,
                start_frame_source_clip_id=start_frame_source_clip_id,
            )
            clip_video_inputs, limit_warnings = self._limit_clip_video_inputs_for_provider(
                inputs=clip_video_inputs,
                provider=video_provider,
                clip_id=clip_id,
            )
            episode_warnings.extend(f"{clip_id}: {warning}" for warning in input_warnings)
            episode_warnings.extend(f"{clip_id}: {warning}" for warning in limit_warnings)
            final_video_prompt, prompt_template = self._render_clip_video_prompt_template(
                provider=video_provider,
                episode_key=storyboard.episode_key,
                shot_id=clip_id,
                duration_seconds=float(clip_prompt.duration_seconds),
                video_prompt=video_prompt,
                clip_video_inputs=clip_video_inputs,
                is_first_clip=is_first_clip,
                start_frame_source_clip_id=start_frame_source_clip_id,
                end_frame_source_clip_id=clip_id,
            )
            for item in clip_video_inputs:
                item.metadata.setdefault("final_video_prompt_template", prompt_template)
                item.metadata.setdefault("video_model", getattr(video_provider, "model", "-"))
            clip = StoryboardShot(
                clip_id=clip_id,
                index=clip_index,
                layout_id=layout_id,
                layout_ids=layout_ids,
                title=f"Clip {clip_index:03d}",
                content=video_prompt,
                duration_seconds=float(clip_prompt.duration_seconds),
                transition="硬切",
                start_frame_source="own_start_frame" if is_first_clip else "previous_clip_end_frame",
                start_frame_inheritance_reason=(
                    "首个 clip 使用自己的 P01 首帧关键帧。"
                    if is_first_clip
                    else f"非首个 clip 使用上一 clip {start_frame_source_clip_id} 的 P12 尾帧作为开头连续性起点。"
                ),
                dialogue=dialogue,
                role_ids=role_ids,
                role_appearance_ids=role_appearance_ids,
                role_audio_ids=role_audio_ids,
                prop_ids=prop_ids,
                storyboard_asset_id=storyboard_sheet.asset_id,
                storyboard_asset_path=storyboard_sheet.asset_path,
                source_storyboard_asset_path=storyboard_sheet.asset_path,
                video_prompt=video_prompt,
                final_video_prompt=final_video_prompt,
                clip_video_inputs=clip_video_inputs,
                start_frame_asset_id=start_frame.asset_id,
                start_frame_asset_path=start_frame.asset_path,
                start_frame_asset_url=start_frame.asset_url,
                start_frame_source_clip_id=start_frame_source_clip_id,
                end_frame_asset_id=current_end_frame.asset_id,
                end_frame_asset_path=current_end_frame.asset_path,
                end_frame_asset_url=current_end_frame.asset_url,
                is_first_clip=is_first_clip,
                requires_initial_hard_cut=not is_first_clip,
            )
            existing = existing_by_id.get(clip.clip_id) or existing_by_index.get(clip.index)
            self._preserve_dynamic_fields(clip, existing)
            clips.append(clip)

        return StoryboardEpisodeOutput(episode_key=storyboard.episode_key, clips=clips), episode_warnings

    def _load_existing_episode(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput | None:
        path = self.layout.shot_path(project_dir, episode_key)
        if not path.exists():
            return None
        try:
            return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("clip_manifest_generation ignored invalid existing shot file %s: %s", path, exc)
            return None

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        prompt_output = self.load_clip_storyboard_prompt_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        keyframe_output = self.load_storyboard_keyframe_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        storyboard_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        storyboard_sheets = {item.clip_id: item for item in sheet_output.generated_storyboards}
        keyframes = self._keyframes_by_clip_role(keyframe_output)
        clip_selectors = self.active_clip_selectors()
        selected_clip_ids_by_episode: dict[str, set[str]] = {}
        selected_clip_count = 0
        if clip_selectors:
            for episode_key in target_episode_keys:
                storyboard = storyboard_by_episode.get(episode_key)
                if storyboard is None:
                    continue
                selected_clip_ids = {
                    self._clean_text(clip.clip_id)
                    for clip_index, clip in enumerate(storyboard.clips, start=1)
                    if self.clip_matches_active_selectors(
                        episode_key=episode_key,
                        clip=clip,
                        clip_index=clip_index,
                        selectors=clip_selectors,
                    )
                }
                selected_clip_ids_by_episode[episode_key] = selected_clip_ids
                selected_clip_count += len(selected_clip_ids)
            if selected_clip_count <= 0:
                raise ValueError(
                    "clip_manifest_generation --clips matched no clips in selected episodes: "
                    f"{', '.join(sorted(clip_selectors))}"
                )
        generated: list[ClipManifestGenerationEpisodeItem] = []

        for episode_key in target_episode_keys:
            if clip_selectors and not selected_clip_ids_by_episode.get(episode_key):
                continue
            storyboard = storyboard_by_episode.get(episode_key)
            if storyboard is None:
                raise ValueError(f"clip_manifest_generation missing clip_storyboard_prompt episode: {episode_key}")
            if not storyboard.clips:
                raise ValueError(f"clip_manifest_generation requires at least one clip for {episode_key}")
            existing = self._load_existing_episode(project_dir, episode_key)
            included_clip_ids = None
            if clip_selectors:
                included_clip_ids = self._manifest_rebuild_clip_ids(
                    storyboard,
                    selected_clip_ids_by_episode.get(episode_key, set()),
                    existing,
                )
            rebuilt_episode, warnings = self._build_episode_manifest(
                project_dir=project_dir,
                state=state,
                storyboard=storyboard,
                storyboard_sheets=storyboard_sheets,
                keyframes=keyframes,
                existing_episode=existing,
                included_clip_ids=included_clip_ids,
            )
            episode = (
                self._merge_partial_episode_manifest(
                    storyboard=storyboard,
                    existing_episode=existing,
                    rebuilt_episode=rebuilt_episode,
                )
                if clip_selectors
                else rebuilt_episode
            )
            self.workflow._save_storyboard_episode(project_dir, episode)
            shot_path = self.layout.project_relative(project_dir, self.layout.shot_path(project_dir, episode_key))
            generated.append(
                ClipManifestGenerationEpisodeItem(
                    episode_key=episode_key,
                    clip_count=len(episode.clips),
                    clip_path=shot_path,
                    warnings=warnings,
                )
            )
            self.logger.info(
                "clip_manifest_generation wrote %s clips=%d rebuilt_clips=%d selected_clips=%d warnings=%d",
                shot_path,
                len(episode.clips),
                len(rebuilt_episode.clips),
                len(selected_clip_ids_by_episode.get(episode_key, set())) if clip_selectors else len(episode.clips),
                len(warnings),
            )

        existing_output_path = self.layout.node_output_path(project_dir, self.name)
        existing_items: dict[str, ClipManifestGenerationEpisodeItem] = {}
        if existing_output_path.exists():
            try:
                existing_output = ClipManifestGenerationOutput.model_validate_json(
                    existing_output_path.read_text(encoding="utf-8")
                )
                existing_items.update({item.episode_key: item for item in existing_output.episodes})
            except Exception as exc:
                self.logger.warning("clip_manifest_generation ignored invalid existing output: %s", exc)
        existing_items.update({item.episode_key: item for item in generated})
        ordered = [
            existing_items[episode_key]
            for episode_key in self.expected_episode_keys(state)
            if episode_key in existing_items
        ]
        self.repo.save_node_output(project_dir, self.name, ClipManifestGenerationOutput(episodes=ordered))
        return state


def build_storyboard_asset_node_runners(workflow: Any) -> dict[str, StoryboardAssetNodeBase]:
    from autodrama.repositories.prop_design_repo import PropDesignRepository
    from autodrama.repositories.script_content_repo import ScriptContentRepository

    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    prop_designs = getattr(workflow, "prop_designs", None)
    if prop_designs is None:
        prop_designs = PropDesignRepository(workflow.repo, workflow.layout)
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "asset_service": workflow.asset_service,
        "script_contents": script_contents,
        "prop_designs": prop_designs,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or getattr(workflow, "_logger", None),
    }
    if deps["logger"] is None:
        from autodrama.logging import get_logger

        deps["logger"] = get_logger()
    return {
        ClipPromptNode.name: ClipPromptNode(**deps),
        StoryboardPromptNode.name: StoryboardPromptNode(**deps),
        StoryboardGenerationNode.name: StoryboardGenerationNode(**deps),
        StoryboardKeyframeGenerationNode.name: StoryboardKeyframeGenerationNode(**deps),
        ClipManifestGenerationNode.name: ClipManifestGenerationNode(**deps),
    }


def build_storyboard_asset_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_storyboard_asset_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in STORYBOARD_ASSET_NODE_NAMES
    ]


__all__ = [
    "STORYBOARD_ASSET_NODE_NAMES",
    "STORYBOARD_IMAGE_PROVIDER_NODE_NAME",
    "ClipPromptNode",
    "ClipManifestGenerationNode",
    "StoryboardGenerationNode",
    "StoryboardKeyframeGenerationNode",
    "StoryboardPromptNode",
    "build_storyboard_asset_node_runners",
    "build_storyboard_asset_nodes",
]
