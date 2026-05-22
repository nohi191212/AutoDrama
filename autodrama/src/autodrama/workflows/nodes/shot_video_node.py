from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

SHOT_VIDEO_NODE_NAME = "shot_video_generation"


def build_shot_video_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=SHOT_VIDEO_NODE_NAME,
        run=workflow._run_shot_video_generation_for_episode,
    )


__all__ = ["SHOT_VIDEO_NODE_NAME", "build_shot_video_episode_node"]
