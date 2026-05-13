"""Pipeline node: 【场景-场景图生成】Generate character-free scene layout images."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class SceneImageNode:
    """Generate empty scene layout images (no characters) from scene descriptions.

    Uses the simple script + scene description to generate background images.
    Writes into DESIGN_LAYOUT[scene]['variant'].image.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("SceneImageNode: stub — not yet implemented")
        return {
            "current_stage": "scene_image",
            "errors": [],
        }
