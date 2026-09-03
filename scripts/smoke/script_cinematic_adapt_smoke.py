from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow


ROOT = Path(__file__).resolve().parents[2]
SMOKE_ROOT = ROOT / ".tmp" / "script_cinematic_adapt_smoke"


def write_fixture() -> Path:
    if SMOKE_ROOT.exists():
        shutil.rmtree(SMOKE_ROOT)
    chapters_dir = SMOKE_ROOT / "chapters"
    chapters_dir.mkdir(parents=True)
    (chapters_dir / "chap0001_门外来客.txt").write_text(
        "林川把钥匙放在桌上。门外有人走近，他抬头看向门口。",
        encoding="utf-8",
        newline="\n",
    )
    config_path = SMOKE_ROOT / "config.yaml"
    config_path.write_text(
        """project:
  id: script_cinematic_adapt_smoke
  title: Script Cinematic Adapt Smoke
  script_chapters_dir: ./chapters
  episode_count: 1
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


async def main() -> None:
    source = "林川把钥匙放在桌上。门外有人走近，他抬头看向门口。"
    prompt = PromptStore().render("script_cinematic_adapt", raw_script=source)
    assert "你的任务不是扩写篇幅" in prompt
    assert "第一秒。灵能管蓝光从暗变亮" in prompt
    assert prompt.rstrip().endswith(source)

    assert PREGEN_NODES.index("script_cinematic_adapt") == PREGEN_NODES.index("script_import") + 1
    fangu_settings = load_settings(ROOT / "fangu.yaml")
    fangu_binding = fangu_settings.nodes["script_cinematic_adapt"]
    assert fangu_binding.model == "aibox:gemini-3.6-flash"
    assert fangu_binding.params["temperature"] == 0.35
    example_settings = load_settings(ROOT / "config.yaml.example")
    assert "script_import" in example_settings.nodes
    assert "script_cinematic_adapt" in example_settings.nodes

    settings = load_settings(write_fixture())
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    await workflow.run(project_dir, only="script_import")
    state = await workflow.run(project_dir, until="script_cinematic_adapt")

    assert "script_cinematic_adapt" in state.completed_nodes
    assert state.metadata["script_cinematic_adapted"] is True
    cinematic_text = ScriptContentRepository.load_content_ref(
        project_dir,
        state.script.novel_full["episode_001"],
    )
    assert cinematic_text is not None
    assert source in cinematic_text
    assert "门外先传来一声金属碰响" in cinematic_text

    output = json.loads(repo.layout.node_output_path(project_dir, "script_cinematic_adapt").read_text("utf-8"))
    assert output["cinematic_adapted"] is True
    assert output["episodes"]["episode_001"]["cinematic_script"] == cinematic_text
    print("script cinematic adapt smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
