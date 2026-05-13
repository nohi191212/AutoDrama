"""Pipeline node: 【场景确认】Scene confirmation — audit for duplicates and consistency bugs."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class SceneConfirmNode:
    """Review all scenes across all episodes to detect duplicates or bugs.

    Ensures the same location is not generated twice under different names.
    If issues found, regenerates the problematic scenes.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("SceneConfirmNode: stub — not yet implemented")
        return {
            "current_stage": "scene_confirm",
            "errors": [],
        }
