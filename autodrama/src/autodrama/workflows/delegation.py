from __future__ import annotations

from typing import Any

from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.pregen import PregenWorkflow


class PregenWorkflowDelegateMixin:
    """Compatibility bridge for workflows that still reuse pregen helpers.

    Generation and editing no longer need to inherit the pregen business
    workflow directly, but they still depend on shared layout, media, state,
    storyboard, and prompt helpers. This mixin keeps that reuse explicit while
    those helpers are migrated into smaller shared services.
    """

    def _init_pregen_delegate(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        delegate = PregenWorkflow(repo=repo, router=router, prompts=prompts)
        self._pregen = delegate
        for name in (
            "repo",
            "layout",
            "router",
            "prompts",
            "script_service",
            "role_service",
            "asset_service",
            "storyboard_service",
            "media_store",
            "storyboards",
            "runner",
        ):
            setattr(self, name, getattr(delegate, name))

    def __getattr__(self, name: str) -> Any:
        if name == "_pregen":
            raise AttributeError(name)
        delegate = self.__dict__.get("_pregen")
        if delegate is None:
            raise AttributeError(name)
        return getattr(delegate, name)


__all__ = ["PregenWorkflowDelegateMixin"]
