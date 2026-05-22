from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from autodrama.core.schemas import (
    DynamicAssetSolidificationOutput,
    ProjectState,
    RefFrameGenerationOutput,
    ShotBGMGenerationOutput,
    ShotVideoGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardGenerationOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.workflows.generation_checklist import (
    selected_episode_keys_from_checklist,
    update_checklist_from_state,
)
from autodrama.workflows.dynamic_assets import DynamicAssetNodeMixin
from autodrama.workflows.pregen import PregenWorkflow
from autodrama.workflows.storyboard_history import update_storyboard_history_from_episode

GENERATION_NODES = [
    "storyboard_generation",
    "shot_bgm_generation",
    "ref_frame_generation",
    "shot_video_generation",
    "dynamic_asset_solidification",
]

DEFAULT_GENERATION_NODES = [
    "storyboard_generation",
    "shot_bgm_generation",
    "ref_frame_generation",
    "shot_video_generation",
    "dynamic_asset_solidification",
]


class GenerationWorkflow(DynamicAssetNodeMixin, PregenWorkflow):
    @staticmethod
    def _visual_style_prompt(state: ProjectState) -> str:
        prompt = str(state.metadata.get("visual_style_prompt", "")).strip()
        if not prompt:
            return ""
        return f"画面风格要求: {prompt}"

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
            self._visual_style_prompt(state),
            f"剧集: {episode.episode_key}",
            "参考帧生成要求:",
            shot.ref_frame_prompt,
        ]
        if layout:
            parts.append(f"场景设定: {layout.name} - {layout.desc}")
        if role_lines:
            parts.append(
                "角色资产参考: "
                "仅当主体 prompt 明确该角色在这一帧可见时绘制；否则只作为本片段后续一致性锚点，不要擅自加入画面。参考列表: "
                + "；".join(role_lines)
            )
        if appearance_lines:
            parts.append("人物一致性要求: " + "；".join(appearance_lines))
        if prop_lines:
            parts.append(
                "道具资产参考: "
                "仅当主体 prompt 明确该道具在这一帧可见时绘制；否则只作为本片段后续一致性锚点，不要擅自加入画面。参考列表: "
                + "；".join(prop_lines)
            )
        if shot.dialogue:
            parts.append("画面对白气氛: " + " / ".join(shot.dialogue))
        parts.append("生成单帧剧照，必须是同一个视频片段里的关键帧；不要添加字幕、水印、文字标识或片段编号。")
        return "\n".join(item for item in parts if item)

    def _shot_video_prompt(self, state: ProjectState, episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> str:
        parts = [
            self._visual_style_prompt(state),
            "视频片段生成要求:",
            shot.video_prompt,
        ]
        if shot.start_frame_source == "previous_shot_last_frame":
            parts.append(
                "首帧继承: 本片段起始画面严格参考上一片段末尾帧，保持人物姿态、空间方向、"
                "道具位置、能量位置和环境粒子连续，再从该状态进入本片段动作。"
            )
            if shot.start_frame_inheritance_reason:
                parts.append(f"继承理由: {shot.start_frame_inheritance_reason}")
        else:
            parts.append("起始参考: 以本片段参考帧为首帧视觉基准，保持人物、场景和道具一致。")
        return "\n".join(item for item in parts if item)

    async def _run_generation_node_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        node_name: str,
        episode_key: str,
    ) -> BaseModel:
        return await getattr(self, f"_run_{node_name}_for_episode")(project_dir, state, episode_key)

    @staticmethod
    def _empty_run_outputs(target_nodes: list[str]) -> dict[str, BaseModel]:
        outputs: dict[str, BaseModel] = {}
        if "storyboard_generation" in target_nodes:
            outputs["storyboard_generation"] = StoryboardGenerationOutput(generated_episodes=[])
        if "shot_bgm_generation" in target_nodes:
            outputs["shot_bgm_generation"] = ShotBGMGenerationOutput(generated_bgms=[])
        if "ref_frame_generation" in target_nodes:
            outputs["ref_frame_generation"] = RefFrameGenerationOutput(generated_ref_frames=[])
        if "shot_video_generation" in target_nodes:
            outputs["shot_video_generation"] = ShotVideoGenerationOutput(generated_videos=[])
        if "dynamic_asset_solidification" in target_nodes:
            outputs["dynamic_asset_solidification"] = DynamicAssetSolidificationOutput(solidified_assets=[])
        return outputs

    @staticmethod
    def _merge_run_output(run_outputs: dict[str, BaseModel], node_name: str, output: BaseModel) -> None:
        current = run_outputs[node_name]
        if node_name == "storyboard_generation":
            if not isinstance(current, StoryboardGenerationOutput) or not isinstance(output, StoryboardGenerationOutput):
                raise TypeError("storyboard_generation output type mismatch")
            current.generated_episodes.extend(output.generated_episodes)
        elif node_name == "shot_bgm_generation":
            if not isinstance(current, ShotBGMGenerationOutput) or not isinstance(output, ShotBGMGenerationOutput):
                raise TypeError("shot_bgm_generation output type mismatch")
            current.generated_bgms.extend(output.generated_bgms)
        elif node_name == "ref_frame_generation":
            if not isinstance(current, RefFrameGenerationOutput) or not isinstance(output, RefFrameGenerationOutput):
                raise TypeError("ref_frame_generation output type mismatch")
            current.generated_ref_frames.extend(output.generated_ref_frames)
        elif node_name == "shot_video_generation":
            if not isinstance(current, ShotVideoGenerationOutput) or not isinstance(output, ShotVideoGenerationOutput):
                raise TypeError("shot_video_generation output type mismatch")
            current.generated_videos.extend(output.generated_videos)
        elif node_name == "dynamic_asset_solidification":
            if not isinstance(current, DynamicAssetSolidificationOutput) or not isinstance(
                output, DynamicAssetSolidificationOutput
            ):
                raise TypeError("dynamic_asset_solidification output type mismatch")
            current.solidified_assets.extend(output.solidified_assets)
        else:
            raise ValueError(f"Unsupported generation node output merge: {node_name}")

    def _save_run_outputs(self, project_dir: Path, run_outputs: dict[str, BaseModel]) -> None:
        for node_name, output in run_outputs.items():
            self.repo.save_node_output(project_dir, node_name, output)

    @staticmethod
    def _target_generation_nodes(until: str, only: str | None) -> list[str]:
        if only:
            return [only]
        if until in DEFAULT_GENERATION_NODES:
            return DEFAULT_GENERATION_NODES[: DEFAULT_GENERATION_NODES.index(until) + 1]
        return GENERATION_NODES[: GENERATION_NODES.index(until) + 1]

    def _sort_episode_keys_in_story_order(self, state: ProjectState, episode_keys: list[str]) -> list[str]:
        selected = set(episode_keys)
        ordered = [
            episode_key
            for episode_key in self._expected_episode_keys(state)
            if episode_key in selected
        ]
        ordered.extend(
            episode_key
            for episode_key in episode_keys
            if episode_key not in set(ordered)
        )
        return ordered

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "dynamic_asset_solidification",
        force: bool = False,
        episode_keys: list[str] | None = None,
        only: str | None = None,
        shot_selectors: list[str] | None = None,
    ) -> ProjectState:
        if until not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation stop node: {until}")
        if only is not None and only not in GENERATION_NODES:
            raise ValueError(f"Unsupported generation only node: {only}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        self._hydrate_roles_from_design_files(project_dir, state)
        selected_episode_keys, checklist = selected_episode_keys_from_checklist(
            self.repo,
            project_dir,
            state,
            episode_keys=episode_keys,
        )
        if force and not episode_keys:
            selected_episode_keys = self._expected_episode_keys(state)

        if not selected_episode_keys:
            update_checklist_from_state(self.repo, project_dir, state, default_generate=False)
            logger.info("workflow=generation skipped project_id=%s reason=no_selected_episodes", state.project_id)
            return state

        selected_set = set(selected_episode_keys)
        known_episode_keys = {str(item.get("episode_key")) for item in checklist.get("episodes", [])}
        unknown_episode_keys = sorted(selected_set.difference(known_episode_keys))
        if unknown_episode_keys:
            raise ValueError(f"Unknown episode keys: {', '.join(unknown_episode_keys)}")
        selected_episode_keys = self._sort_episode_keys_in_story_order(state, selected_episode_keys)
        selected_set = set(selected_episode_keys)

        target_nodes = self._target_generation_nodes(until, only)
        if shot_selectors and "storyboard_generation" in target_nodes:
            raise ValueError("--shots can only be used with generation nodes after storyboard_generation")
        run_outputs = self._empty_run_outputs(target_nodes)
        logger.info(
            "workflow=generation project_id=%s until=%s only=%s force=%s episodes=%s shots=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys),
            ",".join(shot_selectors or []) or "-",
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        previous_active_shot_selectors = getattr(self, "_active_shot_selectors", None)
        previous_force_generation = getattr(self, "_force_generation", None)
        self._force_generation = bool(force)
        if shot_selectors:
            self._active_shot_selectors = {str(selector).strip().lower() for selector in shot_selectors if str(selector).strip()}
        elif hasattr(self, "_active_shot_selectors"):
            delattr(self, "_active_shot_selectors")
        try:
            for episode_index, episode_key in enumerate(selected_episode_keys, start=1):
                self._active_episode_keys = {episode_key}
                logger.info(
                    "episode %d/%d %s started nodes=%s",
                    episode_index,
                    len(selected_episode_keys),
                    episode_key,
                    ",".join(target_nodes),
                )
                try:
                    for node_index, node_name in enumerate(target_nodes, start=1):
                        with log_context(node_name=node_name, episode_key=episode_key):
                            logger.info(
                                "episode %s node %d/%d %s started",
                                episode_key,
                                node_index,
                                len(target_nodes),
                                node_name,
                            )
                            output = await self._run_generation_node_for_episode(
                                project_dir,
                                state,
                                node_name,
                                episode_key,
                            )
                            self._merge_run_output(run_outputs, node_name, output)
                            if node_name == "storyboard_generation":
                                update_storyboard_history_from_episode(
                                    self.repo,
                                    project_dir,
                                    state,
                                    self._load_storyboard_episode(project_dir, episode_key),
                                )
                            if node_name == "dynamic_asset_solidification":
                                existing_dynamic_assets = [
                                    item
                                    for item in state.metadata.get("dynamic_assets", [])
                                    if str(item.get("episode_key")) != episode_key
                                ]
                                if not isinstance(output, DynamicAssetSolidificationOutput):
                                    raise TypeError("dynamic_asset_solidification output type mismatch")
                                output = cast(DynamicAssetSolidificationOutput, output)
                                state.metadata["dynamic_assets"] = existing_dynamic_assets + [
                                    item.model_dump(mode="json")
                                    for item in output.solidified_assets
                                ]
                            state.mark_completed(node_name)
                            self.repo.save_state(project_dir, state)
                            self._save_run_outputs(project_dir, run_outputs)
                            logger.info(
                                "episode %s node %d/%d %s completed current_node=%s",
                                episode_key,
                                node_index,
                                len(target_nodes),
                                node_name,
                                state.current_node,
                            )
                except Exception:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        failed_episode_keys={episode_key},
                    )
                    logger.exception("episode %s failed", episode_key)
                    raise
                if target_nodes[-1] == GENERATION_NODES[-1]:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        processed_episode_keys={episode_key},
                    )
                logger.info("episode %d/%d %s completed", episode_index, len(selected_episode_keys), episode_key)
        finally:
            if previous_active_episode_keys is None:
                if hasattr(self, "_active_episode_keys"):
                    delattr(self, "_active_episode_keys")
            else:
                self._active_episode_keys = previous_active_episode_keys
            if previous_active_shot_selectors is None:
                if hasattr(self, "_active_shot_selectors"):
                    delattr(self, "_active_shot_selectors")
            else:
                self._active_shot_selectors = previous_active_shot_selectors
            if previous_force_generation is None:
                if hasattr(self, "_force_generation"):
                    delattr(self, "_force_generation")
            else:
                self._force_generation = previous_force_generation

        processed_episode_keys = selected_set if target_nodes[-1] == GENERATION_NODES[-1] else None
        update_checklist_from_state(
            self.repo,
            project_dir,
            state,
            default_generate=False,
            processed_episode_keys=processed_episode_keys,
        )
        get_logger().info(
            "workflow=generation completed project_id=%s current_node=%s episodes=%s",
            state.project_id,
            state.current_node,
            ",".join(selected_episode_keys),
        )
        return state
