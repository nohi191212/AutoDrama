from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

REF_FRAME_NODE_NAME = "ref_frame_generation"


def build_ref_frame_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=REF_FRAME_NODE_NAME,
        run=workflow._run_ref_frame_generation_for_episode,
    )


__all__ = ["REF_FRAME_NODE_NAME", "build_ref_frame_episode_node"]
