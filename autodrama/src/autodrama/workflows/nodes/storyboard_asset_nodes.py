from __future__ import annotations

import asyncio
import json
from math import gcd
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    ClipSegmentNodeOutput,
    ProjectState,
    ShotManifestGenerationEpisodeItem,
    ShotManifestGenerationOutput,
    ShotVideoInput,
    StoryboardEpisodeOutput,
    StoryboardPromptEpisode,
    StoryboardPromptOutput,
    StoryboardPromptShot,
    StoryboardShot,
    StoryboardSheetGenerationItem,
    StoryboardSheetGenerationOutput,
)
from autodrama.providers.base import AssetRef
from autodrama.services.director_service import DirectorService
from autodrama.utils.video_prompts import sanitize_video_prompt_text
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase
from autodrama.workflows.runner import WorkflowNode

STORYBOARD_ASSET_NODE_NAMES = [
    "storyboard_prompt",
    "storyboard_generation",
    "shot_manifest_generation",
]
STORYBOARD_IMAGE_PROVIDER_NODE_NAME = "storyboard_sheet_generation"


class StoryboardAssetNodeBase(StaticAssetNodeBase):
    STORYBOARD_PANEL_COUNT = 12
    STORYBOARD_GRID = "4x3"
    STORYBOARD_PANEL_ASPECT_RATIO = "16:9"
    TARGET_SHOT_SECONDS = 15

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
            ("shot_video_generation", "ratio"),
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
        return "16:9"

    def target_episode_keys(self, state: ProjectState) -> list[str]:
        return self.active_episode_keys(state) or self.expected_episode_keys(state)

    def target_shot_count(self, state: ProjectState) -> int:
        duration = self.script_service.episode_duration_seconds(state)
        return max(1, int((duration + self.TARGET_SHOT_SECONDS - 1) // self.TARGET_SHOT_SECONDS))

    @staticmethod
    def shot_count_for_episode(episode: StoryboardPromptEpisode) -> int:
        return len(episode.shots)

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

    @staticmethod
    def storyboard_asset_id(shot_id: str) -> str:
        return f"{str(shot_id).strip()}_storyboard"

    @staticmethod
    def shot_id_for_episode_index(episode_key: str, index: int) -> str:
        return f"{episode_key}_shot_{index:03d}"

    @staticmethod
    def episode_key_from_shot_id(shot_id: str) -> str:
        text = str(shot_id or "").strip()
        if "_shot_" not in text:
            return ""
        return text.rsplit("_shot_", 1)[0]

    def validate_storyboard_prompt_output(
        self,
        output: StoryboardPromptOutput,
        *,
        expected_episode_keys: list[str],
        shot_count: int,
    ) -> StoryboardPromptOutput:
        expected = set(expected_episode_keys)
        seen: set[str] = set()
        seen_shots: set[str] = set()
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
            if len(episode.shots) != shot_count:
                raise ValueError(
                    f"storyboard_prompt must return {shot_count} shots for {episode.episode_key}; "
                    f"got {len(episode.shots)}"
                )
            validated_shots: list[StoryboardPromptShot] = []
            for expected_index, shot in enumerate(episode.shots, start=1):
                expected_shot_id = self.shot_id_for_episode_index(episode.episode_key, expected_index)
                shot.shot_id = str(shot.shot_id or "").strip()
                if shot.shot_id != expected_shot_id:
                    raise ValueError(
                        f"storyboard_prompt shot_id mismatch for {episode.episode_key} index={expected_index}: "
                        f"expected {expected_shot_id}, got {shot.shot_id or '-'}"
                    )
                if shot.shot_id in seen_shots:
                    raise ValueError(f"storyboard_prompt returned duplicate shot_id: {shot.shot_id}")
                seen_shots.add(shot.shot_id)
                try:
                    shot.duration_seconds = float(shot.duration_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"storyboard_prompt invalid duration_seconds for {shot.shot_id}") from exc
                if not 12 <= shot.duration_seconds <= 15:
                    raise ValueError(
                        f"storyboard_prompt duration_seconds for {shot.shot_id} must be 12-15; "
                        f"got {shot.duration_seconds}"
                    )
                shot.role_ids = self._dedupe_nonempty_texts(shot.role_ids)
                shot.layout_ids = self._dedupe_nonempty_texts(shot.layout_ids)
                shot.prop_ids = self._dedupe_nonempty_texts(shot.prop_ids)
                if not shot.layout_ids:
                    raise ValueError(f"storyboard_prompt layout_ids is empty for {shot.shot_id}")
                shot.video_prompt = sanitize_video_prompt_text(str(shot.video_prompt or "").strip())
                if not shot.video_prompt:
                    raise ValueError(f"storyboard_prompt returned empty video_prompt for {shot.shot_id}")
                validated_shots.append(shot)
            episode.shots = validated_shots
            cleaned.append(episode)
        missing = [episode_key for episode_key in expected_episode_keys if episode_key not in seen]
        if missing:
            raise ValueError(f"storyboard_prompt missing episode(s): {', '.join(missing)}")
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
        shot: StoryboardPromptShot,
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

    def storyboard_image_prompt(self, episode_key: str, shot: StoryboardPromptShot) -> str:
        return "\n".join(
            [
                (
                    f"为 {episode_key} 的 {shot.shot_id} 生成一张完整分镜故事板图。"
                    f"这个 video shot 时长 {shot.duration_seconds:g} 秒，一张故事板覆盖整个 12-15 秒 video shot。"
                ),
                "固定要求：16:9 故事板表格，严格 4 列 x 3 行 = 12 个电影风格面板。每个宫格对应约 1-1.2 秒画面，它不是一个 shot；12 个宫格合起来覆盖当前 12-15 秒 video shot 的连续动作。",
                "实际故事板绘图必须仅为黑白：粗糙的铅笔线条、最小细节、快速手势绘图能量、简单的解剖结构构建、强烈的轮廓可读性。保持艺术作品轻量、动态且未完成，像早期影视预演分镜，不要画成最终彩色成片。",
                "请将下方 video_prompt 拆解成 12 个连续推进的关键画面。每个面板都必须清楚体现画面内容、人物动作、镜头关系和情绪节奏；画面之间必须有明确叙事推进感，而不是彼此孤立的静态图片。",
                "每个面板必须包含可见的动作、状态变化或镜头推进。避免重复、呆板或静态站立构图；角色动作、表情、姿态和场景变化必须服务剧情发展，强化连续性、节奏感和视觉张力。",
                "使用电影感摄影方式，包含但不限于：手持感、快速平移、环绕运动、俯拍、仰拍、侧面轮廓、侵略性特写、长焦压缩、极端负空间。镜头语言要服务剧情，不要平均分配，要根据情绪和叙事重点变化。",
                "环境保持简洁，只保留对剧情有帮助的关键场景元素。避免无关杂乱背景，重点突出人物、动作、空间关系、光线方向和氛围。",
                "标注颜色系统：红色箭头=身体运动，蓝色箭头=摄影机运动，绿色标记=取景/构图笔记，橙色标记=灯光方向，紫色标记=情绪/声音/叙事强调，黑色文本=简短镜头笔记和面板标签。标注必须少量、清晰、服务制作，不要遮挡主体。",
                "宫格之间有清晰分隔和留白，不得重叠、裁脸或压住人物肢体。允许很小的面板编号 01-12 和极短制作注释；不要生成字幕、对白气泡、水印、logo、文件名、项目名、资产 ID、二维码或大段可读文字。",
                "输入参考图优先级：角色外观以传入的人物身份板/角色板为准；场景空间以传入的场景图/场景三视图为准；如有道具参考图，道具以传入的道具设计图为准。禁止使用主视觉图/key_vision 作为参考或构图依据。",
                "storyboard_generation 的输入由当前 storyboard_prompt 的 shot.video_prompt、对应角色身份板图、对应场景图和本固定 12 宫格故事板模板组成；不要参考主视觉图，不要新增剧情事实、角色、场景或道具。",
                "当前 shot 视频提示词：",
                shot.video_prompt,
            ]
        )


class StoryboardPromptNode(StoryboardAssetNodeBase):
    name = "storyboard_prompt"

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
        shot_count = self.target_shot_count(state)
        prompt = self.workflow.prompts.render(
            "storyboard_prompt",
            title=state.title,
            episode_keys=", ".join(target_episode_keys),
            shot_count=shot_count,
            final_aspect_ratio=final_aspect_ratio,
            storyboard_panel_count=self.STORYBOARD_PANEL_COUNT,
            storyboard_grid=self.storyboard_grid(),
            storyboard_panel_aspect_ratio=self.storyboard_panel_aspect_ratio(),
            raw_script=state.raw_script,
            novel_extract=self._format_json(self.episode_story_context(project_dir, state, target_episode_keys)),
            novel_full=self._format_json(self.novel_full_contents(project_dir, state, target_episode_keys)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=target_episode_keys),
            roleboard_context=self._format_json(self.roleboard_context(project_dir, state)),
            layout_context=self._format_json(self.layout_context(state)),
            prop_context=self._format_json(self.prop_context(state)),
            clip_segments=self._format_json(self.clip_segments_by_episode(project_dir)),
        )
        output = await provider.generate_json(
            prompt,
            StoryboardPromptOutput,
            temperature=0.45,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "expected_keys": target_episode_keys,
                "shot_count": shot_count,
                "storyboard_panel_count": self.STORYBOARD_PANEL_COUNT,
                "storyboard_grid": self.storyboard_grid(),
                "storyboard_panel_aspect_ratio": self.storyboard_panel_aspect_ratio(),
            },
        )
        output = self.validate_storyboard_prompt_output(
            output,
            expected_episode_keys=target_episode_keys,
            shot_count=shot_count,
        )
        merged = self.merge_storyboard_prompt_outputs(
            project_dir=project_dir,
            generated_output=output,
            target_episode_keys=target_episode_keys,
            all_episode_keys=all_episode_keys,
        )
        state.budget.used_text_calls += 1
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
        return {item.shot_id: item for item in output.generated_storyboards if item.shot_id}

    def _resume_storyboard_sheet_from_existing_file(
        self,
        *,
        project_dir: Path,
        provider: object,
        episode: StoryboardPromptEpisode,
        shot: StoryboardPromptShot,
        asset_id: str,
        existing_path: str | None,
        image_prompt: str,
    ) -> StoryboardSheetGenerationItem | None:
        existing_rel = self.layout.existing_project_file(project_dir, existing_path)
        if not existing_rel:
            return None
        return StoryboardSheetGenerationItem(
            episode_key=episode.episode_key,
            shot_id=shot.shot_id,
            asset_id=asset_id,
            prompt=image_prompt,
            duration_seconds=shot.duration_seconds,
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

        existing_by_shot = self.load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 12) or 12))
        supports_refs = bool(max_refs and getattr(provider, "supports_reference_images", False))
        generated_by_shot = dict(existing_by_shot)

        pending: list[tuple[StoryboardPromptEpisode, StoryboardPromptShot]] = []
        skipped_existing = 0
        for episode in target_storyboards:
            for shot in episode.shots:
                asset_id = self.storyboard_asset_id(shot.shot_id)
                existing_item = existing_by_shot.get(shot.shot_id)
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
                        image_prompt = self.storyboard_image_prompt(episode.episode_key, shot)
                        existing_item = self._resume_storyboard_sheet_from_existing_file(
                            project_dir=project_dir,
                            provider=provider,
                            episode=episode,
                            shot=shot,
                            asset_id=asset_id,
                            existing_path=existing_file_path,
                            image_prompt=image_prompt,
                        )
                    if existing_item is None:
                        pending.append((episode, shot))
                        continue
                    print(
                        (
                            "[autodrama] storyboard_generation skip existing "
                            f"source={reuse_source} shot={shot.shot_id} asset_id={asset_id} path={existing_item.asset_path}"
                        ),
                        flush=True,
                    )
                    self.logger.info("%s already exists, reused from %s", asset_id, existing_item.asset_path)
                    generated_by_shot[shot.shot_id] = existing_item
                    skipped_existing += 1
                    continue
                pending.append((episode, shot))

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
            sum(len(episode.shots) for episode in target_storyboards),
            len(pending),
            concurrency,
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def generate_one(episode: StoryboardPromptEpisode, shot: StoryboardPromptShot) -> StoryboardSheetGenerationItem:
            async with semaphore:
                return await self.generate_storyboard_sheet(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    episode=episode,
                    shot=shot,
                    max_refs=max_refs,
                    supports_refs=supports_refs,
                )

        tasks = [asyncio.create_task(generate_one(episode, shot)) for episode, shot in pending]
        try:
            generated_items = list(await asyncio.gather(*tasks)) if tasks else []
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for item in generated_items:
            generated_by_shot[item.shot_id] = item

        prompt_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        ordered_items = [
            generated_by_shot[shot.shot_id]
            for episode_key in self.expected_episode_keys(state)
            for shot in prompt_by_episode.get(episode_key, StoryboardPromptEpisode(episode_key=episode_key, shots=[])).shots
            if shot.shot_id in generated_by_shot
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StoryboardSheetGenerationOutput(generated_storyboards=ordered_items),
        )
        return state

    async def generate_storyboard_sheet(
        self,
        *,
        provider: object,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardPromptEpisode,
        shot: StoryboardPromptShot,
        max_refs: int,
        supports_refs: bool,
    ) -> StoryboardSheetGenerationItem:
        asset_id = self.storyboard_asset_id(shot.shot_id)
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
                "shot_id": shot.shot_id,
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
                "shot_id": shot.shot_id,
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
            shot_id=shot.shot_id,
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
                f"shot={shot.shot_id} asset_id={asset_id} path={asset_path}"
            ),
            flush=True,
        )
        return item


class ShotManifestGenerationNode(StoryboardAssetNodeBase):
    name = "shot_manifest_generation"

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

    def _layout_id_for_shot(
        self,
        *,
        state: ProjectState,
        shot_prompt: StoryboardPromptShot,
        lookup: dict[str, str],
        text: str,
        episode_key: str,
    ) -> str:
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
    def _prompt_shot_text(cls, shot_prompt: StoryboardPromptShot) -> str:
        parts = [
            shot_prompt.shot_id,
            " ".join(shot_prompt.role_ids),
            " ".join(shot_prompt.layout_ids),
            " ".join(shot_prompt.prop_ids),
            shot_prompt.video_prompt,
        ]
        return " ".join(cls._clean_text(part) for part in parts if cls._clean_text(part))

    def _video_prompt_for_shot(self, shot_prompt: StoryboardPromptShot) -> tuple[str, list[str]]:
        original_prompt = self._clean_text(shot_prompt.video_prompt)
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

    def _shot_video_model_params(self, provider: object) -> dict[str, Any]:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        if isinstance(params, dict) and params:
            return dict(params)
        node_settings = self.repo.settings.nodes.get("shot_video_generation")
        if node_settings is None:
            return {}
        return dict(node_settings.params)

    def _shot_video_template_candidates(self, provider: object) -> list[str]:
        params = self._shot_video_model_params(provider)
        configured = str(params.get("prompt_template") or params.get("shot_video_prompt_template") or "").strip()
        if configured:
            return [configured.removesuffix(".md")]
        provider_name = slugify(str(getattr(provider, "name", "") or ""), fallback="provider").lower()
        model_name = slugify(str(getattr(provider, "model", "") or ""), fallback="model").lower()
        return [
            f"shot_video/{provider_name}_{model_name}",
            f"shot_video/{provider_name}",
            "shot_video/default",
        ]

    def _render_shot_video_prompt_template(
        self,
        *,
        provider: object,
        episode_key: str,
        shot_id: str,
        duration_seconds: float,
        video_prompt: str,
        shot_video_inputs: list[ShotVideoInput],
    ) -> tuple[str, str]:
        prompts = getattr(self.workflow, "prompts", None)
        if prompts is None:
            raise ValueError("shot_manifest_generation requires workflow prompt store to render final_video_prompt")
        params = self._shot_video_model_params(provider)
        negative_rules = params.get("negative_rules") or []
        if isinstance(negative_rules, str):
            negative_rules = [negative_rules]
        if not isinstance(negative_rules, list):
            negative_rules = []
        clean_negative_rules = [self._clean_text(rule) for rule in negative_rules if self._clean_text(rule)]
        if not clean_negative_rules:
            binding = getattr(provider, "model_binding", None)
            model_id = getattr(binding, "model_id", None)
            node_settings = self.repo.settings.nodes.get("shot_video_generation")
            if not model_id and node_settings is not None:
                model_id = node_settings.model
            model_label = str(model_id or getattr(provider, "model", "unknown"))
            raise ValueError(
                "shot_manifest_generation requires model-bound "
                f"nodes.shot_video_generation.params.negative_rules for {model_label}; "
                "put provider/model-specific negative rules under the same shot_video_generation node config."
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
            for item in shot_video_inputs
        ]
        slots_by_type: dict[str, list[str]] = {}
        for item in shot_video_inputs:
            slots_by_type.setdefault(str(item.asset_type), []).append(item.slot)

        last_error: Exception | None = None
        for template_name in self._shot_video_template_candidates(provider):
            try:
                return (
                    prompts.render(
                        template_name,
                        episode_key=episode_key,
                        shot_id=shot_id,
                        duration_seconds=duration_seconds,
                        video_prompt=video_prompt,
                        shot_video_inputs_json=json.dumps(input_rows, ensure_ascii=False, indent=2),
                        storyboard_input_slot=", ".join(slots_by_type.get("storyboard", [])) or "无",
                        roleboard_input_slots=", ".join(slots_by_type.get("roleboard", [])) or "无",
                        layout_input_slots=", ".join(slots_by_type.get("layout", [])) or "无",
                        prop_input_slots=", ".join(slots_by_type.get("prop", [])) or "无",
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
    def _append_shot_video_input(
        inputs: list[ShotVideoInput],
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
            ShotVideoInput(
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

    def _shot_video_inputs_for_shot(
        self,
        *,
        state: ProjectState,
        storyboard_sheet: StoryboardSheetGenerationItem,
        role_ids: list[str],
        role_appearance_ids: list[str],
        layout_ids: list[str],
        prop_ids: list[str],
    ) -> tuple[list[ShotVideoInput], list[str]]:
        inputs: list[ShotVideoInput] = []
        warnings: list[str] = []
        self._append_shot_video_input(
            inputs,
            asset_type="storyboard",
            asset_id=storyboard_sheet.asset_id,
            asset_path=storyboard_sheet.asset_path,
            asset_url=storyboard_sheet.asset_url,
            source_node="storyboard_generation",
            label="当前 shot 的 12 宫格故事板整图",
            required=True,
            shot_id=storyboard_sheet.shot_id,
        )

        explicit_appearance_ids = {str(value).strip() for value in role_appearance_ids if str(value).strip()}
        for role_id in role_ids:
            role = state.roles.get(role_id)
            if role is None:
                warnings.append(f"missing role for shot video input: {role_id}")
                self._append_shot_video_input(
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
                self._append_shot_video_input(
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
                self._append_shot_video_input(
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
                self._append_shot_video_input(
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
            self._append_shot_video_input(
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
                self._append_shot_video_input(
                    inputs,
                    asset_type="prop",
                    asset_id=None,
                    asset_path=None,
                    asset_url=None,
                    source_node="prop_generation",
                    label=f"missing prop {prop_id}",
                    prop_id=prop_id,
                    required=False,
                )
                continue
            if not (prop.asset_path or prop.asset_url):
                warnings.append(f"{prop_id}: prop image is missing")
            self._append_shot_video_input(
                inputs,
                asset_type="prop",
                asset_id=prop.asset_id or prop.id,
                asset_path=prop.asset_path,
                asset_url=prop.asset_url,
                source_node="prop_generation",
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
        existing_episode: StoryboardEpisodeOutput | None,
    ) -> tuple[StoryboardEpisodeOutput, list[str]]:
        role_lookup = self._role_lookup(state)
        prop_lookup = self._prop_lookup(state)
        layout_lookup = self._layout_lookup(state)
        existing_by_id = {shot.shot_id: shot for shot in (existing_episode.shots if existing_episode else [])}
        existing_by_index = {shot.index: shot for shot in (existing_episode.shots if existing_episode else [])}
        episode_warnings: list[str] = []
        shots: list[StoryboardShot] = []
        video_provider = self.router.video("shot", node_name="shot_video_generation")

        for shot_index, shot_prompt in enumerate(storyboard.shots, start=1):
            shot_id = self._clean_text(shot_prompt.shot_id)
            prompt_text = self._prompt_shot_text(shot_prompt)
            role_ids = self._resolve_ids(
                explicit_ids=list(shot_prompt.role_ids or []),
                names=[],
                lookup=role_lookup,
                text=prompt_text,
                fallback_prefix="role",
            )
            prop_ids = self._resolve_ids(
                explicit_ids=list(shot_prompt.prop_ids or []),
                names=[],
                lookup=prop_lookup,
                text=prompt_text,
                fallback_prefix="prop",
            )
            layout_ids = self._resolve_ids(
                explicit_ids=list(shot_prompt.layout_ids or []),
                names=[],
                lookup=layout_lookup,
                text=prompt_text,
                fallback_prefix="layout",
            )
            if not layout_ids:
                layout_ids = [
                    self._layout_id_for_shot(
                        state=state,
                        shot_prompt=shot_prompt,
                        lookup=layout_lookup,
                        text=prompt_text,
                        episode_key=storyboard.episode_key,
                    )
                ]
            layout_id = layout_ids[0] if layout_ids else normalize_id("layout", storyboard.episode_key)
            role_appearance_ids = self._first_appearance_ids(state, role_ids)
            role_audio_ids: list[str] = []
            dialogue: list[str] = []
            video_prompt, prompt_warnings = self._video_prompt_for_shot(shot_prompt)
            episode_warnings.extend(f"{shot_id}: {warning}" for warning in prompt_warnings)
            storyboard_sheet = storyboard_sheets.get(shot_id)
            if storyboard_sheet is None:
                raise ValueError(f"shot_manifest_generation missing storyboard_generation image for {shot_id}")
            shot_video_inputs, input_warnings = self._shot_video_inputs_for_shot(
                state=state,
                storyboard_sheet=storyboard_sheet,
                role_ids=role_ids,
                role_appearance_ids=role_appearance_ids,
                layout_ids=layout_ids,
                prop_ids=prop_ids,
            )
            episode_warnings.extend(f"{shot_id}: {warning}" for warning in input_warnings)
            final_video_prompt, prompt_template = self._render_shot_video_prompt_template(
                provider=video_provider,
                episode_key=storyboard.episode_key,
                shot_id=shot_id,
                duration_seconds=float(shot_prompt.duration_seconds),
                video_prompt=video_prompt,
                shot_video_inputs=shot_video_inputs,
            )
            for item in shot_video_inputs:
                item.metadata.setdefault("final_video_prompt_template", prompt_template)
                item.metadata.setdefault("video_model", getattr(video_provider, "model", "-"))
            shot = StoryboardShot(
                shot_id=shot_id,
                index=shot_index,
                layout_id=layout_id,
                layout_ids=layout_ids,
                title=f"镜头{shot_index:03d}",
                content=video_prompt,
                duration_seconds=float(shot_prompt.duration_seconds),
                transition="硬切",
                start_frame_source="new_reference_frame",
                start_frame_inheritance_reason="本片段按当前 shot 的 12 宫格故事板整图重新建立画面。",
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
                shot_video_inputs=shot_video_inputs,
            )
            existing = existing_by_id.get(shot.shot_id) or existing_by_index.get(shot.index)
            self._preserve_dynamic_fields(shot, existing)
            shots.append(shot)

        return StoryboardEpisodeOutput(episode_key=storyboard.episode_key, shots=shots), episode_warnings

    def _load_existing_episode(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput | None:
        path = self.layout.shot_path(project_dir, episode_key)
        if not path.exists():
            return None
        try:
            return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("shot_manifest_generation ignored invalid existing shot file %s: %s", path, exc)
            return None

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        prompt_output = self.load_storyboard_prompt_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        storyboard_by_episode = {episode.episode_key: episode for episode in prompt_output.storyboards}
        storyboard_sheets = {item.shot_id: item for item in sheet_output.generated_storyboards}
        generated: list[ShotManifestGenerationEpisodeItem] = []

        for episode_key in target_episode_keys:
            storyboard = storyboard_by_episode.get(episode_key)
            if storyboard is None:
                raise ValueError(f"shot_manifest_generation missing storyboard_prompt episode: {episode_key}")
            if not storyboard.shots:
                raise ValueError(f"shot_manifest_generation requires at least one shot for {episode_key}")
            existing = self._load_existing_episode(project_dir, episode_key)
            episode, warnings = self._build_episode_manifest(
                project_dir=project_dir,
                state=state,
                storyboard=storyboard,
                storyboard_sheets=storyboard_sheets,
                existing_episode=existing,
            )
            self.workflow._save_storyboard_episode(project_dir, episode)
            shot_path = self.layout.project_relative(project_dir, self.layout.shot_path(project_dir, episode_key))
            generated.append(
                ShotManifestGenerationEpisodeItem(
                    episode_key=episode_key,
                    shot_count=len(episode.shots),
                    shot_path=shot_path,
                    warnings=warnings,
                )
            )
            self.logger.info(
                "shot_manifest_generation wrote %s shots=%d warnings=%d",
                shot_path,
                len(episode.shots),
                len(warnings),
            )

        existing_output_path = self.layout.node_output_path(project_dir, self.name)
        existing_items: dict[str, ShotManifestGenerationEpisodeItem] = {}
        if existing_output_path.exists():
            try:
                existing_output = ShotManifestGenerationOutput.model_validate_json(
                    existing_output_path.read_text(encoding="utf-8")
                )
                existing_items.update({item.episode_key: item for item in existing_output.episodes})
            except Exception as exc:
                self.logger.warning("shot_manifest_generation ignored invalid existing output: %s", exc)
        existing_items.update({item.episode_key: item for item in generated})
        ordered = [
            existing_items[episode_key]
            for episode_key in self.expected_episode_keys(state)
            if episode_key in existing_items
        ]
        self.repo.save_node_output(project_dir, self.name, ShotManifestGenerationOutput(episodes=ordered))
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
        ShotManifestGenerationNode.name: ShotManifestGenerationNode(**deps),
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
    "ShotManifestGenerationNode",
    "StoryboardGenerationNode",
    "StoryboardPromptNode",
    "build_storyboard_asset_node_runners",
    "build_storyboard_asset_nodes",
]
