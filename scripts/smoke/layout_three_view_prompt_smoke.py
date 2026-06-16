from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeImageProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.core.schemas import Layout  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingLayoutImageProvider(FakeImageProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        self.prompts.append(prompt)
        return await super().generate_image(prompt, refs=refs, size=size, metadata=metadata)


async def main_async() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "layout_three_view_prompt"
    settings.project.episode_count = 1
    settings.project.episode_duration_seconds = 30

    project_dir = settings.output.root_dir / "layout_three_view_prompt"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Layout Three View Prompt Smoke",
        raw_script="第一集发生在雨夜办公室。",
        project_id="layout_three_view_prompt",
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.layouts = {
        "layout_雨夜办公室": Layout(
            id="layout_雨夜办公室",
            name="雨夜办公室",
            desc="雨夜现代办公室，长桌和落地窗可复用。",
            prompt="真人电影质感，无人物办公室空场景，冷白灯，桌面整洁。",
            episode_keys=["episode_001"],
        )
    }
    repo.save_state(project_dir, state)

    router = ProviderRouter(settings, provider_override="fake")
    image_provider = RecordingLayoutImageProvider()
    router._fake_image = image_provider

    workflow = PregenWorkflow(repo=repo, router=router)
    await workflow.run(project_dir, only="layout_image_generation", force=True)

    require(image_provider.prompts, "layout image generation should call the image provider")
    prompt = image_provider.prompts[0]
    require("无人物场景三视图设定板" in prompt, "three-view marker missing")
    require("左侧为顶视平面" in prompt, "top-view instruction missing")
    require("中间为正向主视" in prompt, "front-view instruction missing")
    require("右侧为侧向或45度透视图" in prompt, "side-view instruction missing")
    require("同一个空间" in prompt, "same-space constraint missing")

    print("layout_three_view_prompt_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
