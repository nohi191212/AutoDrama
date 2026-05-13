"""Pipeline node: 【参考帧生成】Generate reference frames with characters for each shot."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class ReferenceFrameNode:
    """Generate a key reference frame for each storyboard shot.

    Combines: simple script + storyboard settings + scene layout image.
    The frame includes characters in position with correct appearance.
    Stores into shot.ref_frame.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("ReferenceFrameNode: stub — not yet implemented")
        return {
            "current_stage": "reference_frame",
            "errors": [],
        }
