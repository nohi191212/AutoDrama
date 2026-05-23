from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import ProjectState, RoleExtractItem, ScriptBundle  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    state = ProjectState(
        project_id="role_design_missing_episode_keys_smoke",
        title="Role Design Missing Episode Keys Smoke",
        raw_script="",
        script=ScriptBundle(raw_script=""),
        metadata={"episode_count": 2},
    )

    valid = RoleExtractItem(name="有效角色", episode_keys=["episode_001"])
    require(workflow._role_episode_keys(valid, state) == ["episode_001"], "valid episode_keys should pass")

    missing = RoleExtractItem(name="缺集数角色", episode_keys=[])
    try:
        workflow._role_episode_keys(missing, state)
    except ValueError as exc:
        require("missing episode_keys" in str(exc), f"unexpected missing-key error: {exc}")
    else:
        raise AssertionError("missing episode_keys should fail")

    invalid = RoleExtractItem(name="错集数角色", episode_keys=["episode_099"])
    try:
        workflow._role_episode_keys(invalid, state)
    except ValueError as exc:
        require("do not match existing episodes" in str(exc), f"unexpected invalid-key error: {exc}")
    else:
        raise AssertionError("invalid episode_keys should fail when no valid keys remain")

    print("role_design_missing_episode_keys_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
