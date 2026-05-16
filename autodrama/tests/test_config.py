from pathlib import Path

from autodrama.config import load_settings


def test_load_settings_resolves_output_root(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
output:
  root_dir: ./outputs
project:
  id: demo
  title: Demo
  script_outline_file: ./story.md
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
    assert settings.project.script_outline_file == tmp_path / "story.md"
    assert settings.project.episode_count == 4
    assert settings.project.episode_duration_seconds == 45
    assert settings.provider_for("text", "script") == "fake"
