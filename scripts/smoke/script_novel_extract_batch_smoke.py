from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingTextProvider(FakeTextProvider):
    def __init__(self) -> None:
        self.prompts: dict[str, list[str]] = {}
        self.metadata: dict[str, list[dict[str, Any]]] = {}

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        metadata = metadata or {}
        node_name = str(metadata.get("node_name") or schema.__name__)
        self.prompts.setdefault(node_name, []).append(prompt)
        self.metadata.setdefault(node_name, []).append(dict(metadata))
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class ScriptOnlyRouter:
    def __init__(self, provider: RecordingTextProvider) -> None:
        self.provider = provider

    def text(self, purpose: str) -> RecordingTextProvider:
        del purpose
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 6
    settings.project.episode_duration_seconds = 5
    repo = ProjectRepository(settings)
    project_id = f"script_novel_extract_batch_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Script Novel Extract Batch Smoke",
        raw_script=(
            "第1章，林舟发现合同异常。第2章，苏晚递来邮件截图。"
            "第3章，赵启施压。第4章，林舟准备反击。"
            "第5章，会议即将开始。第6章，证据浮出水面。"
            "第7章，赵启试图狡辩。第8章，同事态度转变。"
            "第9章，林舟补齐证据链。第10章，会议进入关键时刻。"
            "第11章，苏晚确认备份。第12章，林舟发起反击。"
        ),
        project_id=project_id,
        episode_count=6,
        episode_duration_seconds=5,
    )

    provider = RecordingTextProvider()
    workflow = PregenWorkflow(repo=repo, router=ScriptOnlyRouter(provider))
    state = await workflow.run(project_dir, until="script_novel_extract", force=True)

    expected_keys = {f"episode_{index:03d}" for index in range(1, 7)}
    require(state.completed_nodes == ["script_outline", "script_novel", "script_novel_extract"], "Unexpected nodes")
    require(set(state.script.novel_full) == expected_keys, "Novel full keys mismatch")
    require(set(state.script.novel_extract) == expected_keys, "Novel extract keys mismatch")
    require(
        all(str(value).startswith("assets/json/scripts/novel_full/") for value in state.script.novel_full.values()),
        "Novel full state should store per-episode JSON paths",
    )
    require(
        all(str(value).startswith("assets/json/scripts/novel_extract/") for value in state.script.novel_extract.values()),
        "Novel extract state should store per-episode JSON paths",
    )
    require(state.metadata["script_novel_extract_batch_size"] == 5, "Unexpected extract batch size")

    extract_prompts = provider.prompts.get("script_novel_extract", [])
    extract_metadata = provider.metadata.get("script_novel_extract", [])
    require(len(extract_prompts) == 2, f"Expected two extract batch prompts, got {len(extract_prompts)}")
    require(extract_metadata[0]["batch_episode_keys"] == [f"episode_{index:03d}" for index in range(1, 6)], "First batch keys mismatch")
    require(extract_metadata[1]["batch_episode_keys"] == ["episode_006"], "Second batch keys mismatch")
    require("（暂无，当前是第一批。）" in extract_prompts[0], "First batch should not have previous extract")
    require("episode_001" in extract_prompts[1], "Second batch prompt missing previous extract")
    require("episode_006" in extract_prompts[1], "Second batch prompt missing current hint")

    node_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "script_novel_extract.json").read_text(encoding="utf-8")
    )
    require(set(node_output["novel_extract"]) == expected_keys, "script_novel_extract node output mismatch")

    print("script_novel_extract_batch_smoke=ok")
    print(f"project_dir={project_dir}")
    print("extract_batch_count=2")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
