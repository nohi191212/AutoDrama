"""Video-related data models — VideoSegment, FinalVideo."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VideoSegment(BaseModel):
    """A single video segment (image + audio) ready for composition."""

    scene_number: int
    shot_index: int
    image_path: str
    audio_path: str
    duration: float
    transition_in: str = "fade"
    transition_out: str = "fade"
    subtitle_lines: list[str] = Field(default_factory=list)


class FinalVideo(BaseModel):
    """Metadata for the rendered final video."""

    output_path: str
    segments: list[VideoSegment] = Field(default_factory=list)
    total_duration: float = 0.0
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 30
