from pathlib import Path

from autodrama.config import load_settings


def test_load_settings_resolves_output_root(tmp_path: Path) -> None:
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "chap0001_demo.txt").write_text("测试章节。", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
output:
  root_dir: ./outputs
project:
  id: demo
  title: Demo
  script_chapters_dir: ./chapters
  episode_count: 4
  episode_duration_seconds: 45
routing:
  text:
    script: fake
    role: fake
providers: {}
""",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.output.root_dir == tmp_path / "outputs"
    assert settings.project.script_chapters_dir == chapters_dir
    assert settings.project.episode_count == 4
    assert settings.project.episode_duration_seconds == 45
    assert settings.provider_for("text", "script") == "fake"
