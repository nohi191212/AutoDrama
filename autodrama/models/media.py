"""Media asset data models — ImageAsset, AudioAsset, BackgroundMusic."""

from __future__ import annotations

from pydantic import BaseModel


class ImageAsset(BaseModel):
    """A generated image file."""

    scene_number: int
    shot_index: int
    prompt: str
    image_path: str = ""
    width: int = 1920
    height: int = 1080
    provider: str = ""


class AudioAsset(BaseModel):
    """A generated audio / TTS file."""

    scene_number: int
    shot_index: int
    character: str = ""
    text: str
    audio_path: str = ""
    duration_seconds: float = 0.0
    provider: str = ""


class BackgroundMusic(BaseModel):
    """Background music track reference."""

    track_name: str
    file_path: str
    volume: float = 0.3
    loop: bool = True
