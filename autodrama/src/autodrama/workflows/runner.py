from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from autodrama.core.schemas import ProjectState
from autodrama.logging import log_context
from autodrama.repositories.project_repo import ProjectRepository

NodeFunc = Callable[[Path, ProjectState], Awaitable[ProjectState]]
EpisodeNodeFunc = Callable[[Path, ProjectState, str], Awaitable[BaseModel]]
NODE_COMPLETION_ALIASES = {
    "script_import": ("script_outline",),
    "key_vision_prompt": ("design_key_vision_prompt",),
    "key_vision_image_generation": ("key_vision_image", "design_key_vision_image"),
}


def node_is_completed(node_name: str, completed_nodes: list[str]) -> bool:
    if node_name in completed_nodes:
        return True
    return any(alias in completed_nodes for alias in NODE_COMPLETION_ALIASES.get(node_name, ()))


@dataclass(slots=True)
class WorkflowNode:
    name: str
    run: NodeFunc


@dataclass(slots=True)
class EpisodeWorkflowNode:
    name: str
    run: EpisodeNodeFunc


class WorkflowRunner:
    def __init__(self, *, repo: ProjectRepository, logger) -> None:
        self.repo = repo
        self.logger = logger

    async def run_nodes(
        self,
        project_dir: Path,
        state: ProjectState,
        nodes: list[WorkflowNode],
        *,
        force: bool = False,
        skip_completed: bool = True,
    ) -> ProjectState:
        for index, node in enumerate(nodes, start=1):
            if skip_completed and not force and node_is_completed(node.name, state.completed_nodes):
                with log_context(node_name=node.name):
                    self.logger.info("node %d/%d %s skipped", index, len(nodes), node.name)
                continue
            with log_context(node_name=node.name):
                self.logger.info("node %d/%d %s started", index, len(nodes), node.name)
                try:
                    state = await node.run(project_dir, state)
                    state.mark_completed(node.name)
                    self.repo.save_state(project_dir, state)
                except Exception:
                    self.logger.exception("node %d/%d %s failed", index, len(nodes), node.name)
                    raise
                self.logger.info(
                    "node %d/%d %s completed current_node=%s",
                    index,
                    len(nodes),
                    node.name,
                    state.current_node,
                )
        return state


def save_run_outputs(repo: ProjectRepository, project_dir: Path, outputs: dict[str, BaseModel]) -> None:
    for node_name, output in outputs.items():
        repo.save_node_output(project_dir, node_name, output)
