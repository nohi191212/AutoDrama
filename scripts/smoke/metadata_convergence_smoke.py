from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_state_payload(project_dir: Path) -> dict[str, object]:
    return json.loads((project_dir / "state.json").read_text(encoding="utf-8"))


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"metadata_convergence_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Metadata Convergence Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)

    await workflow.run(project_dir, until="role_extract", force=True)
    state_payload = load_state_payload(project_dir)
    metadata = state_payload.get("metadata", {})
    require(isinstance(metadata, dict), "state metadata should be a dict")
    require("role_extract" not in metadata, "state.json metadata should not contain role_extract")
    role_refs = state_payload.get("roles", {})
    require(isinstance(role_refs, dict) and "林舟" in role_refs, "state roles should contain role JSON refs")
    role_ref_path = project_dir / str(role_refs["林舟"])
    require(role_ref_path.exists(), f"role extract JSON missing: {role_ref_path}")
    role_payload = json.loads(role_ref_path.read_text(encoding="utf-8"))
    require(role_payload.get("extract"), "per-role JSON should contain extract content")
    require((project_dir / "assets" / "json" / "nodes" / "role_extract.json").exists(), "role_extract node output missing")

    await workflow.run(project_dir, only="prop_extract", force=True)
    state_payload = load_state_payload(project_dir)
    metadata = state_payload.get("metadata", {})
    require(isinstance(metadata, dict), "state metadata should be a dict after prop_extract")
    require("prop_extract" not in metadata, "state.json metadata should not contain prop_extract")
    require((project_dir / "assets" / "json" / "nodes" / "prop_extract.json").exists(), "prop_extract node output missing")
    require(
        bool(list((project_dir / "assets" / "json" / "props").glob("prop_*.json"))),
        "per-prop extract JSON should exist",
    )

    await workflow.run(project_dir, only="layout_design", force=True)
    state_payload = load_state_payload(project_dir)
    metadata = state_payload.get("metadata", {})
    require(isinstance(metadata, dict), "state metadata should be a dict after layout_design")
    require("simple_script" not in metadata, "state.json metadata should not contain simple_script")
    require("global_script" not in metadata, "state.json metadata should not contain global_script")
    require((project_dir / "assets" / "json" / "nodes" / "layout_design.json").exists(), "layout_design node output missing")
    require(
        not (project_dir / "assets" / "json" / "nodes" / "script_compress.json").exists(),
        "script_compress node output should not be written",
    )

    try:
        await workflow.run(project_dir, only="script_compress", force=True)
    except ValueError as exc:
        require("Unsupported pregen only node" in str(exc), "script_compress should be unsupported")
    else:
        raise AssertionError("script_compress should be rejected")

    print("metadata_convergence_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
