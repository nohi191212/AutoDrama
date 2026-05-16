from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScriptBundle(BaseModel):
    raw_script: str
    outline: str | None = None
    episode_outlines: dict[str, str] = Field(default_factory=dict)
    detailed_script: dict[str, str] = Field(default_factory=dict)
    final_script: dict[str, str] = Field(default_factory=dict)
    revision_notes: list[str] = Field(default_factory=list)


class RoleAudio(BaseModel):
    id: str
    role_id: str
    emotion: str
    desc: str
    sample_text: str | None = None
    asset_id: str | None = None
    asset_path: str | None = None


class Role(BaseModel):
    id: str
    name: str
    intro: str
    personality: str | None = None
    voice_summary: str | None = None
    aliases: list[str] = Field(default_factory=list)
    audio: dict[str, RoleAudio] = Field(default_factory=dict)


class BudgetState(BaseModel):
    max_total_cny: float = 100
    used_estimated_cny: float = 0
    max_image_count: int = 20
    max_video_seconds: float = 45
    max_music_count: int = 5
    max_text_calls: int = 200
    used_text_calls: int = 0


class ProjectState(BaseModel):
    project_id: str
    title: str
    raw_script: str
    current_node: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    script: ScriptBundle
    roles: dict[str, Role] = Field(default_factory=dict)
    budget: BudgetState = Field(default_factory=BudgetState)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def mark_completed(self, node_name: str) -> None:
        if node_name not in self.completed_nodes:
            self.completed_nodes.append(node_name)
        self.current_node = node_name
        self.updated_at = datetime.now()


class NodeRecord(BaseModel):
    node_name: str
    provider: str
    model: str | None = None
    output_path: str
    created_at: datetime = Field(default_factory=datetime.now)


class ScriptOutlineOutput(BaseModel):
    logline: str
    outline: str
    episode_count: int = 1
    target_duration_seconds: int = 30
    episode_outlines: dict[str, str] = Field(default_factory=dict)


class ScriptDetailOutput(BaseModel):
    detailed_script: dict[str, str]


class ScriptPolishOutput(BaseModel):
    final_script: dict[str, str]
    revision_notes: list[str] = Field(default_factory=list)


class RoleDesignItem(BaseModel):
    name: str
    intro: str
    personality: str | None = None
    aliases: list[str] = Field(default_factory=list)


class RoleDesignOutput(BaseModel):
    roles: list[RoleDesignItem]


class RoleVoiceItem(BaseModel):
    role_name: str
    emotion: Literal["normal", "angry", "sad", "happy", "tense", "whisper", "other"] | str
    desc: str
    sample_text: str | None = Field(
        default=None,
        description=(
            "Voice-design reference text in first person. It should be 2-4 sentences in the format "
            "'identity self-introduction + early inner monologue', reflect the role identity/personality/tone, "
            "and avoid late key plot, final twists, key evidence, endings, or outcome spoilers."
        ),
    )


class RoleVoiceDesignOutput(BaseModel):
    role_voices: list[RoleVoiceItem]


class RoleVoiceGenerationItem(BaseModel):
    role_id: str
    role_name: str
    emotion: str
    audio_id: str
    generation_method: Literal["design", "clone"]
    voice: str
    source_audio_id: str | None = None
    source_audio_path: str | None = None
    voice_prompt: str
    preview_text: str
    preview_audio_path: str | None = None
    provider: str
    model: str
    target_model: str | None = None
    sample_rate: int | None = None
    response_format: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class RoleVoiceGenerationOutput(BaseModel):
    generated_voices: list[RoleVoiceGenerationItem]
