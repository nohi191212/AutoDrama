from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import WorkflowNode

ROLE_NODE_NAMES = [
    "role_extract",
    "role_design",
]


def build_role_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        WorkflowNode(name=node_name, run=getattr(workflow, f"_run_{node_name}"))
        for node_name in ROLE_NODE_NAMES
    ]


__all__ = ["ROLE_NODE_NAMES", "build_role_nodes"]
