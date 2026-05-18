from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from autodrama.core.schemas import (
    DynamicAssetSolidificationOutput,
    ProjectState,
    RefFrameGenerationOutput,
    ShotDialogueAudioGenerationOutput,
    ShotVideoGenerationOutput,
    StoryboardGenerationOutput,
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
    "shot_dialogue_audio_generation",
    "ref_frame_generation",
    "shot_video_generation",
    "dynamic_asset_solidification",
]


class GenerationWorkflow(DynamicAssetNodeMixin, PregenWorkflow):
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
        if "shot_dialogue_audio_generation" in target_nodes:
            outputs["shot_dialogue_audio_generation"] = ShotDialogueAudioGenerationOutput(
                generated_dialogue_audios=[],
                skipped_dialogue_lines=[],
            )
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
        elif node_name == "shot_dialogue_audio_generation":
            if not isinstance(current, ShotDialogueAudioGenerationOutput) or not isinstance(
                output, ShotDialogueAudioGenerationOutput
            ):
                raise TypeError("shot_dialogue_audio_generation output type mismatch")
            current.generated_dialogue_audios.extend(output.generated_dialogue_audios)
            current.skipped_dialogue_lines.extend(output.skipped_dialogue_lines)
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

        target_nodes = [only] if only else GENERATION_NODES[: GENERATION_NODES.index(until) + 1]
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
