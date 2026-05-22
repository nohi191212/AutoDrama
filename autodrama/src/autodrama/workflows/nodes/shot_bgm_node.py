from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

SHOT_BGM_NODE_NAME = "shot_bgm_generation"


def build_shot_bgm_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=SHOT_BGM_NODE_NAME,
        run=workflow._run_shot_bgm_generation_for_episode,
    )


__all__ = ["SHOT_BGM_NODE_NAME", "build_shot_bgm_episode_node"]
