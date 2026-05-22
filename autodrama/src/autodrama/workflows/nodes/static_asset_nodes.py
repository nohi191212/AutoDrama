from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import WorkflowNode

STATIC_ASSET_NODE_NAMES = [
    "role_appearance_generation",
    "prop_design",
    "prop_image_generation",
    "script_compress",
    "layout_design",
    "layout_dedupe_review",
    "layout_image_generation",
]


def build_static_asset_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        WorkflowNode(name=node_name, run=getattr(workflow, f"_run_{node_name}"))
        for node_name in STATIC_ASSET_NODE_NAMES
    ]


__all__ = ["STATIC_ASSET_NODE_NAMES", "build_static_asset_nodes"]
