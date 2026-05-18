from __future__ import annotations

from pathlib import Path

from autodrama.core.schemas import ProjectState
from autodrama.logging import get_logger, setup_logging
from autodrama.workflows.generation_checklist import (
    selected_episode_keys_from_checklist,
    update_checklist_from_state,
)
from autodrama.workflows.pregen import PregenWorkflow

GENERATION_NODES = [
    "storyboard_generation",
    "shot_dialogue_audio_generation",
    "ref_frame_generation",
    "shot_video_generation",
    "dynamic_asset_solidification",
]


class GenerationWorkflow(PregenWorkflow):
    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "dynamic_asset_solidification",
        force: bool = False,
        episode_keys: list[str] | None = None,
        only: str | None = None,
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

        target_nodes = [only] if only else GENERATION_NODES[: GENERATION_NODES.index(until) + 1]
        logger.info(
            "workflow=generation project_id=%s until=%s only=%s force=%s episodes=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys),
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        self._active_episode_keys = selected_set
        try:
            for index, node_name in enumerate(target_nodes, start=1):
                logger.info("node %d/%d %s started", index, len(target_nodes), node_name)
                try:
                    state = await getattr(self, f"_run_{node_name}")(project_dir, state)
                    state.mark_completed(node_name)
                    self.repo.save_state(project_dir, state)
                except Exception:
                    update_checklist_from_state(
                        self.repo,
                        project_dir,
                        state,
                        default_generate=False,
                        failed_episode_keys=selected_set,
                    )
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
            if previous_active_episode_keys is None:
                delattr(self, "_active_episode_keys")
            else:
                self._active_episode_keys = previous_active_episode_keys

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
