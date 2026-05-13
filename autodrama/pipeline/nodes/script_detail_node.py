"""Pipeline node: 【剧本-详细剧本生成】Detailed script per episode."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ScriptDetailNode:
    """Generate detailed script for all episodes simultaneously.

    Registers into SCRIPT.detailed_script = {episode_1: ..., episode_2: ...}.
    Each episode should be 50-70s of content.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("ScriptDetailNode: stub — not yet implemented")
        return {
            "current_stage": "script_detail",
            "errors": [],
        }
