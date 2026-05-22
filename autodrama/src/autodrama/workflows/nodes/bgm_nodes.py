from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import WorkflowNode

BGM_NODE_NAMES = [
    "bgm_design",
    "bgm_generation",
]


def build_bgm_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        WorkflowNode(name=node_name, run=getattr(workflow, f"_run_{node_name}"))
        for node_name in BGM_NODE_NAMES
    ]


__all__ = ["BGM_NODE_NAMES", "build_bgm_nodes"]
