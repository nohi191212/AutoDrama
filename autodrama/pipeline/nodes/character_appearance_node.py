"""Pipeline node: 【角色-基本外形图】Character base appearance image generation."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class CharacterAppearanceNode:
    """Generate base appearance images for each character in different outfits.

    Registers into role.appearance = {variant: RoleAppearance}.
    Each variant has a text description and a generated image path.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("CharacterAppearanceNode: stub — not yet implemented")
        return {
            "current_stage": "character_appearance",
            "errors": [],
        }
