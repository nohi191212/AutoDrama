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
    def __init__(self, *, fail_after_novel_calls: int | None = None) -> None:
        self.prompts: dict[str, list[str]] = {}
        self.fail_after_novel_calls = fail_after_novel_calls
        self.novel_call_count = 0

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
        if node_name == "script_novel_episode":
            self.novel_call_count += 1
            if self.fail_after_novel_calls is not None and self.novel_call_count > self.fail_after_novel_calls:
                raise RuntimeError("intentional script_novel interruption")
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
    settings.project.episode_count = 3
    settings.project.episode_duration_seconds = 5
    repo = ProjectRepository(settings)
    project_id = f"script_novel_serial_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Script Novel Serial Smoke",
        raw_script=(
            "第1章，林舟发现合同异常。第2章，苏晚递来邮件截图。"
            "第3章，赵启施压。第4章，林舟准备反击。"
            "第5章，会议即将开始。第6章，证据浮出水面。"
        ),
        project_id=project_id,
        episode_count=3,
        episode_duration_seconds=5,
    )

    interrupted_provider = RecordingTextProvider(fail_after_novel_calls=2)
    workflow = PregenWorkflow(repo=repo, router=ScriptOnlyRouter(interrupted_provider))
    try:
        await workflow.run(project_dir, until="script_novel", force=True)
    except RuntimeError as exc:
        require("intentional script_novel interruption" in str(exc), f"Unexpected interruption: {exc}")
    else:
        raise AssertionError("Expected intentional script_novel interruption")

    partial_state = repo.load_state(project_dir)
    require(
        set(partial_state.script.novel_full) == {"episode_001", "episode_002", "episode_003"},
        f"Partial state should keep every episode key: {partial_state.script.novel_full}",
    )
    require(
        partial_state.script.novel_full["episode_003"] is False,
        f"Missing episode should be marked False: {partial_state.script.novel_full}",
    )
    require("script_novel" not in partial_state.completed_nodes, "Interrupted script_novel should not be completed")
    for episode_key in ("episode_001", "episode_002"):
        require(
            (project_dir / "assets" / "json" / "scripts" / "novel_full" / f"{episode_key}.json").exists(),
            f"Missing partial JSON for {episode_key}",
        )

    resume_provider = RecordingTextProvider()
    workflow = PregenWorkflow(repo=repo, router=ScriptOnlyRouter(resume_provider))
    state = await workflow.run(project_dir, until="script_novel")

    resume_novel_prompts = resume_provider.prompts.get("script_novel_episode", [])
    require(len(resume_novel_prompts) == 1, f"Resume should request one missing episode, got {len(resume_novel_prompts)}")
    require("episode_001" in resume_novel_prompts[0], "Resume prompt missing episode_001 previous context")
    require("episode_002" in resume_novel_prompts[0], "Resume prompt missing episode_002 previous context")

    extract_provider = RecordingTextProvider()
    workflow = PregenWorkflow(repo=repo, router=ScriptOnlyRouter(extract_provider))
    state = await workflow.run(project_dir, until="script_novel_extract")

    require(
        state.completed_nodes == ["script_outline", "script_novel", "director_prep", "script_novel_extract"],
        f"Unexpected completed nodes: {state.completed_nodes}",
    )
    require(
        set(state.script.novel_full) == {"episode_001", "episode_002", "episode_003"},
        "Novel full keys mismatch",
    )
    require(
        set(state.script.novel_extract) == {"episode_001", "episode_002", "episode_003"},
        "Novel extract keys mismatch",
    )
    require(state.metadata["script_novel_target_char_count"] == 300, "Unexpected target char count")
    require(state.metadata["director_prep_episode_keys"] == ["episode_001", "episode_002", "episode_003"], "Unexpected director prep keys")

    novel_prompts = interrupted_provider.prompts.get("script_novel_episode", [])
    require(len(novel_prompts) == 3, f"Interrupted run should attempt three prompts, got {len(novel_prompts)}")
    require("（暂无，当前是第一章。）" in novel_prompts[0], "First novel prompt missing empty previous context")
    require("episode_001" in novel_prompts[1], "Second novel prompt missing previous chapter context")

    extract_prompts = extract_provider.prompts.get("script_novel_extract", [])
    require(len(extract_prompts) == 1, f"Expected one script_novel_extract prompt, got {len(extract_prompts)}")
    require("全本完整小说" in extract_prompts[0], "script_novel_extract prompt missing full novel section")
    require("导演前期约束" in extract_prompts[0], "script_novel_extract prompt missing director prep section")
    require("episode_003" in extract_prompts[0], "script_novel_extract prompt missing episode key")

    node_output = json.loads((project_dir / "assets" / "json" / "nodes" / "script_novel.json").read_text(encoding="utf-8"))
    require(
        set(node_output["novel_full"]) == {"episode_001", "episode_002", "episode_003"},
        "script_novel node output mismatch",
    )
    extract_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "script_novel_extract.json").read_text(encoding="utf-8")
    )
    require(
        set(extract_output["novel_extract"]) == {"episode_001", "episode_002", "episode_003"},
        "script_novel_extract node output mismatch",
    )

    for episode_key in ("episode_001", "episode_002", "episode_003"):
        path = project_dir / "assets" / "json" / "scripts" / "novel_full" / f"{episode_key}.json"
        require(path.exists(), f"Missing per-episode novel JSON: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(payload["node_name"] == "script_novel", f"Unexpected node name for {episode_key}")
        require(payload["episode_key"] == episode_key, f"Unexpected episode key for {episode_key}")
        require(set(payload) <= {"node_name", "episode_key", "content", "source_outline_path"}, f"Unexpected novel payload keys for {episode_key}")
        require(payload["content"], f"Missing novel content for {episode_key}")
        extract_path = project_dir / "assets" / "json" / "scripts" / "novel_extract" / f"{episode_key}.json"
        require(extract_path.exists(), f"Missing per-episode extract JSON: {extract_path}")
        extract_payload = json.loads(extract_path.read_text(encoding="utf-8"))
        require(extract_payload["node_name"] == "script_novel_extract", f"Unexpected extract node name for {episode_key}")
        require(extract_payload["episode_key"] == episode_key, f"Unexpected extract episode key for {episode_key}")
        require(set(extract_payload) <= {"node_name", "episode_key", "content", "source_novel_full_path"}, f"Unexpected extract payload keys for {episode_key}")
        require(extract_payload["content"], f"Missing extract content for {episode_key}")

    print("script_novel_serial_smoke=ok")
    print(f"project_dir={project_dir}")
    print("episode_json_count=3")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
