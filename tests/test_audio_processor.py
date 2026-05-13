"""Tests for pydub-based audio processing."""

from pathlib import Path

from pydub import AudioSegment

from autodrama.audio.processor import AudioProcessor
from autodrama.config.loader import ConfigLoader
from autodrama.models.media import AudioAsset, BackgroundMusic


def test_concatenate_empty_assets(test_config_path: str) -> None:
    cfg = ConfigLoader.load(test_config_path)
    ap = AudioProcessor(cfg)
    result = ap.concatenate([])
    assert len(result) == 0


def test_concatenate_silent_assets(silent_audio_path: str, test_config_path: str) -> None:
    cfg = ConfigLoader.load(test_config_path)
    ap = AudioProcessor(cfg)

    asset = AudioAsset(
        scene_number=1,
        shot_index=0,
        character="Alice",
        text="Hello",
        audio_path=silent_audio_path,
        duration_seconds=0.1,
    )
    result = ap.concatenate([asset, asset])
    # Two 100ms clips = ~200ms
    assert 150 <= len(result) <= 300


def test_mix_with_music(silent_audio_path: str, test_config_path: str) -> None:
    cfg = ConfigLoader.load(test_config_path)
    ap = AudioProcessor(cfg)

    dialogue = AudioSegment.silent(duration=500)
    music = BackgroundMusic(
        track_name="test_bgm",
        file_path=silent_audio_path,
        volume=0.1,
        loop=True,
    )
    mixed = ap.mix_with_music(dialogue, music)
    # Should produce audio of same length as dialogue
    assert len(mixed) == 500


def test_concatenate_missing_file_uses_silence(test_config_path: str) -> None:
    cfg = ConfigLoader.load(test_config_path)
    ap = AudioProcessor(cfg)

    asset = AudioAsset(
        scene_number=1,
        shot_index=0,
        character="Alice",
        text="Hello",
        audio_path="/nonexistent/file.mp3",
        duration_seconds=0.5,
    )
    result = ap.concatenate([asset])
    assert len(result) == 500  # 0.5s of silence
