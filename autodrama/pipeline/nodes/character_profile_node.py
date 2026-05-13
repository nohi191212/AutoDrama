"""Pipeline node: 【角色-基本设定】Character profile generation (rebuttle 3X)."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class CharacterProfileNode:
    """Generate detailed character profiles from the final script.

    For each character: personality, appearance, voice type,
    and outfit/mood per scene.
    Registers into ROLES['name'] = Role('name').
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("CharacterProfileNode: stub — not yet implemented")
        return {
            "current_stage": "character_profile",
            "errors": [],
        }
