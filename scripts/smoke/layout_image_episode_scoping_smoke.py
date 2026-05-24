from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import Layout  # noqa: E402
from autodrama.providers.local.mock.fake import FakeImageProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class RecordingImageProvider(FakeImageProvider):
    def __init__(self) -> None:
        self.generated_asset_ids: list[str] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        metadata = metadata or {}
        self.generated_asset_ids.append(str(metadata.get("asset_id")))
        return await super().generate_image(prompt, refs=refs, size=size, metadata=metadata)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    settings.project.episode_duration_seconds = 30

    project_dir = settings.output.root_dir / "layout_image_episode_scoping"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Layout Image Episode Scoping",
        raw_script="第一集在办公室，第二集在机房。",
        project_id="layout_image_episode_scoping",
        episode_count=2,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.layouts = {
        "layout_第一集办公室": Layout(
            id="layout_第一集办公室",
            name="第一集办公室",
            desc="第一集出现的办公室。",
            prompt="真人电影质感，无人物办公室空场景，冷白灯，桌面整洁。",
            episode_keys=["episode_001"],
        ),
        "layout_第二集机房": Layout(
            id="layout_第二集机房",
            name="第二集机房",
            desc="第二集出现的服务器机房。",
            prompt="真人电影质感，无人物服务器机房空场景，蓝色指示灯，金属机柜。",
            episode_keys=["episode_002"],
        ),
    }
    repo.save_state(project_dir, state)

    router = ProviderRouter(settings, provider_override="fake")
    image_provider = RecordingImageProvider()
    router._fake_image = image_provider

    workflow = PregenWorkflow(repo=repo, router=router)
    await workflow.run(project_dir, only="layout_image_generation", force=True)
    require(
        image_provider.generated_asset_ids == ["layout_第一集办公室", "layout_第二集机房"],
        f"initial layout generation did not render all layouts: {image_provider.generated_asset_ids}",
    )

    image_provider.generated_asset_ids = []
    await workflow.run(project_dir, only="layout_image_generation", episode_keys=["episode_002"], force=True)
    require(
        image_provider.generated_asset_ids == ["layout_第二集机房"],
        f"episode-scoped layout generation rendered wrong layouts: {image_provider.generated_asset_ids}",
    )

    output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "layout_image_generation.json").read_text(encoding="utf-8")
    )
    output_ids = [item["asset_id"] for item in output["generated_assets"]]
    require(
        output_ids == ["layout_第一集办公室", "layout_第二集机房"],
        f"episode-scoped layout output did not preserve existing records: {output_ids}",
    )

    print("layout_image_episode_scoping_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
