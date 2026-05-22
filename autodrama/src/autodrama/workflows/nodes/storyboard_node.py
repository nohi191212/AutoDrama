from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

STORYBOARD_NODE_NAME = "storyboard_generation"


def build_storyboard_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=STORYBOARD_NODE_NAME,
        run=workflow._run_storyboard_generation_for_episode,
    )


__all__ = ["STORYBOARD_NODE_NAME", "build_storyboard_episode_node"]
