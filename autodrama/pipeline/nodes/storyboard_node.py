"""Pipeline node: 【分镜生成】Storyboard generation — detailed shot-by-shot plans."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class StoryboardNode:
    """Generate detailed storyboard shots from script + scene + props + character settings.

    Each shot includes: layout_id, focal_length, camera_angle, camera_movement,
    detailed content, duration, role_appearances, role_audios, ref_props, ref_frame.

    Registers into STORYBOARDS['episode_X']['shot_Y'] = Shot(...).
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("StoryboardNode: stub — not yet implemented")
        return {
            "current_stage": "storyboard",
            "errors": [],
        }
