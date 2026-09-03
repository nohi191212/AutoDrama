from __future__ import annotations

from typing import Any


class WorkflowRouterAdapter:
    def __init__(self, router: Any) -> None:
        self._router = router

    def __getattr__(self, name: str) -> Any:
        return getattr(self._router, name)

    def _call(self, method_name: str, purpose: str, *, node_name: str | None = None):
        method = getattr(self._router, method_name)
        if node_name is None:
            return method(purpose)
        try:
            return method(purpose, node_name=node_name)
        except TypeError as exc:
            if "node_name" not in str(exc):
                raise
            return method(purpose)

    def text(self, purpose: str, *, node_name: str | None = None):
        return self._call("text", purpose, node_name=node_name)

    def image(self, purpose: str, *, node_name: str | None = None):
        return self._call("image", purpose, node_name=node_name)

    def video(self, purpose: str, *, node_name: str | None = None):
        return self._call("video", purpose, node_name=node_name)

    def audio(self, purpose: str, *, node_name: str | None = None):
        return self._call("audio", purpose, node_name=node_name)

    def judge(self, purpose: str, *, node_name: str | None = None):
        return self._call("judge", purpose, node_name=node_name)

    def music(self, purpose: str, *, node_name: str | None = None):
        return self._call("music", purpose, node_name=node_name)


def adapt_workflow_router(router: Any) -> WorkflowRouterAdapter:
    if isinstance(router, WorkflowRouterAdapter):
        return router
    return WorkflowRouterAdapter(router)
