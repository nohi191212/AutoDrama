from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

SHOT_DIALOGUE_AUDIO_NODE_NAME = "shot_dialogue_audio_generation"


def build_shot_dialogue_audio_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=SHOT_DIALOGUE_AUDIO_NODE_NAME,
        run=workflow._run_shot_dialogue_audio_generation_for_episode,
    )


__all__ = ["SHOT_DIALOGUE_AUDIO_NODE_NAME", "build_shot_dialogue_audio_episode_node"]
