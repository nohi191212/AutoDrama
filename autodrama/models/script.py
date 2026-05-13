"""Script-related data models — Character, Dialogue, Scene, Script."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Character(BaseModel):
    """A character in the drama."""

    name: str
    gender: str = "unknown"
    age_group: str = "adult"
    personality: str = ""
    voice_description: str = ""


class DialogueLine(BaseModel):
    """A single line of dialogue."""

    character: str
    text: str
    emotion: str = "neutral"
    timing_hint: float = 3.0  # estimated duration in seconds
    shot_type: str = "medium"  # close-up, medium, wide


class Scene(BaseModel):
    """One scene in the script."""

    scene_number: int
    location: str
    time_of_day: str = "day"
    atmosphere: str = "neutral"
    description: str = ""
    dialogue: list[DialogueLine] = Field(default_factory=list)
    narration: str = ""
    image_keywords: list[str] = Field(default_factory=list)


class Script(BaseModel):
    """Complete drama script."""

    title: str
    genre: str
    style: str = "realistic"
    logline: str = ""
    synopsis: str = ""
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
