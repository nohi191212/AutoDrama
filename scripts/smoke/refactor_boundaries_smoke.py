from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.editing import EditingWorkflow  # noqa: E402
from autodrama.workflows.generation import GENERATION_NODES, GenerationWorkflow  # noqa: E402
from autodrama.workflows.nodes import (  # noqa: E402
    AVAILABLE_PREGEN_NODE_NAMES,
    DEFERRED_PREGEN_NODE_NAMES,
    PREGEN_NODE_NAMES,
    build_generation_episode_nodes,
    build_manual_pregen_nodes,
    build_pregen_nodes,
)
from autodrama.workflows.pregen import PREGEN_NODES, PREGEN_ONLY_NODES, PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "refactor_boundaries"
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")

    pregen = PregenWorkflow(repo=repo, router=router)
    generation = GenerationWorkflow(repo=repo, router=router)
    editing = EditingWorkflow(repo=repo, router=router)

    require(not isinstance(generation, PregenWorkflow), "GenerationWorkflow must not inherit PregenWorkflow")
    require(not isinstance(editing, PregenWorkflow), "EditingWorkflow must not inherit PregenWorkflow")
    require([node.name for node in build_pregen_nodes(pregen)] == PREGEN_NODE_NAMES, "Pregen node registry mismatch")
    require(
        [node.name for node in build_manual_pregen_nodes(pregen)] == DEFERRED_PREGEN_NODE_NAMES,
        "Manual pregen node registry mismatch",
    )
    require(PREGEN_NODE_NAMES == PREGEN_NODES, "Pregen node constants drifted")
    require(AVAILABLE_PREGEN_NODE_NAMES == PREGEN_ONLY_NODES, "Pregen only-node constants drifted")
    require(
        [node.name for node in build_generation_episode_nodes(generation)] == GENERATION_NODES,
        "Generation episode node registry mismatch",
    )

    require(router.text("script").name == "fake", "Fake text router mismatch")
    require(router.image("role").name == "fake", "Fake image router mismatch")
    require(router.video("shot").name == "fake", "Fake video router mismatch")
    require(router.audio("speech").name == "fake", "Fake audio router mismatch")
    require(router.music("bgm").name == "fake", "Fake music router mismatch")

    prompt_dir = ROOT_DIR / ".tmp" / "smoke" / "refactor_prompt_store"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "missing.md").write_text("Hello {{title}} {{missing}}", encoding="utf-8")
    store = PromptStore(prompt_dir, strict=True)
    try:
        store.render("missing", title="AutoDrama")
    except ValueError as exc:
        require("missing" in str(exc), f"Unexpected prompt error: {exc}")
    else:
        raise AssertionError("PromptStore strict mode did not reject unresolved variables")

    print("refactor_boundaries_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
