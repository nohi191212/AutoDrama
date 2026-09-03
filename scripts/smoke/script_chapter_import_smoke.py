from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.script_chapter_service import load_script_chapters
from autodrama.workflows.pregen import PregenWorkflow


ROOT = Path(__file__).resolve().parents[2]
SMOKE_ROOT = ROOT / ".tmp" / "script_chapter_import_smoke"


def write_fixture() -> Path:
    if SMOKE_ROOT.exists():
        shutil.rmtree(SMOKE_ROOT)
    chapters_dir = SMOKE_ROOT / "chapters"
    chapters_dir.mkdir(parents=True)
    for index in range(1, 4):
        (chapters_dir / f"chap{index:04d}_测试章节{index}.txt").write_text(
            f"这是第 {index} 个测试章节的正文，不包含章节标题。",
            encoding="utf-8",
        )
    config_path = SMOKE_ROOT / "config.yaml"
    config_path.write_text(
        """project:
  id: script_chapter_import_smoke
  title: Script Chapter Import Smoke
  script_chapters_dir: ./chapters
  episode_count: 3
  episode_duration_seconds: 30
output:
  root_dir: ./outputs
generation:
  visual_style: xuanhuan-v1
routing:
  text:
    script: fake
    role: fake
providers: {}
""",
        encoding="utf-8",
        newline="\n",
    )
    return config_path


def expect_rejected(chapters_dir: Path, filename: str, content: str) -> None:
    path = chapters_dir / filename
    path.write_text(content, encoding="utf-8")
    try:
        load_script_chapters(chapters_dir)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid chapter fixture was accepted: {filename}")
    path.unlink()


async def main() -> None:
    fangu_settings = load_settings(ROOT / "fangu.yaml")
    fangu_chapters = load_script_chapters(fangu_settings.project.script_chapters_dir)
    assert [chapter.filename for chapter in fangu_chapters] == [
        "chap0001_刻名墙.txt",
        "chap0002_地下斗场.txt",
        "chap0003_排械之印.txt",
    ]

    config_path = write_fixture()
    settings = load_settings(config_path)
    chapters = load_script_chapters(settings.project.script_chapters_dir)
    assert [chapter.filename for chapter in chapters] == [
        "chap0001_测试章节1.txt",
        "chap0002_测试章节2.txt",
        "chap0003_测试章节3.txt",
    ]

    expect_rejected(
        settings.project.script_chapters_dir,
        "第0004章_非法标题.txt",
        "正文",
    )
    expect_rejected(
        settings.project.script_chapters_dir,
        "chap0004_含标题.txt",
        "第 4 章 不应出现在正文",
    )

    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()
    router = ProviderRouter(settings, provider_override="fake")
    state = await PregenWorkflow(repo=repo, router=router).run(
        project_dir,
        only="script_import",
    )
    expected_keys = {"episode_001", "episode_002", "episode_003"}
    assert set(state.script.novel_full) == expected_keys
    assert state.script.outline is None
    assert state.script.episode_outlines == {}
    assert state.metadata["script_mode"] == "mature_chapters"
    assert state.metadata["source_chapters_dir"] == str(settings.project.script_chapters_dir)
    assert state.budget.used_text_calls == 0
    assert "script_import_facts" not in state.metadata
    assert "script_import_semantic_attempts" not in state.metadata
    print("script chapter import smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
