from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import BGMDesignOutput, ProjectState, ScriptBundle  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


T = TypeVar("T", bound=BaseModel)


class BGMCountTextProvider:
    name = "bgm-count-smoke"
    model = "dummy-text"

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del temperature
        metadata = metadata or {}
        if schema is not BGMDesignOutput:
            raise AssertionError(f"Unexpected schema: {schema}")

        bgm_count = int(metadata["bgm_count"])
        if f"BGM数量：{bgm_count}" not in prompt:
            raise AssertionError("BGM count was not rendered into the prompt")
        if f"必须正好生成 {bgm_count} 首 BGM" not in prompt:
            raise AssertionError("Prompt does not require the exact BGM count")

        return schema.model_validate(
            {
                "bgms": [
                    {
                        "name": f"烟雾测试{index + 1}",
                        "mood": "测试情绪",
                        "prompt": "instrumental cinematic background music, no vocals",
                        "usage_hint": "smoke",
                    }
                    for index in range(bgm_count)
                ]
            }
        )


class BGMCountRouter:
    def __init__(self) -> None:
        self.provider = BGMCountTextProvider()

    def text(self, purpose: str) -> BGMCountTextProvider:
        if purpose != "bgm_plan":
            raise ValueError(f"Unexpected text purpose: {purpose}")
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=BGMCountRouter())

    project_dir = ROOT_DIR / ".tmp" / "smoke" / "bgm_count"
    repo._create_project_dirs(project_dir)
    state = ProjectState(
        project_id="bgm_count_smoke",
        title="BGM Count Smoke",
        raw_script="Smoke",
        script=ScriptBundle(
            raw_script="Smoke",
            novel_extract={"episode_001": "assets/json/scripts/novel_extract/episode_001.json"},
        ),
    )
    repo.write_json(
        project_dir / "assets" / "json" / "scripts" / "novel_extract" / "episode_001.json",
        {
            "node_name": "script_novel_extract",
            "episode_key": "episode_001",
            "content": "林舟发现合同异常，并在会议上公开反击赵启。",
            "source_novel_full_path": "assets/json/scripts/novel_full/episode_001.json",
        },
    )
    workflow._apply_script_plan_settings(state)

    await workflow._run_bgm_design(project_dir, state)
    expected_count = settings.project.bgm_count
    output_path = project_dir / "assets" / "json" / "nodes" / "bgm_design.json"

    assert len(state.bgms) == expected_count
    assert output_path.exists()

    print("bgm_count_smoke=ok")
    print(f"bgm_count={expected_count}")
    print(f"bgm_items={len(state.bgms)}")
    print(f"output_path={output_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
