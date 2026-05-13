"""Pipeline node: 【剪辑方案】Editing plan — LLM decides how to cut and arrange素材."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class EditingPlanNode:
    """Use Qwen3.5Max (or equivalent) to plan the edit for the current super-episode.

    Given all storyboard shots, the LLM decides:
    - Which clips to use and in what order
    - Whether to apply audio-visual separation (J-Cut / L-Cut)
    - Timing and pacing adjustments

    Produces a structured editing plan as output.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("EditingPlanNode: stub — not yet implemented")
        return {
            "current_stage": "editing_plan",
            "errors": [],
        }
