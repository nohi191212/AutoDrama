"""Pipeline node: 【角色-声音】Character voice / timbre generation for each emotion."""

from __future__ import annotations

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class CharacterVoiceNode:
    """Generate voice profiles for each character under different emotions.

    Flow: character intro → voice description (LLM) → voice generation (TTS model).
    Registers into role.audio = {emotion: RoleAudio}.
    """

    def execute(self, state: DramaState) -> dict:
        logger.info("CharacterVoiceNode: stub — not yet implemented")
        return {
            "current_stage": "character_voice",
            "errors": [],
        }
