"""Tests for config loading and validation."""

from pathlib import Path

from autodrama.config.loader import ConfigLoader


def test_load_valid_config(test_config_path: str) -> None:
    cfg = ConfigLoader.load(test_config_path)
    assert cfg.project.name == "AutoDramaTest"
    assert cfg.llm.default_provider == "openai"
    assert cfg.pipeline.fps == 10
    assert cfg.pipeline.resolution == (320, 240)


def test_save_default_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "new_config.yaml"
    ConfigLoader.save_default(p)
    assert p.exists()
    # Load it back
    cfg = ConfigLoader.load(p)
    assert cfg.project.name == "AutoDrama"


def test_env_var_interpolation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEST_KEY", "sk-test-12345")
    yaml_content = """\
project:
  name: "test"
  output_dir: "./out"
  temp_dir: "./tmp"
llm:
  default_provider: openai
  providers:
    openai:
      api_key: "${TEST_KEY}"
      model: "gpt-4o"
media:
  image:
    default_provider: openai
    providers:
      openai:
        api_key: ""
        model: "dall-e-3"
        size: "1024x1024"
        quality: "standard"
  audio:
    default_provider: edge_tts
    providers:
      edge_tts:
        voice: "test"
pipeline:
  max_retries: 1
  subtitle_enabled: true
  fps: 30
  resolution: [1920, 1080]
  transition_duration: 0.5
  default_shot_duration: 3.0
  cleanup_temp: true
logging:
  level: "INFO"
  file: ""
  format: "plain"
"""
    p = tmp_path / "env_config.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    cfg = ConfigLoader.load(p)
    assert cfg.llm.providers["openai"].api_key == "sk-test-12345"
