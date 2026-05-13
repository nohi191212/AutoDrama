"""Tests for script generation module."""

from unittest.mock import MagicMock

from autodrama.script.generator import ScriptGenerator
from autodrama.llm.base import LLMResponse


def test_generate_returns_valid_script(mock_llm_provider: MagicMock) -> None:
    gen = ScriptGenerator(mock_llm_provider)
    script = gen.generate("A test concept")

    assert script.title == "Test Drama"
    assert script.genre == "thriller"
    assert len(script.scenes) == 1
    assert script.scenes[0].location == "office"
    assert len(script.scenes[0].dialogue) == 2
    assert script.scenes[0].dialogue[0].character == "Alice"


def test_generate_passes_genre_to_prompt(mock_llm_provider: MagicMock) -> None:
    gen = ScriptGenerator(mock_llm_provider)
    gen.generate("A love story", params={"genre": "romance"})

    call_args = mock_llm_provider.chat_with_structured_output.call_args
    messages = call_args[1]["messages"]
    system = messages[0]["content"]
    # Should include genre-specific prompt
    assert "emotional tension" in system or "intimate" in system


def test_generate_passes_style_and_scenes(mock_llm_provider: MagicMock) -> None:
    gen = ScriptGenerator(mock_llm_provider)
    gen.generate("A comedy", params={"style": "anime", "num_scenes": 5})

    call_args = mock_llm_provider.chat_with_structured_output.call_args
    messages = call_args[1]["messages"]
    user_prompt = messages[1]["content"]
    assert "anime" in user_prompt
    assert "5 scenes" in user_prompt
