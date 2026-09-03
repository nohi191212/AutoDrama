from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import EpisodeWorkflowNode

DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME = "dynamic_asset_solidification"


def build_dynamic_asset_solidification_episode_node(workflow: Any) -> EpisodeWorkflowNode:
    return EpisodeWorkflowNode(
        name=DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
        run=workflow._run_dynamic_asset_solidification_for_episode,
    )


__all__ = [
    "DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME",
    "build_dynamic_asset_solidification_episode_node",
]
