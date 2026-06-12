from __future__ import annotations

import json
from math import gcd
from pathlib import Path
from typing import Any

from autodrama.core.schemas import (
    ProjectState,
    StoryboardBBox,
    StoryboardBBoxDetectionOutput,
    StoryboardBBoxEpisode,
    StoryboardPanelBBoxItem,
    StoryboardPanelCropItem,
    StoryboardPanelCropOutput,
    StoryboardPromptEpisode,
    StoryboardPromptOutput,
    StoryboardSheetGenerationItem,
    StoryboardSheetGenerationOutput,
)
from autodrama.providers.base import AssetRef
from autodrama.services.director_service import DirectorService
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase
from autodrama.workflows.runner import WorkflowNode

STORYBOARD_ASSET_NODE_NAMES = [
    "storyboard_prompt",
    "storyboard_generation",
    "storyboard_bbox_detection",
    "storyboard_panel_crop",
]
STORYBOARD_IMAGE_PROVIDER_NODE_NAME = "storyboard_sheet_generation"


class StoryboardAssetNodeBase(StaticAssetNodeBase):
    PANEL_COUNT = 12

    @staticmethod
    def _format_json(value: object) -> str:
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
            ("ref_frame_generation", "size"),
            ("storyboard_sheet_generation", "size"),
        ):
            node_settings = self.repo.settings.nodes.get(node_name)
            if node_settings is None:
                continue
            ratio = self._ratio_from_value(node_settings.params.get(param_name))
            if ratio:
                return ratio
        return "9:16"

    def storyboard_grid(self, aspect_ratio: str) -> str:
        ratio_number = self._ratio_number(aspect_ratio)
        return "3x4" if ratio_number is not None and ratio_number < 1 else "4x3"

    def storyboard_sheet_size(self, aspect_ratio: str) -> str:
        ratio_number = self._ratio_number(aspect_ratio)
        return "9:16" if ratio_number is not None and ratio_number < 1 else "16:9"

    def target_episode_keys(self, state: ProjectState) -> list[str]:
        return self.active_episode_keys(state) or self.expected_episode_keys(state)

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

    def load_storyboard_bbox_output(self, project_dir: Path) -> StoryboardBBoxDetectionOutput:
        path = self.layout.node_output_path(project_dir, "storyboard_bbox_detection")
        if not path.exists():
            raise FileNotFoundError(
                "storyboard_bbox_detection output is missing; run pregen through storyboard_bbox_detection first"
            )
        return StoryboardBBoxDetectionOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def validate_storyboard_prompt_output(
        self,
        output: StoryboardPromptOutput,
        *,
        expected_episode_keys: list[str],
        aspect_ratio: str,
        grid: str,
    ) -> StoryboardPromptOutput:
        expected = set(expected_episode_keys)
        seen: set[str] = set()
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
            episode.aspect_ratio = str(episode.aspect_ratio or aspect_ratio).strip() or aspect_ratio
            episode.grid = str(episode.grid or grid).strip() or grid
            episode.image_prompt = str(episode.image_prompt or "").strip()
            if not episode.image_prompt:
                raise ValueError(f"storyboard_prompt returned empty image_prompt for {episode.episode_key}")
            if len(episode.panels) != self.PANEL_COUNT:
                raise ValueError(
                    f"storyboard_prompt must return {self.PANEL_COUNT} panels for {episode.episode_key}; "
                    f"got {len(episode.panels)}"
                )
            for expected_index, panel in enumerate(episode.panels, start=1):
                if panel.index != expected_index:
                    raise ValueError(
                        f"storyboard_prompt panel index mismatch for {episode.episode_key}: "
                        f"expected {expected_index}, got {panel.index}"
                    )
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

    def roleboard_reference_refs(self, project_dir: Path, state: ProjectState, *, limit: int) -> list[AssetRef]:
        refs: list[AssetRef] = []
        key_vision = state.metadata.get("key_vision_asset")
        if isinstance(key_vision, dict):
            asset_path = key_vision.get("asset_path")
            asset_url = key_vision.get("asset_url")
        else:
            asset_path = state.metadata.get("key_vision_asset_path")
            asset_url = state.metadata.get("key_vision_asset_url")
        existing_key_vision = self.layout.existing_project_file(project_dir, str(asset_path)) if asset_path else None
        if existing_key_vision or asset_url:
            refs.append(
                AssetRef(
                    id="key_vision_original",
                    type="image",
                    path=str(project_dir / existing_key_vision) if existing_key_vision else None,
                    url=str(asset_url) if asset_url else None,
                    metadata={"asset_type": "key_vision", "reference_for": "storyboard"},
                )
            )

        for role in state.roles.values():
            for appearance in role.appearances.values():
                asset_path = appearance.asset_path or appearance.design_image_asset_path
                asset_url = appearance.asset_url or appearance.design_image_asset_url
                existing = self.layout.existing_project_file(project_dir, asset_path)
                if not existing and not asset_url:
                    continue
                refs.append(
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
                )
                if len(refs) >= limit:
                    return refs
        return refs[:limit]

    def storyboard_image_prompt(self, episode: StoryboardPromptEpisode) -> str:
        panel_lines = []
        for panel in episode.panels:
            panel_lines.append(
                (
                    f"{panel.index}. {panel.title}；景别：{panel.shot_size}；机位：{panel.camera_position}；"
                    f"构图：{panel.composition}；动作：{panel.action}；情绪：{panel.emotion}；"
                    f"镜头运动：{panel.camera_movement}；音效：{panel.sound_effects}"
                )
            )
        return "\n".join(
            [
                (
                    f"生成一张黑白线稿 12 宫格故事板，episode={episode.episode_key}，网格 {episode.grid}，"
                    f"每个宫格内部画幅比例必须是 {episode.aspect_ratio}。"
                ),
                "每格只做清晰分镜草图：线条干净、灰阶阴影少量、人物动势明确、构图方向准确、镜头顺序准确。",
                "每格左上角可放很小编号 1-12 和极短标题；不要出现对白气泡、字幕、水印、logo、文件名、项目名或大段文字。",
                "角色外观参考传入的角色身份板，保持脸型、发型、服装、配饰和体型稳定；但画面风格必须是黑白线稿故事板，不追求最终画质。",
                "原始故事板生成 prompt：",
                episode.image_prompt,
                "逐格分镜脚本：",
                "\n".join(panel_lines),
            ]
        )

    @staticmethod
    def bbox_is_valid(bbox: StoryboardBBox) -> bool:
        return bbox.x_min < bbox.x_max and bbox.y_min < bbox.y_max

    @staticmethod
    def bbox_contains(outer: StoryboardBBox, inner: StoryboardBBox) -> bool:
        return (
            outer.x_min <= inner.x_min
            and outer.y_min <= inner.y_min
            and outer.x_max >= inner.x_max
            and outer.y_max >= inner.y_max
        )

    def validate_storyboard_bbox_episode(
        self,
        episode: StoryboardBBoxEpisode,
        *,
        expected_episode_key: str,
    ) -> StoryboardBBoxEpisode:
        episode.episode_key = str(episode.episode_key or "").strip()
        if episode.episode_key != expected_episode_key:
            raise ValueError(
                f"storyboard_bbox_detection returned episode_key {episode.episode_key!r}; "
                f"expected {expected_episode_key!r}"
            )
        if int(episode.panel_count) != self.PANEL_COUNT:
            raise ValueError(
                f"storyboard_bbox_detection panel_count must be {self.PANEL_COUNT} for {episode.episode_key}; "
                f"got {episode.panel_count}"
            )
        if len(episode.panels) != self.PANEL_COUNT:
            raise ValueError(
                f"storyboard_bbox_detection must return {self.PANEL_COUNT} panels for {episode.episode_key}; "
                f"got {len(episode.panels)}"
            )
        expected_indexes = set(range(1, self.PANEL_COUNT + 1))
        actual_indexes = {int(panel.shot_index) for panel in episode.panels}
        if actual_indexes != expected_indexes:
            raise ValueError(
                f"storyboard_bbox_detection shot_index set mismatch for {episode.episode_key}: "
                f"expected 1-{self.PANEL_COUNT}, got {sorted(actual_indexes)}"
            )
        for panel in episode.panels:
            if not self.bbox_is_valid(panel.bbox_1000):
                raise ValueError(
                    f"storyboard_bbox_detection invalid bbox_1000 for {episode.episode_key} "
                    f"shot_index={panel.shot_index}: {panel.bbox_1000.model_dump(mode='json')}"
                )
            if panel.content_bbox_1000 is not None and not self.bbox_is_valid(panel.content_bbox_1000):
                raise ValueError(
                    f"storyboard_bbox_detection invalid content_bbox_1000 for {episode.episode_key} "
                    f"shot_index={panel.shot_index}: {panel.content_bbox_1000.model_dump(mode='json')}"
                )
            if panel.content_bbox_1000 is not None and not self.bbox_contains(
                panel.bbox_1000,
                panel.content_bbox_1000,
            ):
                raise ValueError(
                    f"storyboard_bbox_detection content_bbox_1000 must be inside bbox_1000 for "
                    f"{episode.episode_key} shot_index={panel.shot_index}"
                )
        episode.panels = sorted(episode.panels, key=lambda panel: int(panel.shot_index))
        return episode

    def merge_storyboard_bbox_outputs(
        self,
        *,
        project_dir: Path,
        generated_output: StoryboardBBoxDetectionOutput,
        all_episode_keys: list[str],
    ) -> StoryboardBBoxDetectionOutput:
        existing_path = self.layout.node_output_path(project_dir, "storyboard_bbox_detection")
        by_episode: dict[str, StoryboardBBoxEpisode] = {}
        if existing_path.exists():
            try:
                existing = StoryboardBBoxDetectionOutput.model_validate_json(existing_path.read_text(encoding="utf-8"))
                by_episode.update({episode.episode_key: episode for episode in existing.episodes})
            except Exception as exc:
                self.logger.warning(
                    "storyboard_bbox_detection ignored invalid existing output %s: %s",
                    existing_path,
                    exc,
                )
        by_episode.update({episode.episode_key: episode for episode in generated_output.episodes})
        return StoryboardBBoxDetectionOutput(
            episodes=[by_episode[episode_key] for episode_key in all_episode_keys if episode_key in by_episode]
        )

    @staticmethod
    def bbox_to_pixels(bbox: StoryboardBBox, *, width: int, height: int) -> tuple[int, int, int, int]:
        x_min = max(0, min(width - 1, round(width * bbox.x_min / 1000)))
        y_min = max(0, min(height - 1, round(height * bbox.y_min / 1000)))
        x_max = max(1, min(width, round(width * bbox.x_max / 1000)))
        y_max = max(1, min(height, round(height * bbox.y_max / 1000)))
        if x_max <= x_min:
            x_max = min(width, x_min + 1)
        if y_max <= y_min:
            y_max = min(height, y_min + 1)
        return x_min, y_min, x_max, y_max


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
        aspect_ratio = self.final_aspect_ratio()
        grid = self.storyboard_grid(aspect_ratio)
        prompt = self.workflow.prompts.render(
            "storyboard_prompt",
            title=state.title,
            episode_keys=", ".join(target_episode_keys),
            panel_count=self.PANEL_COUNT,
            aspect_ratio=aspect_ratio,
            grid=grid,
            raw_script=state.raw_script,
            novel_extract=self._format_json(self.episode_story_context(project_dir, state, target_episode_keys)),
            novel_full=self._format_json(self.novel_full_contents(project_dir, state, target_episode_keys)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=target_episode_keys),
            roleboard_context=self._format_json(self.roleboard_context(project_dir, state)),
        )
        output = await provider.generate_json(
            prompt,
            StoryboardPromptOutput,
            temperature=0.45,
            metadata={
                "node_name": self.name,
                "project_id": state.project_id,
                "expected_keys": target_episode_keys,
                "panel_count": self.PANEL_COUNT,
                "aspect_ratio": aspect_ratio,
                "grid": grid,
            },
        )
        output = self.validate_storyboard_prompt_output(
            output,
            expected_episode_keys=target_episode_keys,
            aspect_ratio=aspect_ratio,
            grid=grid,
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

    def load_existing_output(self, project_dir: Path) -> dict[str, StoryboardSheetGenerationItem]:
        path = self.layout.node_output_path(project_dir, self.name)
        if not path.exists():
            return {}
        try:
            output = StoryboardSheetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {item.episode_key: item for item in output.generated_storyboards}

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("ref_frame", node_name=STORYBOARD_IMAGE_PROVIDER_NODE_NAME)
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

        existing_by_episode = self.load_existing_output(project_dir)
        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        max_refs = max(0, int(getattr(provider, "max_reference_images", 12) or 12))
        refs = (
            self.roleboard_reference_refs(project_dir, state, limit=max_refs)
            if max_refs and getattr(provider, "supports_reference_images", False)
            else []
        )
        generated_by_episode = dict(existing_by_episode)

        for episode in target_storyboards:
            asset_id = f"{episode.episode_key}_storyboard_12up"
            output_path = self.layout.image_asset_path(project_dir, "storyboards", asset_id)
            existing_item = existing_by_episode.get(episode.episode_key)
            if (
                not force_pregen
                and existing_item is not None
                and self.layout.existing_project_file(project_dir, existing_item.asset_path)
            ):
                self.logger.info("%s already exists, reused from %s", asset_id, existing_item.asset_path)
                generated_by_episode[episode.episode_key] = existing_item
                continue

            image_prompt = self.storyboard_image_prompt(episode)
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
                    "asset_id": asset_id,
                    "asset_type": "storyboard",
                    "panel_count": self.PANEL_COUNT,
                    "aspect_ratio": episode.aspect_ratio,
                    "grid": episode.grid,
                    "size": self.storyboard_sheet_size(episode.aspect_ratio),
                    "provider_binding_node": STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
                },
                context={
                    "asset_type": "storyboard",
                    "episode_key": episode.episode_key,
                    "panel_count": self.PANEL_COUNT,
                    "aspect_ratio": episode.aspect_ratio,
                    "grid": episode.grid,
                },
            )
            asset_path = await self.media_store.write_first_generated_image(project_dir, output_path, result)
            asset_url = self.first_image_url(result)
            item = StoryboardSheetGenerationItem(
                episode_key=episode.episode_key,
                asset_id=asset_id,
                prompt=image_prompt,
                asset_path=asset_path,
                asset_url=asset_url,
                provider=result.provider,
                model=result.model,
                request_id=result.request_id,
                usage=result.usage,
                raw_response=result.raw_response,
            )
            generated_by_episode[episode.episode_key] = item
            self.logger.info("%s generated successfully, saved in %s", asset_id, asset_path)

        ordered_items = [
            generated_by_episode[episode_key]
            for episode_key in self.expected_episode_keys(state)
            if episode_key in generated_by_episode
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StoryboardSheetGenerationOutput(generated_storyboards=ordered_items),
        )
        return state


class StoryboardBBoxDetectionNode(StoryboardAssetNodeBase):
    name = "storyboard_bbox_detection"

    def storyboard_sheet_by_episode(self, project_dir: Path) -> dict[str, StoryboardSheetGenerationItem]:
        return {
            item.episode_key: item
            for item in self.load_storyboard_sheet_output(project_dir).generated_storyboards
        }

    def storyboard_prompt_by_episode(self, project_dir: Path) -> dict[str, StoryboardPromptEpisode]:
        return {
            episode.episode_key: episode
            for episode in self.load_storyboard_prompt_output(project_dir).storyboards
        }

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("storyboard", node_name=self.name)
        self.logger.info(
            "node=storyboard_bbox_detection provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        prompt_by_episode = self.storyboard_prompt_by_episode(project_dir)
        sheet_by_episode = self.storyboard_sheet_by_episode(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        generated_episodes: list[StoryboardBBoxEpisode] = []

        for episode_key in target_episode_keys:
            storyboard_script = prompt_by_episode.get(episode_key)
            if storyboard_script is None:
                raise ValueError(f"storyboard_bbox_detection missing storyboard_prompt episode: {episode_key}")
            sheet_item = sheet_by_episode.get(episode_key)
            if sheet_item is None:
                raise ValueError(f"storyboard_bbox_detection missing storyboard_generation sheet: {episode_key}")
            sheet_path = self.layout.existing_project_file(project_dir, sheet_item.asset_path)
            if not sheet_path and not sheet_item.asset_url:
                raise FileNotFoundError(
                    f"storyboard_bbox_detection cannot find storyboard image for {episode_key}: "
                    f"{sheet_item.asset_path or '-'}"
                )
            prompt = self.workflow.prompts.render(
                "storyboard_bbox_detection",
                title=state.title,
                episode_key=episode_key,
                panel_count=self.PANEL_COUNT,
                storyboard_script=self._format_json(storyboard_script.model_dump(mode="json")),
            )
            refs = [
                AssetRef(
                    id=sheet_item.asset_id,
                    type="image",
                    path=str(project_dir / sheet_path) if sheet_path else None,
                    url=sheet_item.asset_url,
                    metadata={
                        "asset_type": "storyboard_sheet",
                        "reference_source": "storyboard_generation",
                        "episode_key": episode_key,
                        "panel_count": self.PANEL_COUNT,
                    },
                )
            ]
            output = await provider.generate_json(
                prompt,
                StoryboardBBoxDetectionOutput,
                temperature=0.1,
                refs=refs,
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "episode_key": episode_key,
                    "expected_keys": [episode_key],
                    "panel_count": self.PANEL_COUNT,
                    "storyboard_asset_id": sheet_item.asset_id,
                    "storyboard_asset_path": sheet_item.asset_path,
                },
            )
            if len(output.episodes) != 1:
                raise ValueError(
                    f"storyboard_bbox_detection must return exactly one episode for {episode_key}; "
                    f"got {len(output.episodes)}"
                )
            generated_episodes.append(
                self.validate_storyboard_bbox_episode(
                    output.episodes[0],
                    expected_episode_key=episode_key,
                )
            )
            state.budget.used_text_calls += 1

        merged = self.merge_storyboard_bbox_outputs(
            project_dir=project_dir,
            generated_output=StoryboardBBoxDetectionOutput(episodes=generated_episodes),
            all_episode_keys=self.expected_episode_keys(state),
        )
        self.repo.save_node_output(project_dir, self.name, merged)
        return state


class StoryboardPanelCropNode(StoryboardAssetNodeBase):
    name = "storyboard_panel_crop"

    def existing_crop_output(self, project_dir: Path) -> dict[tuple[str, int], StoryboardPanelCropItem]:
        path = self.layout.node_output_path(project_dir, self.name)
        if not path.exists():
            return {}
        try:
            output = StoryboardPanelCropOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("storyboard_panel_crop ignored invalid existing output %s: %s", path, exc)
            return {}
        return {
            (item.episode_key, int(item.shot_index)): item
            for item in output.cropped_panels
        }

    @staticmethod
    def selected_crop_bbox(panel: StoryboardPanelBBoxItem) -> tuple[str, StoryboardBBox]:
        if panel.content_bbox_1000 is not None:
            return "content_bbox_1000", panel.content_bbox_1000
        return "bbox_1000", panel.bbox_1000

    def crop_panel(
        self,
        *,
        project_dir: Path,
        sheet_item: StoryboardSheetGenerationItem,
        panel: StoryboardPanelBBoxItem,
    ) -> StoryboardPanelCropItem:
        from PIL import Image

        source_path = self.layout.existing_project_file(project_dir, sheet_item.asset_path)
        if not source_path:
            raise FileNotFoundError(
                f"storyboard_panel_crop requires a local storyboard image for {sheet_item.episode_key}; "
                f"got {sheet_item.asset_path or '-'}"
            )
        absolute_source_path = project_dir / source_path
        bbox_source, bbox = self.selected_crop_bbox(panel)
        asset_id = f"{sheet_item.episode_key}_shot_{int(panel.shot_index):03d}_storyboard_panel"
        output_path = project_dir / "assets" / "images" / "storyboards" / "panels" / f"{asset_id}.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(absolute_source_path) as image:
            image = image.convert("RGB")
            width, height = image.size
            pixel_bbox = self.bbox_to_pixels(bbox, width=width, height=height)
            cropped = image.crop(pixel_bbox)
            if cropped.size[0] < 2 or cropped.size[1] < 2:
                raise ValueError(
                    f"storyboard_panel_crop produced too small crop for {sheet_item.episode_key} "
                    f"shot_index={panel.shot_index}: {cropped.size}"
                )
            cropped.save(output_path, format="PNG")

        return StoryboardPanelCropItem(
            episode_key=sheet_item.episode_key,
            shot_index=int(panel.shot_index),
            shot_id=panel.shot_id or f"{sheet_item.episode_key}_shot_{int(panel.shot_index):03d}",
            source_storyboard_asset_path=source_path,
            bbox_source=bbox_source,
            bbox_1000=bbox,
            asset_id=asset_id,
            asset_path=self.layout.project_relative(project_dir, output_path),
            warnings=[],
        )

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        bbox_output = self.load_storyboard_bbox_output(project_dir)
        sheet_output = self.load_storyboard_sheet_output(project_dir)
        target_episode_keys = self.target_episode_keys(state)
        target_set = set(target_episode_keys)
        bbox_by_episode = {episode.episode_key: episode for episode in bbox_output.episodes}
        sheet_by_episode = {item.episode_key: item for item in sheet_output.generated_storyboards}
        existing_by_key = self.existing_crop_output(project_dir)
        cropped_by_key = dict(existing_by_key)

        for episode_key in target_episode_keys:
            bbox_episode = bbox_by_episode.get(episode_key)
            if bbox_episode is None:
                raise ValueError(f"storyboard_panel_crop missing bbox episode: {episode_key}")
            sheet_item = sheet_by_episode.get(episode_key)
            if sheet_item is None:
                raise ValueError(f"storyboard_panel_crop missing storyboard image episode: {episode_key}")
            bbox_episode = self.validate_storyboard_bbox_episode(
                bbox_episode,
                expected_episode_key=episode_key,
            )
            for panel in bbox_episode.panels:
                cropped = self.crop_panel(
                    project_dir=project_dir,
                    sheet_item=sheet_item,
                    panel=panel,
                )
                cropped_by_key[(cropped.episode_key, int(cropped.shot_index))] = cropped
                self.logger.info(
                    "%s shot %03d storyboard panel cropped from %s",
                    cropped.episode_key,
                    cropped.shot_index,
                    cropped.bbox_source,
                )

        ordered_items = [
            cropped_by_key[(episode_key, shot_index)]
            for episode_key in self.expected_episode_keys(state)
            for shot_index in range(1, self.PANEL_COUNT + 1)
            if (episode_key, shot_index) in cropped_by_key
            and (episode_key in target_set or (episode_key, shot_index) in existing_by_key)
        ]
        self.repo.save_node_output(
            project_dir,
            self.name,
            StoryboardPanelCropOutput(cropped_panels=ordered_items),
        )
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
        StoryboardBBoxDetectionNode.name: StoryboardBBoxDetectionNode(**deps),
        StoryboardPromptNode.name: StoryboardPromptNode(**deps),
        StoryboardGenerationNode.name: StoryboardGenerationNode(**deps),
        StoryboardPanelCropNode.name: StoryboardPanelCropNode(**deps),
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
    "StoryboardBBoxDetectionNode",
    "StoryboardGenerationNode",
    "StoryboardPanelCropNode",
    "StoryboardPromptNode",
    "build_storyboard_asset_node_runners",
    "build_storyboard_asset_nodes",
]
