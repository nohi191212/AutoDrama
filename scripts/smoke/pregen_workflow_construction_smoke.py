from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def main() -> int:
    settings = load_settings(ROOT / "saodi.yaml")
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=ProviderRouter(settings))
    assert workflow.settings is repo.settings
    assert workflow.layout is repo.layout
    assert workflow.settings.generation.visual_style == "xuanhuan-v1"
    print("pregen workflow construction smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
