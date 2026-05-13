"""Pipeline node: 【配乐方案】BGM scoring — LLM decides music placement and technique."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class BgmScoringNode:
    """Use Qwen3.5Max to plan BGM placement for the assembled super-episode.

    Given the complete video + BGM list (text descriptions), the LLM decides:
    - Which BGM track plays when
    - Start and end timestamps
    - Transition technique (fade in/out, hard cut, crossfade)

    Produces a structured scoring plan, then applies the mix.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("BgmScoringNode: stub — not yet implemented")
        return {
            "current_stage": "bgm_scoring",
            "errors": [],
        }
