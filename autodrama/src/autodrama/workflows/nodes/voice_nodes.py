from __future__ import annotations

from typing import Any

from autodrama.workflows.runner import WorkflowNode

VOICE_NODE_NAMES = [
    "role_voice_generation",
]


def build_voice_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        WorkflowNode(name=node_name, run=getattr(workflow, f"_run_{node_name}"))
        for node_name in VOICE_NODE_NAMES
    ]


__all__ = ["VOICE_NODE_NAMES", "build_voice_nodes"]
