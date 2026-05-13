"""Pipeline node: 【剧本-压缩】Compress full scripts into simple and global summaries."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ScriptCompressNode:
    """Compress the detailed script into per-episode simple scripts and a global summary.

    Produces SCRIPT.simple_script (per episode) and SCRIPT.global_script (overall).
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("ScriptCompressNode: stub — not yet implemented")
        return {
            "current_stage": "script_compress",
            "errors": [],
        }
