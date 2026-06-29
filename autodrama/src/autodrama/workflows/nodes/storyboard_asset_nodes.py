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
    "storyboard_prompt",
    "storyboard_generation",
    "storyboard_keyframe_generation",
    "clip_manifest_generation",
]
STORYBOARD_IMAGE_PROVIDER_NODE_NAME = "storyboard_sheet_generation"


class StoryboardAssetNodeBase(StaticAssetNodeBase):
    STORYBOARD_PANEL_COUNT = 12
    STORYBOARD_GRID = "4x3"
    STORYBOARD_PANEL_ASPECT_RATIO = "3:4"
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
        for node_name, param_name in (
            ("clip_video_generation", "ratio"),
            ("storyboard_sheet_generation", "size"),
        ):
            node_settings = self.repo.settings.nodes.get(node_name)
            if node_settings is None:
                continue
            ratio = self._ratio_from_value(node_settings.params.get(param_name))
            if ratio:
                return ratio
        return "9:16"

    @classmethod
    def storyboard_grid(cls) -> str:
        return cls.STORYBOARD_GRID

    @classmethod
    def storyboard_panel_aspect_ratio(cls) -> str:
        return cls.STORYBOARD_PANEL_ASPECT_RATIO

    @classmethod
    def storyboard_sheet_size(cls) -> str:
        return "1:1"

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
        path = self.layout.node_output_path(project_dir, "clip_segment")
        if not path.exists():
            raise FileNotFoundError("clip_segment output is missing; run pregen through clip_segment first")
        return ClipSegmentNodeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def clip_segments_by_episode(self, project_dir: Path) -> dict[str, object]:
        return dict(self.load_clip_segment_output(project_dir).root)

    def episode_story_context(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> dict[str, str]:
        refs = state.script.novel_extract
        if not any(refs.get(episode_key) for episode_key in episode_keys):
            refs = state.script.novel_full
        return self.script_contents.load_contents(
            project_dir,
            refs,
            episode_keys,
            label="storyboard_prompt.episode_stories",
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

    def load_storyboard_prompt_output(self, project_dir: Path) -> StoryboardPromptOutput:
        path = self.layout.node_output_path(project_dir, "storyboard_prompt")
        if not path.exists():
            raise FileNotFoundError("storyboard_prompt output is missing; run pregen through storyboard_prompt first")
        return StoryboardPromptOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_storyboard_sheet_output(self, project_dir: Path) -> StoryboardSheetGenerationOutput:
        path = self.layout.node_output_path(project_dir, "storyboard_generation")
        if not path.exists():
            raise FileNotFoundError("storyboard_generation output is missing; run pregen through storyboard_generation first")
        return StoryboardSheetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_storyboard_keyframe_output(self, project_dir: Path) -> StoryboardKeyframeGenerationOutput:
        path = self.layout.node_output_path(project_dir, "storyboard_keyframe_generation")
        if not path.exists():
            raise FileNotFoundError(
                "storyboard_keyframe_generation output is missing; run pregen through storyboard_keyframe_generation first"
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
            raise ValueError(f"storyboard_prompt missing clip_segment episode(s): {', '.join(missing)}")
        return expected

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

    def validate_storyboard_prompt_output(
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
                    f"storyboard_prompt returned unexpected episode_key {episode.episode_key!r}; "
                    f"expected one of {', '.join(expected_episode_keys)}"
                )
            if episode.episode_key in seen:
                raise ValueError(f"storyboard_prompt returned duplicate episode_key: {episode.episode_key}")
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
            if require_complete and len(episode.clips) != len(ordered_source_keys):
                raise ValueError(
                    f"storyboard_prompt must return {len(ordered_source_keys)} clips for {episode.episode_key}; "
                    f"got {len(episode.clips)}"
                )
            validated_clips: list[StoryboardPromptClip] = []
            returned_clip_ids: set[str] = set()
            for clip in episode.clips:
                candidate_clip_id = str(clip.clip_id or "").strip().replace("_shot_", "_clip_")
                expected_item = expected_by_id.get(candidate_clip_id)
                if expected_item is None:
                    raise ValueError(
                        f"storyboard_prompt returned clip_id outside requested batch for {episode.episode_key}: "
                        f"got {clip.clip_id or '-'}; expected one of {', '.join(expected_by_id) or '-'}"
                    )
                source_key, expected_index = expected_item
                expected_clip_id = self.clip_id_for_episode_index(episode.episode_key, expected_index)
                clip.clip_id = expected_clip_id
                if clip.clip_id in seen_clips:
                    raise ValueError(f"storyboard_prompt returned duplicate clip_id: {clip.clip_id}")
                seen_clips.add(clip.clip_id)
                returned_clip_ids.add(clip.clip_id)
                try:
                    clip.duration_seconds = float(clip.duration_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"storyboard_prompt invalid duration_seconds for {clip.clip_id}") from exc
                if not 8 <= clip.duration_seconds <= 15 and getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "storyboard_prompt duration_seconds for %s is outside 8-15s guidance: %s",
                        clip.clip_id,
                        clip.duration_seconds,
                    )
                clip.role_ids = self._dedupe_nonempty_texts(clip.role_ids)
                clip.layout_ids = self._dedupe_nonempty_texts(clip.layout_ids)
                clip.prop_ids = self._dedupe_nonempty_texts(clip.prop_ids)
                if not clip.layout_ids:
                    raise ValueError(f"storyboard_prompt layout_ids is empty for {clip.clip_id}")
                clip.video_prompt = sanitize_video_prompt_text(str(clip.video_prompt or "").strip())
                if not clip.video_prompt:
                    raise ValueError(f"storyboard_prompt returned empty video_prompt for {clip.clip_id}")
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
                        f"storyboard_prompt panel_plan for {clip.clip_id} must cover P01-P12; "
                        f"missing {', '.join(missing_panels)}"
                    )
                camera_shot_count = self._camera_shot_count(clip)
                if camera_shot_count < 1:
                    raise ValueError(f"storyboard_prompt {clip.clip_id} must include at least one Camera Shot")
                if not 2 <= camera_shot_count <= 4 and getattr(self, "logger", None) is not None:
                    self.logger.warning(
                        "storyboard_prompt %s has %d Camera Shot(s); recommended range is 2-4",
                        clip.clip_id,
                        camera_shot_count,
                    )
                validated_clips.append(clip)
            if require_complete:
                missing_clip_ids = sorted(set(expected_by_id).difference(returned_clip_ids))
                if missing_clip_ids:
                    raise ValueError(
                        f"storyboard_prompt missing clip(s) for {episode.episode_key}: "
                        f"{', '.join(missing_clip_ids)}"
                    )
            episode.clips = validated_clips
            cleaned.append(episode)
        missing = [episode_key for episode_key in expected_episode_keys if episode_key not in seen]
        if missing and require_complete:
            raise ValueError(f"storyboard_prompt missing episode(s): {', '.join(missing)}")
        for episode_key in missing:
            cleaned.append(StoryboardPromptEpisode(episode_key=episode_key, clips=[]))
        return StoryboardPromptOutput(
            storyboards=[
                next(episode for episode in cleaned if episode.episode_key == episode_key)
                for episode_key in expected_episode_keys
            ]
        )

    def merge_storyboard_prompt_outputs(
        self,
        *,
        project_dir: Path,
        generated_output: StoryboardPromptOutput,
        target_episode_keys: list[str],
        all_episode_keys: list[str],
    ) -> StoryboardPromptOutput:
        existing_path = self.layout.node_output_path(project_dir, "storyboard_prompt")
        by_episode: dict[str, StoryboardPromptEpisode] = {}
        if existing_path.exists():
            try:
                existing = StoryboardPromptOutput.model_validate_json(existing_path.read_text(encoding="utf-8"))
                by_episode.update({episode.episode_key: episode for episode in existing.storyboards})
            except Exception as exc:
                self.logger.warning("storyboard_prompt ignored invalid existing output %s: %s", existing_path, exc)
        by_episode.update({episode.episode_key: episode for episode in generated_output.storyboards})
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

    def storyboard_image_prompt(self, episode_key: str, clip: StoryboardPromptClip) -> str:
        return "\n".join(
            [
                (
                    f"为 {episode_key} 的 {clip.clip_id} 生成一张完整分镜故事板图。"
                    f"这个 storyboard clip 时长 {clip.duration_seconds:g} 秒，一张故事板覆盖整个 clip。"
                ),
                "固定要求：1:1 方形故事板整图，严格 4 列 x 3 行 = 12 个电影风格面板。每个宫格是当前 clip 的一个关键画面，不是一个独立 shot，也不是逐秒切片；多个宫格可以属于同一个内部 camera shot。每个宫格上方是黑白故事板画面，下方必须留出一条清晰的文字说明带。",
                "实际故事板绘图必须仅为黑白：粗糙的铅笔线条、最小细节、快速手势绘图能量、简单的解剖结构构建、强烈的轮廓可读性。保持艺术作品轻量、动态且未完成，像早期影视预演分镜，不要画成最终彩色成片。",
                "必须严格按照下方 video_prompt 中的“十二宫格面板规划 P01-P12”绘制：每个面板都要对应一个明确的 P 编号内容，不能漏画、合并或重新编排。每个面板要清楚体现画面内容、人物动作、镜头关系、情绪节奏、声音/对白提示和所属 camera shot。",
                "每个面板必须包含可见的动作、状态变化或镜头推进。避免重复、呆板或静态站立构图；同一 camera shot 内的连续面板要表现同一个镜头里的动作发展，不要画成频繁硬切。",
                "切镜标记必须非常明显：在 video_prompt 标出的 camera shot 边界处，把红色斜杠 cut mark 画在两个相邻宫格的正中间分隔线上，像一条跨越中线的清楚斜杠，不要画在某个宫格的右下角或角落里。如果边界是 P04-P05 或 P08-P09 这种跨行连续编号，不要把斜杠画到整图右侧边角；应画在两行之间的水平分隔线中央位置，并用小黑字标注“CUT P04-P05”或“CUT P08-P09”。红色斜杠只表示剪辑点，不能画在人物脸上、关键道具上或被误解成剧情物体；除 cut mark 和少量箭头标注外不要使用红色。",
                "使用电影感摄影方式，包含但不限于：手持感、快速平移、环绕运动、俯拍、仰拍、侧面轮廓、侵略性特写、长焦压缩、极端负空间。镜头语言要服务剧情，不要平均分配，要根据 camera shot 和情绪重点变化。",
                "环境保持简洁，只保留对剧情有帮助的关键场景元素。避免无关杂乱背景，重点突出人物、动作、空间关系、光线方向和氛围。",
                "标注颜色系统：红色箭头=身体运动，蓝色箭头=摄影机运动，绿色标记=取景/构图笔记，橙色标记=灯光方向，紫色标记=情绪/声音/叙事强调，黑色文本=简短镜头笔记和面板标签。每个宫格底部文字说明带必须额外写一行颜色图例文字，按本格实际使用的颜色逐项说明箭头作用，例如“红=角色动作，蓝=镜头运动，绿=构图，橙=灯光，紫=情绪/声音，黑=镜头注记”。标注必须少量、清晰、服务制作，不要遮挡主体。",
                "宫格之间有清晰分隔和留白，不得重叠、裁脸或压住人物肢体。允许很小的面板编号 01-12 和极短制作注释；不要生成字幕、对白气泡、水印、logo、文件名、项目名、资产 ID、二维码或大段可读文字。整张图底部再留一条全局颜色图例说明带，重复说明各色箭头的含义，方便一眼识别。",
                "输入参考图优先级：角色外观以传入的人物身份板/角色板为准；场景空间以传入的场景图/场景三视图为准；如有道具参考图，道具以传入的道具设计图为准。禁止使用主视觉图/key_vision 作为参考或构图依据。",
                "storyboard_generation 的输入由当前 storyboard_prompt 的 clip.video_prompt、对应角色身份板图、对应场景图和本固定 12 宫格故事板模板组成；不要参考主视觉图，不要新增剧情事实、角色、场景或道具。",
                "当前 clip 视频提示词：",
                clip.video_prompt,
            ]
        )


class StoryboardPromptNode(StoryboardAssetNodeBase):
    name = "storyboard_prompt"
    MAX_CLIPS_PER_BATCH = 8
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
                "storyboard_prompt_concurrency",
                "storyboard_prompt_batch_concurrency",
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
                "storyboard_prompt_concurrency",
                "storyboard_prompt_batch_concurrency",
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
            label="storyboard_prompt.episode_window_full",
            allow_missing=True,
        )
        if episode_key not in contents:
            contents.update(self.episode_story_context(project_dir, state, [episode_key]))
        return {key: contents[key] for key in window_keys if key in contents}

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

    def _storyboard_prompt_reference_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        role_ids: list[str],
        layout_ids: list[str],
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
                        "reference_for": "storyboard_prompt",
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
                        "reference_for": "storyboard_prompt",
                    },
                ),
                {
                    "asset_type": "layout",
                    "layout_id": layout.id,
                    "layout_name": layout.name,
                },
            )
        return refs, context

    async def _generate_storyboard_prompt_batch(
        self,
        *,
        provider: object,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        all_episode_keys: list[str],
        final_aspect_ratio: str,
        total_clip_count_by_episode: dict[str, int],
        batch_index: int,
        batch_total: int,
        batch_clips: dict[str, object],
    ) -> tuple[str, int, list[StoryboardPromptClip]]:
        batch_keys = self._sorted_clip_segment_keys(batch_clips)
        batch_clip_count_by_episode = {episode_key: len(batch_clips)}
        role_ids = self._role_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        layout_ids = self._layout_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        prop_ids = self._prop_ids_for_batch(state, episode_key=episode_key, batch_clips=batch_clips)
        refs, reference_image_context = self._storyboard_prompt_reference_refs(
            project_dir,
            state,
            role_ids=role_ids,
            layout_ids=layout_ids,
        )
        first_key = batch_keys[0] if batch_keys else "-"
        last_key = batch_keys[-1] if batch_keys else "-"
        self.logger.info(
            "node=storyboard_prompt episode=%s batch=%d/%d clips=%s-%s refs=%d",
            episode_key,
            batch_index,
            batch_total,
            first_key,
            last_key,
            len(refs),
        )
        prompt = self.workflow.prompts.render(
            "storyboard_prompt",
            title=state.title,
            episode_keys=episode_key,
            clip_batch=(
                f"{episode_key} clip_segment keys {first_key}-{last_key}; "
                f"batch {batch_index}/{batch_total}; max {self.MAX_CLIPS_PER_BATCH} clips per call"
            ),
            clip_count=self._format_json(batch_clip_count_by_episode),
            shot_count=self._format_json(batch_clip_count_by_episode),
            clip_count_by_episode=self._format_json(batch_clip_count_by_episode),
            total_clip_count_by_episode=self._format_json(total_clip_count_by_episode),
            final_aspect_ratio=final_aspect_ratio,
            storyboard_panel_count=self.STORYBOARD_PANEL_COUNT,
            storyboard_grid=self.storyboard_grid(),
            storyboard_panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            raw_script=state.raw_script,
            novel_extract=self._format_json(self.episode_story_context(project_dir, state, [episode_key])),
            novel_full=self._format_json(
                self._episode_window_full_context(project_dir, state, episode_key, all_episode_keys)
            ),
            director_prep=DirectorService.director_prep_context(state, episode_keys=[episode_key]),
            roleboard_context=self._format_json(self._roleboard_context_for_ids(project_dir, state, role_ids)),
            layout_context=self._format_json(self._layout_context_for_ids(state, layout_ids)),
            prop_context=self._format_json(self._prop_context_for_ids(state, prop_ids)),
            reference_image_context=self._format_json(reference_image_context),
            clip_segments=self._format_json({episode_key: batch_clips}),
        )
        batch_output = await provider.generate_json(
            prompt,
            StoryboardPromptOutput,
            temperature=0.45,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "episode_key": episode_key,
                "expected_keys": [episode_key],
                "expected_clip_counts": batch_clip_count_by_episode,
                "total_clip_counts": total_clip_count_by_episode,
                "clip_batch_index": batch_index,
                "clip_batch_total": batch_total,
                "clip_batch_keys": batch_keys,
                "storyboard_panel_count": self.STORYBOARD_PANEL_COUNT,
                "storyboard_grid": self.storyboard_grid(),
                "storyboard_panel_aspect_ratio": self.storyboard_panel_aspect_ratio(),
            },
            refs=refs,
        )
        state.budget.used_text_calls += 1
        batch_output = self.validate_storyboard_prompt_output(
            batch_output,
            expected_episode_keys=[episode_key],
            expected_clip_segments={episode_key: batch_clips},
            require_complete=False,
        )
        return episode_key, batch_index, batch_output.storyboards[0].clips

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("storyboard", node_name=self.name)
        self.logger.info(
            "node=storyboard_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        target_episode_keys = self.target_episode_keys(state)
        all_episode_keys = self.expected_episode_keys(state)
        final_aspect_ratio = self.final_aspect_ratio()
        clip_segments_by_episode = self.clip_segments_by_episode(project_dir)
        expected_clip_segments = self._expected_clip_segments(clip_segments_by_episode, target_episode_keys)
        total_clip_count_by_episode = {
            episode_key: len(clips)
            for episode_key, clips in expected_clip_segments.items()
        }

        batch_specs: list[tuple[str, int, int, dict[str, object]]] = []
        for episode_key in target_episode_keys:
            source_clips = expected_clip_segments[episode_key]
            batches = self._clip_segment_batches(source_clips)
            for batch_index, batch_clips in enumerate(batches, start=1):
                batch_specs.append((episode_key, batch_index, len(batches), batch_clips))

        concurrency = self.batch_generation_concurrency(provider)
        self.logger.info(
            "node=storyboard_prompt batches=%d concurrency=%d",
            len(batch_specs),
            concurrency,
        )
        print(
            f"[autodrama] storyboard_prompt batches={len(batch_specs)} concurrency={concurrency}",
            flush=True,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(
            episode_key: str,
            batch_index: int,
            batch_total: int,
            batch_clips: dict[str, object],
        ) -> tuple[str, int, list[StoryboardPromptClip]]:
            async with semaphore:
                return await self._generate_storyboard_prompt_batch(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    episode_key=episode_key,
                    all_episode_keys=all_episode_keys,
                    final_aspect_ratio=final_aspect_ratio,
                    total_clip_count_by_episode=total_clip_count_by_episode,
                    batch_index=batch_index,
                    batch_total=batch_total,
                    batch_clips=batch_clips,
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

        missing_batch_specs: list[tuple[str, int, int, dict[str, object]]] = []
        retry_batch_index = 0
        for episode_key in target_episode_keys:
            source_clips = expected_clip_segments[episode_key]
            for source_key in self._sorted_clip_segment_keys(source_clips):
                try:
                    expected_index = int(str(source_key).strip())
                except ValueError:
                    expected_index = retry_batch_index + 1
                expected_clip_id = self.clip_id_for_episode_index(episode_key, expected_index)
                if expected_clip_id in generated_by_clip_id[episode_key]:
                    continue
                retry_batch_index += 1
                missing_batch_specs.append((episode_key, retry_batch_index, 0, {source_key: source_clips[source_key]}))

        if missing_batch_specs:
            retry_total = len(missing_batch_specs)
            missing_labels = [
                self.clip_id_for_episode_index(
                    episode_key,
                    int(str(next(iter(batch_clips))).strip()) if str(next(iter(batch_clips))).strip().isdigit() else index,
                )
                for index, (episode_key, _batch_index, _batch_total, batch_clips) in enumerate(
                    missing_batch_specs,
                    start=1,
                )
            ]
            self.logger.warning(
                "node=storyboard_prompt retrying missing clips as single-clip batches: %s",
                ", ".join(missing_labels),
            )
            print(
                "[autodrama] storyboard_prompt retry missing clips as single-clip batches: "
                + ", ".join(missing_labels),
                flush=True,
            )
            retry_specs = [
                (episode_key, batch_index, retry_total, batch_clips)
                for episode_key, batch_index, _batch_total, batch_clips in missing_batch_specs
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
        output = self.validate_storyboard_prompt_output(
            output,
            expected_episode_keys=target_episode_keys,
            expected_clip_segments=expected_clip_segments,
        )
        merged = self.merge_storyboard_prompt_outputs(
            project_dir=project_dir,
            generated_output=output,
            target_episode_keys=target_episode_keys,
            all_episode_keys=all_episode_keys,
        )
        self.repo.save_node_output(project_dir, self.name, merged)
        return state


class StoryboardGenerationNode(StoryboardAssetNodeBase):
    name = "storyboard_generation"
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
            panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
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
                "storyboard_generation_concurrency",
                "storyboard_sheet_generation_concurrency",
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
                "storyboard_generation_concurrency",
                "storyboard_sheet_generation_concurrency",
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

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("storyboard", node_name=STORYBOARD_IMAGE_PROVIDER_NODE_NAME)
        self.logger.info(
            "node=storyboard_generation provider=%s model=%s image_binding=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
        )
        self.workflow._hydrate_roles_from_design_files(project_dir, state)
        prompt_output = self.load_storyboard_prompt_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        target_set = set(target_episode_keys)
        target_storyboards = [
            episode
            for episode in prompt_output.storyboards
            if episode.episode_key in target_set
        ]
        missing = sorted(target_set.difference(episode.episode_key for episode in target_storyboards))
        if missing:
            raise ValueError(f"storyboard_generation missing storyboard_prompt episode(s): {', '.join(missing)}")

        existing_by_clip = self.load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 12) or 12))
        supports_refs = bool(max_refs and getattr(provider, "supports_reference_images", False))
        generated_by_clip = dict(existing_by_clip)

        pending: list[tuple[StoryboardPromptEpisode, StoryboardPromptClip]] = []
        skipped_existing = 0
        for episode in target_storyboards:
            for clip in episode.clips:
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
                        image_prompt = self.storyboard_image_prompt(episode.episode_key, clip)
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
                            "[autodrama] storyboard_generation skip existing "
                            f"source={reuse_source} clip={clip.clip_id} asset_id={asset_id} path={existing_item.asset_path}"
                        ),
                        flush=True,
                    )
                    self.logger.info("%s already exists, reused from %s", asset_id, existing_item.asset_path)
                    generated_by_clip[clip.clip_id] = existing_item
                    skipped_existing += 1
                    continue
                pending.append((episode, clip))

        concurrency = self.generation_concurrency(provider)
        print(
            (
                "[autodrama] storyboard_generation "
                f"concurrency={concurrency} pending={len(pending)} skipped_existing={skipped_existing}"
            ),
            flush=True,
        )
        self.logger.info(
            "node=storyboard_generation total_images=%d pending_images=%d concurrency=%d",
            sum(len(episode.clips) for episode in target_storyboards),
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
                self.logger.error("storyboard_generation failed clip=%s: %s", clip.clip_id, result)
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
            preview = "; ".join(f"{clip.clip_id}: {exc}" for clip, exc in failures[:5])
            if len(failures) > 5:
                preview = f"{preview}; ..."
            raise ProviderBadResponseError(
                "storyboard_generation failed for "
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
        image_prompt = self.storyboard_image_prompt(episode.episode_key, shot)
        result, image_prompt, _safety_rewrites = await self._generate_image_with_safety_prompt_rewrites(
            provider=provider,
            state=state,
            node_name=self.name,
            asset_id=asset_id,
            prompt=image_prompt,
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
            context={
                "asset_type": "storyboard",
                "episode_key": episode.episode_key,
                "clip_id": shot.clip_id,
                "duration_seconds": shot.duration_seconds,
                "panel_count": self.STORYBOARD_PANEL_COUNT,
                "grid": self.storyboard_grid(),
                "panel_aspect_ratio": self.storyboard_panel_aspect_ratio(),
            },
        )
        asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
        asset_url = self.first_image_url(result)
        item = StoryboardSheetGenerationItem(
            episode_key=episode.episode_key,
            clip_id=shot.clip_id,
            asset_id=asset_id,
            prompt=image_prompt,
            duration_seconds=shot.duration_seconds,
            panel_count=self.STORYBOARD_PANEL_COUNT,
            grid=self.storyboard_grid(),
            panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            asset_path=asset_path,
            asset_url=asset_url,
            provider=result.provider,
            model=result.model,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )
        self.logger.info("%s generated successfully, saved in %s", asset_id, asset_path)
        print(
            (
                "[autodrama] storyboard_generation generated "
                f"clip={shot.clip_id} asset_id={asset_id} path={asset_path}"
            ),
            flush=True,
        )
        return item


class StoryboardKeyframeGenerationNode(StoryboardAssetNodeBase):
    name = "storyboard_keyframe_generation"
    DEFAULT_CONCURRENCY = 4
    MAX_CONCURRENCY = 10

    @staticmethod
    def keyframe_asset_id(clip_id: str, frame_role: str) -> str:
        return f"{str(clip_id).strip()}_{str(frame_role).strip()}_frame"

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
                "storyboard_keyframe_generation_concurrency",
                "storyboard_keyframe_concurrency",
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
                "storyboard_keyframe_generation_concurrency",
                "storyboard_keyframe_concurrency",
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
            raise ValueError("storyboard_keyframe_generation requires workflow prompt store")
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

        def append(ref: AssetRef) -> bool:
            if len(refs) >= limit:
                return False
            refs.append(ref)
            return len(refs) < limit

        for ref in self._storyboard_ref(project_dir, storyboard_sheet, frame_role=frame_role, panel_ref=panel_ref):
            if not append(ref):
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
                "[autodrama] storyboard_keyframe_generation generated "
                f"clip={clip.clip_id} frame_role={frame_role} asset_id={asset_id} path={asset_path}"
            ),
            flush=True,
        )
        return item

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("storyboard", node_name=self.name)
        self.logger.info(
            "node=storyboard_keyframe_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prompt_output = self.load_storyboard_prompt_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        target_set = set(target_episode_keys)
        prompt_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        sheet_by_clip = {item.clip_id: item for item in sheet_output.generated_storyboards}
        existing_by_key = self._load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 1) or 1))
        supports_refs = bool(max_refs and getattr(provider, "supports_reference_images", False))
        generated_by_key = dict(existing_by_key)
        clip_selectors = self._active_clip_selectors()
        selected_clip_count = 0

        pending: list[tuple[str, StoryboardPromptClip, StoryboardSheetGenerationItem, str, str]] = []
        skipped_existing = 0
        for episode_key in target_episode_keys:
            episode = prompt_by_episode.get(episode_key)
            if episode is None:
                raise ValueError(f"storyboard_keyframe_generation missing storyboard_prompt episode: {episode_key}")
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
                    raise ValueError(f"storyboard_keyframe_generation missing storyboard_generation image for {clip.clip_id}")
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
                                "[autodrama] storyboard_keyframe_generation skip existing "
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
                "storyboard_keyframe_generation --clips matched no clips in selected episodes: "
                f"{', '.join(sorted(clip_selectors))}"
            )

        concurrency = self.generation_concurrency(provider)
        print(
            (
                "[autodrama] storyboard_keyframe_generation "
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
                    "storyboard_keyframe_generation failed clip=%s frame_role=%s: %s",
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
                "storyboard_keyframe_generation failed for "
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
                "clip_manifest_generation missing storyboard_keyframe_generation "
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
            source_node="storyboard_keyframe_generation",
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
            source_node="storyboard_keyframe_generation",
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
            source_node="storyboard_generation",
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
                    source_node="roleboard_generation",
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
                    source_node="roleboard_generation",
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
                    source_node="roleboard_generation",
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

    def _build_episode_manifest(
        self,
        *,
        project_dir: Path,
        state: ProjectState,
        storyboard: StoryboardPromptEpisode,
        storyboard_sheets: dict[str, StoryboardSheetGenerationItem],
        keyframes: dict[tuple[str, str, str], StoryboardKeyframeGenerationItem],
        existing_episode: StoryboardEpisodeOutput | None,
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
                raise ValueError(f"clip_manifest_generation missing storyboard_generation image for {clip_id}")
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
        prompt_output = self.load_storyboard_prompt_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        keyframe_output = self.load_storyboard_keyframe_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        storyboard_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        storyboard_sheets = {item.clip_id: item for item in sheet_output.generated_storyboards}
        keyframes = self._keyframes_by_clip_role(keyframe_output)
        generated: list[ClipManifestGenerationEpisodeItem] = []

        for episode_key in target_episode_keys:
            storyboard = storyboard_by_episode.get(episode_key)
            if storyboard is None:
                raise ValueError(f"clip_manifest_generation missing storyboard_prompt episode: {episode_key}")
            if not storyboard.clips:
                raise ValueError(f"clip_manifest_generation requires at least one clip for {episode_key}")
            existing = self._load_existing_episode(project_dir, episode_key)
            episode, warnings = self._build_episode_manifest(
                project_dir=project_dir,
                state=state,
                storyboard=storyboard,
                storyboard_sheets=storyboard_sheets,
                keyframes=keyframes,
                existing_episode=existing,
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
                "clip_manifest_generation wrote %s clips=%d warnings=%d",
                shot_path,
                len(episode.clips),
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
    "ClipManifestGenerationNode",
    "StoryboardGenerationNode",
    "StoryboardKeyframeGenerationNode",
    "StoryboardPromptNode",
    "build_storyboard_asset_node_runners",
    "build_storyboard_asset_nodes",
]
