from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from autodrama.config import OutputSettings, ProjectSettings, Settings
from autodrama.providers.local.mock.fake import FakeTextProvider
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.script_nodes import ScriptNovelExtractNode


class RouterStub:
    def __init__(self, provider: FakeTextProvider) -> None:
        self.provider = provider

    def text(self, capability: str, *, node_name: str):
        assert capability == "script"
        assert node_name == "script_novel_extract"
        return self.provider


async def main() -> None:
    output_root = Path(".tmp/script_novel_extract_parallel_smoke")
    if output_root.exists():
        shutil.rmtree(output_root)

    settings = Settings(
        project=ProjectSettings(id="script_novel_extract_parallel_smoke", title="Smoke", episode_count=3),
        output=OutputSettings(root_dir=output_root),
        generation={
            "visual_style": {
                "schema_version": 2,
                "medium": "smoke_fixture",
                "render_engine_language": ["Deterministic script smoke fixture."],
            }
        },
    )
    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Smoke",
        raw_script="Smoke raw script",
        project_id="script_novel_extract_parallel_smoke",
        episode_count=3,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    script_contents = ScriptContentRepository(repo, repo.layout)
    for index in range(1, 4):
        episode_key = f"episode_{index:03d}"
        state.script.novel_full[episode_key] = script_contents.write_content(
            project_dir,
            "novel_full",
            episode_key,
            node_name="script_novel",
            content=f"{episode_key} full script text with a distinct event for compression.",
        )
    repo.save_state(project_dir, state)

    provider = FakeTextProvider()
    provider.model_binding = SimpleNamespace(params={"script_novel_extract_concurrency": 2})
    node = ScriptNovelExtractNode(
        repo=repo,
        layout=repo.layout,
        router=RouterStub(provider),
        script_service=ScriptService(PromptStore()),
        script_contents=script_contents,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        force_getter=lambda: True,
    )
    state = await node.run(project_dir, state)

    refs = state.script.novel_extract
    assert set(refs) == {"episode_001", "episode_002", "episode_003"}
    assert all(str(value).startswith("assets/json/scripts/novel_extract/") for value in refs.values())
    assert state.budget.used_text_calls == 3
    assert state.metadata["script_novel_extract_concurrency"] == 2
    for episode_key, ref in refs.items():
        payload = json.loads((project_dir / str(ref)).read_text(encoding="utf-8"))
        assert payload["node_name"] == "script_novel_extract"
        assert payload["episode_key"] == episode_key
        assert payload["content"]
        assert payload["source_novel_full_path"] == state.script.novel_full[episode_key]

    node_output = json.loads((project_dir / "assets/json/nodes/script_novel_extract.json").read_text(encoding="utf-8"))
    assert set(node_output["novel_extract"]) == set(refs)
    print(json.dumps({"episodes": sorted(refs), "text_calls": state.budget.used_text_calls}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
