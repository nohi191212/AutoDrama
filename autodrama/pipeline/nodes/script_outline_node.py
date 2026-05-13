"""Pipeline node: 【剧本-大纲】Script outline generation."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ScriptOutlineNode:
    """Generate a high-level script outline from raw_script.

    Registers into SCRIPT.raw_script.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("ScriptOutlineNode: stub — not yet implemented")
        return {
            "current_stage": "script_outline",
            "errors": [],
        }
