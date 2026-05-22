from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import WorkflowNode

SCRIPT_NODE_NAMES = [
    "script_outline",
    "script_novel",
    "script_novel_extract",
]


def build_script_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        WorkflowNode(name=node_name, run=getattr(workflow, f"_run_{node_name}"))
        for node_name in SCRIPT_NODE_NAMES
    ]


__all__ = ["SCRIPT_NODE_NAMES", "build_script_nodes"]
