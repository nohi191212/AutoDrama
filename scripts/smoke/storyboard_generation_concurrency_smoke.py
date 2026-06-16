from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardPromptEpisode, StoryboardPromptShot  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import build_storyboard_asset_node_runners  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardGenerationNode  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_fake_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    provider = ProviderRouter(settings, provider_override="fake").image("storyboard")
    require(
        StoryboardGenerationNode.generation_concurrency(provider) == 5,
        "Default storyboard_generation concurrency must be 5",
    )

    low_provider = SimpleNamespace(settings=SimpleNamespace(options={"storyboard_generation_concurrency": 2}))
    require(
        StoryboardGenerationNode.generation_concurrency(low_provider) == 2,
        "Explicit storyboard_generation_concurrency=2 was not honored",
    )

    high_provider = SimpleNamespace(settings=SimpleNamespace(options={"storyboard_generation_concurrency": 99}))
    require(
        StoryboardGenerationNode.generation_concurrency(high_provider) == 5,
        "Storyboard generation concurrency must be capped at 5",
    )

    invalid_provider = SimpleNamespace(settings=SimpleNamespace(options={"storyboard_generation_concurrency": "bad"}))
    require(
        StoryboardGenerationNode.generation_concurrency(invalid_provider) == 5,
        "Invalid storyboard_generation_concurrency must fall back to 5",
    )

    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "storyboard_generation_concurrency" / "outputs"
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    node = build_storyboard_asset_node_runners(workflow)["storyboard_generation"]
    project_dir = settings.output.root_dir / "resume_existing_file"
    shot = StoryboardPromptShot(
        shot_id="episode_001_shot_001",
        duration_seconds=15,
        role_ids=[],
        layout_ids=["layout_test"],
        prop_ids=[],
        video_prompt="0-1秒：测试镜头。",
    )
    episode = StoryboardPromptEpisode(episode_key="episode_001", shots=[shot])
    asset_id = node.storyboard_panel_asset_id(shot.shot_id)
    existing_path = node.layout.image_asset_path(project_dir, "storyboards", asset_id)
    write_fake_png(existing_path)
    item = node._resume_storyboard_sheet_from_existing_file(
        project_dir=project_dir,
        provider=provider,
        episode=episode,
        shot=shot,
        asset_id=asset_id,
        existing_path=node.layout.project_relative(project_dir, existing_path),
        image_prompt="storyboard prompt",
    )
    require(item is not None, "Existing storyboard sheet file was not resumed")
    require(item.shot_id == "episode_001_shot_001", f"Unexpected resumed shot_id: {item.shot_id if item else None}")
    require(
        item.asset_path == "assets/images/storyboards/episode_001_shot_001_storyboard_panel.png",
        f"Unexpected resumed asset_path: {item.asset_path}",
    )
    require(item.raw_response.get("resumed_from_existing_file") is True, f"Unexpected raw_response: {item.raw_response}")

    print("storyboard_generation_concurrency_smoke=ok")
    print(f"default_concurrency={StoryboardGenerationNode.generation_concurrency(provider)}")
    print(f"resumed_asset_path={item.asset_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
