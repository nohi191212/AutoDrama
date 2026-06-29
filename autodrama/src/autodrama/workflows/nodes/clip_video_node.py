from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

CLIP_VIDEO_NODE_NAME = "clip_video_generation"


def build_clip_video_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=CLIP_VIDEO_NODE_NAME,
        run=workflow._run_clip_video_generation_for_episode,
    )


__all__ = ["CLIP_VIDEO_NODE_NAME", "build_clip_video_episode_node"]
