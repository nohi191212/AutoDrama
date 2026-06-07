from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from autodrama.core.schemas import ProjectState
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.director_service import DirectorService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

DIRECTOR_NODE_NAMES = [
    "director_prep",
]


class DirectorNodeBase:
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        director_service: DirectorService,
        script_contents: ScriptContentRepository,
        logger: Any,
        force_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.director_service = director_service
        self.script_contents = script_contents
        self.logger = logger
        self.force_getter = force_getter or (lambda: False)

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def validate_episode_keys(self, label: str, payload: dict[str, object], state: ProjectState) -> None:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    def text_provider(self):
        try:
            return self.router.text("director")
        except KeyError:
            return self.router.text("script")


class DirectorPrepNode(DirectorNodeBase):
    name = "director_prep"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.text_provider()
        self.logger.info(
            "node=director_prep provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_novel.novel_full",
        )
        output = await self.director_service.director_prep(
            state,
            provider,
            novel_full=novel_full,
        )
        output_keys = [item.episode_key for item in output.episodes]
        if set(output_keys) != set(episode_keys):
            raise ValueError(
                "director_prep.episodes must contain exactly "
                f"{', '.join(episode_keys)}; got {', '.join(sorted(output_keys)) or '-'}"
            )
        path = self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["director_prep"] = output.model_dump(mode="json", exclude_none=True)
        state.metadata["director_prep_path"] = self.layout.project_relative(project_dir, path)
        state.metadata["director_prep_episode_keys"] = episode_keys
        state.budget.used_text_calls += 1
        return state


def build_director_node_runners(workflow: Any) -> dict[str, DirectorNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)

    def force_getter() -> bool:
        return bool(getattr(workflow, "_force_pregen", False))

    deps = {
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "director_service": workflow.director_service,
        "script_contents": script_contents,
        "logger": getattr(workflow, "logger", None) or get_logger(),
        "force_getter": force_getter,
    }
    return {
        DirectorPrepNode.name: DirectorPrepNode(**deps),
    }


def build_director_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_director_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in DIRECTOR_NODE_NAMES
    ]


__all__ = [
    "DIRECTOR_NODE_NAMES",
    "DirectorPrepNode",
    "build_director_node_runners",
    "build_director_nodes",
]
