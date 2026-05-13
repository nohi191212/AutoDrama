"""Scene-planning data models — ShotComposition, ScenePlan."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShotComposition(BaseModel):
    """Visual composition for one shot."""

    shot_index: int
    camera_angle: str = "eye-level"  # eye-level, high-angle, low-angle, dutch
    frame_type: str = "medium"  # close-up, medium, long, wide, extreme-wide
    subject_focus: str = ""
    background_description: str = ""
    lighting: str = "natural"
    image_prompt: str = ""  # full image-generation prompt
    negative_prompt: str = ""


class ScenePlan(BaseModel):
    """Production plan for a single scene."""

    scene_number: int
    background_music: str = ""
    sound_effects: list[str] = Field(default_factory=list)
    shots: list[ShotComposition] = Field(default_factory=list)
