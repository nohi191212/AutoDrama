"""Shared test fixtures."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image as PILImage

# Ensure the package root is on sys.path
_this_dir = Path(__file__).resolve().parent
_project_root = _this_dir.parent
import sys

sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Fake / mock media files
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_image_path(tmp_path: Path) -> str:
    """Create a tiny (2×2) PNG for testing image/video pipelines."""
    img = PILImage.new("RGB", (2, 2), color=(255, 0, 0))
    path = tmp_path / "tiny.png"
    img.save(path)
    return str(path)


@pytest.fixture
def silent_audio_path(tmp_path: Path) -> str:
    """Create a minimal silent MP3 for testing audio pipelines."""
    from pydub import AudioSegment

    seg = AudioSegment.silent(duration=100)  # 100 ms
    path = tmp_path / "silent.mp3"
    seg.export(path, format="mp3")
    return str(path)


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------

MOCK_SCRIPT_JSON = json.dumps({
    "title": "Test Drama",
    "genre": "thriller",
    "style": "realistic",
    "logline": "A test drama.",
    "synopsis": "Just a test.",
    "characters": [
        {"name": "Alice", "gender": "female", "personality": "brave", "voice_description": "clear"},
        {"name": "Bob", "gender": "male", "personality": "cautious", "voice_description": "deep"},
    ],
    "scenes": [
        {
            "scene_number": 1,
            "location": "office",
            "time_of_day": "night",
            "atmosphere": "tense",
            "description": "A dimly lit office with rain outside.",
            "dialogue": [
                {"character": "Alice", "text": "We need to leave now.", "emotion": "urgent", "shot_type": "close-up"},
                {"character": "Bob", "text": "I agree.", "emotion": "neutral", "shot_type": "medium"},
            ],
            "narration": "",
            "image_keywords": ["office", "rain", "night"],
        },
    ],
}, ensure_ascii=False)

MOCK_SCENE_PLANS_JSON = json.dumps([
    {
        "scene_number": 1,
        "background_music": "tense ambient",
        "shots": [
            {
                "shot_index": 0,
                "camera_angle": "eye-level",
                "frame_type": "close-up",
                "subject_focus": "Alice looking worried",
                "background_description": "dark office with rain-streaked window",
                "lighting": "low-key",
                "image_prompt": "A worried woman in a dark office, rain-streaked window, low-key cinematic lighting, 4K",
                "negative_prompt": "daylight, smiling",
            },
            {
                "shot_index": 1,
                "camera_angle": "eye-level",
                "frame_type": "medium",
                "subject_focus": "Bob standing near the door",
                "background_description": "dark office corridor",
                "lighting": "moody",
                "image_prompt": "A cautious man standing near a door in a dark office corridor, moody cinematic lighting, 4K",
                "negative_prompt": "daylight, smiling",
            },
        ],
    },
], ensure_ascii=False)


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    """Returns a mock LLMProvider that returns valid test Script and ScenePlan JSON."""
    from autodrama.llm.base import LLMResponse

    mock = MagicMock()

    # Default: return script JSON
    mock.chat_with_structured_output.return_value = LLMResponse(
        content=MOCK_SCRIPT_JSON,
        model="mock-model",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    mock.chat.return_value = LLMResponse(
        content=MOCK_SCRIPT_JSON,
        model="mock-model",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )

    return mock


@pytest.fixture
def sample_script():
    """Return a pre-built Script object for tests that don't need the LLM."""
    from autodrama.models.script import Character, DialogueLine, Scene, Script

    return Script(
        title="Test Drama",
        genre="thriller",
        style="realistic",
        characters=[
            Character(name="Alice", gender="female", personality="brave"),
            Character(name="Bob", gender="male", personality="cautious"),
        ],
        scenes=[
            Scene(
                scene_number=1,
                location="office",
                time_of_day="night",
                atmosphere="tense",
                description="A dimly lit office.",
                dialogue=[
                    DialogueLine(character="Alice", text="Run!", emotion="urgent", timing_hint=1.5),
                    DialogueLine(character="Bob", text="Wait.", emotion="neutral", timing_hint=1.0),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Config override
# ---------------------------------------------------------------------------


@pytest.fixture
def test_config_path(tmp_path: Path) -> str:
    """Write a minimal config.yaml for testing."""
    content = """\
project:
  name: "AutoDramaTest"
  output_dir: "{tmp}/output"
  temp_dir: "{tmp}/temp"

llm:
  default_provider: openai
  providers:
    openai:
      api_key: ""
      model: "gpt-4o"
      max_tokens: 1024
      temperature: 0.7

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
        voice: "zh-CN-XiaoxiaoNeural"
        rate: "+0%"
        volume: "+0%"

pipeline:
  max_retries: 2
  parallel_media_generation: false
  subtitle_enabled: false
  fps: 10
  resolution: [320, 240]
  transition_duration: 0.0
  default_shot_duration: 1.0
  cleanup_temp: true

logging:
  level: "WARNING"
  file: ""
  format: "plain"
""".format(tmp=str(tmp_path).replace("\\", "/"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content, encoding="utf-8")
    return str(config_path)
