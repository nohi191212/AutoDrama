"""Pipeline node: 【场景-场景描述生成】Scene description generation and layout registration."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class SceneDescriptionNode:
    """Generate scene descriptions for each location in the script.

    Checks whether a scene already exists in DESIGN_LAYOUT.
    If not, generates a new scene layout description.
    Registers into DESIGN_LAYOUT['scene_name'] = {variants...}.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("SceneDescriptionNode: stub — not yet implemented")
        return {
            "current_stage": "scene_description",
            "errors": [],
        }
