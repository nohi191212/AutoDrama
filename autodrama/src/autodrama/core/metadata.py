from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectMetadata(BaseModel):
    episode_count: int = 1
    episode_duration_seconds: int = 30
    bgm_count: int = 3
    visual_style: str = "live_action"
    visual_style_label: str | None = None
    visual_style_prompt: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_state_metadata(cls, metadata: dict[str, Any]) -> "ProjectMetadata":
        known = {
            "episode_count",
            "episode_duration_seconds",
            "bgm_count",
            "visual_style",
            "visual_style_label",
            "visual_style_prompt",
        }
        payload = {key: metadata[key] for key in known if key in metadata}
        parsed = cls.model_validate(payload)
        parsed.extra = {key: value for key, value in metadata.items() if key not in known}
        return parsed


class GenerationTaskRegistry(BaseModel):
    schema_version: int = 1
    project_id: str | None = None
    updated_at: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class GenerationChecklist(BaseModel):
    project_id: str
    title: str
    updated_at: str
    instructions: str = ""
    episodes: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "GenerationChecklist",
    "GenerationTaskRegistry",
    "ProjectMetadata",
]
