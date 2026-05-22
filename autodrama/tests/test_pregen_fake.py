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
  episode_count: 3
  episode_duration_seconds: 45
output:
  root_dir: ./outputs
  project_dir_template: "{date}_{slug}"
routing:
  text:
    script: fake
    role: fake
  audio:
    speech: fake
providers: {}
""",
        encoding="utf-8",
    )
    return config


def test_pregen_runs_integrated_role_pipeline(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path))
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    state = asyncio.run(workflow.run(project_dir))

    assert state.completed_nodes == [
        "script_outline",
        "script_novel",
        "script_novel_extract",
        "role_extract",
        "role_design",
        "role_voice_generation",
        "role_appearance_generation",
        "prop_extract",
        "prop_design",
        "prop_generation",
        "script_compress",
        "layout_design",
        "layout_dedupe_review",
        "layout_image_generation",
        "bgm_design",
        "bgm_generation",
    ]
    assert state.metadata["episode_count"] == 3
    assert state.metadata["episode_duration_seconds"] == 45
    assert set(state.script.episode_outlines) == {"episode_001", "episode_002", "episode_003"}
    assert set(state.script.novel_full) == {"episode_001", "episode_002", "episode_003"}
    assert set(state.script.novel_extract) == {"episode_001", "episode_002", "episode_003"}
    assert all(str(value).startswith("assets/json/scripts/outlines/") for value in state.script.episode_outlines.values())
    assert all(str(value).startswith("assets/json/scripts/novel_full/") for value in state.script.novel_full.values())
    assert all(str(value).startswith("assets/json/scripts/novel_extract/") for value in state.script.novel_extract.values())
    assert "role_林舟".lower() in {key.lower() for key in state.roles}
    assert any(role.audio for role in state.roles.values())
    assert all(audio.asset_id for role in state.roles.values() for audio in role.audio.values())
    assert all(audio.asset_path for role in state.roles.values() for audio in role.audio.values())
    assert state.roles["role_林舟"].audio["normal"].asset_id.startswith("fake_ad_")
    assert state.roles["role_林舟"].audio["tense"].asset_id.startswith("fake_clone_ad_")
    assert state.roles["role_林舟"].appearances["base"].design_image_asset_path
    assert state.roles["role_林舟"].appearances["base"].intro_video_asset_path
    assert state.props
    assert state.layouts
    assert state.bgms
    assert (project_dir / "assets" / "json" / "nodes" / "role_extract.json").exists()
    assert (project_dir / "assets" / "json" / "nodes" / "role_design.json").exists()
    assert (project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json").exists()
    assert not hasattr(state, "storyboards")
    assert (settings.output.root_dir / "current_project.json").exists()
