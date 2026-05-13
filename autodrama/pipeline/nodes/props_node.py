"""Pipeline node: 【道具-基本设定】Key props / items settings and image generation."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class PropsNode:
    """Generate settings for key props that appear throughout the series.

    Registers into PROPS['item_name'] = Prop('item_name').
    Each prop has a text description (size, material, etc.) and an image.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("PropsNode: stub — not yet implemented")
        return {
            "current_stage": "props_setting",
            "errors": [],
        }
