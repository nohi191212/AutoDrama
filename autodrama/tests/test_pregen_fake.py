import asyncio
from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.pregen import PregenWorkflow


def write_config(tmp_path: Path) -> Path:
    (tmp_path / "story.md").write_text("一个被陷害的年轻人在会议上拿出证据反击。", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
project:
  id: test_project
  title: 测试短片
  script_outline_file: ./story.md
output:
  root_dir: ./outputs
  project_dir_template: "{date}_{slug}"
routing:
  text:
    script: fake
    role: fake
providers: {}
""",
        encoding="utf-8",
    )
    return config


def test_pregen_stops_at_role_voice_design(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path))
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    state = asyncio.run(workflow.run(project_dir))

    assert state.completed_nodes == [
        "script_outline",
        "script_detail",
        "script_polish",
        "role_design",
        "role_voice_design",
    ]
    assert state.script.final_script
    assert "role_林舟".lower() in {key.lower() for key in state.roles}
    assert any(role.audio for role in state.roles.values())
    assert (project_dir / "assets" / "json" / "nodes" / "role_voice_design.json").exists()
    assert (settings.output.root_dir / "current_project.json").exists()
