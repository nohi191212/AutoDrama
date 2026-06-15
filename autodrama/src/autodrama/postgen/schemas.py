from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


POSTGEN_EDIT_PLAN_SCHEMA_VERSION = "autodrama.postgen.edit_plan.v1"


class PostgenSourceClip(BaseModel):
    episode_key: str
    shot_id: str
    shot_index: int
    title: str | None = None
    source_path: str
    duration_seconds: float
    dialogue_lines: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    content: str | None = None
    video_prompt: str | None = None
    camera_movement: str | None = None
    transition_hint: str | None = None


class PostgenTransition(BaseModel):
    type: Literal["cut"] = "cut"


class PostgenTimelineItem(BaseModel):
    clip_id: str
    shot_id: str
    source_in: float = 0.0
    source_out: float
    speed: float = 1.0
    transition_after: PostgenTransition = Field(default_factory=PostgenTransition)
    rationale: str | None = None


class PostgenOutputSpec(BaseModel):
    path: str
    width: int = 720
    height: int = 1280
    fps: int = 25
    burn_subtitles: bool = True
    audio: bool = True


class PostgenAudioLayer(BaseModel):
    layer_id: str
    layer_type: Literal["dialogue", "bgm"]
    source_path: str
    start_time: float
    duration_seconds: float | None = None
    volume: float = 1.0
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostgenSubtitleCue(BaseModel):
    index: int
    start_time: float
    end_time: float
    text: str
    role_name: str | None = None
    shot_id: str | None = None


class PostgenEditPlan(BaseModel):
    schema_version: Literal["autodrama.postgen.edit_plan.v1"] = POSTGEN_EDIT_PLAN_SCHEMA_VERSION
    episode_key: str
    source_clips: list[PostgenSourceClip] = Field(default_factory=list)
    timeline: list[PostgenTimelineItem] = Field(default_factory=list)
    output: PostgenOutputSpec
    audio_layers: list[PostgenAudioLayer] = Field(default_factory=list)
    subtitle_cues: list[PostgenSubtitleCue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostgenSourceCollectItem(BaseModel):
    episode_key: str
    source_clip_count: int
    path: str
    warnings: list[str] = Field(default_factory=list)


class PostgenSourceCollectOutput(BaseModel):
    episodes: list[PostgenSourceCollectItem]


class PostgenEditPlanGenerationItem(BaseModel):
    episode_key: str
    raw_plan_path: str
    source_clip_count: int
    timeline_count: int
    provider: str
    model: str | None = None


class PostgenEditPlanGenerationOutput(BaseModel):
    generated_plans: list[PostgenEditPlanGenerationItem]


class PostgenEditPlanValidationItem(BaseModel):
    episode_key: str
    raw_plan_path: str
    validated_plan_path: str
    timeline_count: int
    estimated_duration_seconds: float
    warnings: list[str] = Field(default_factory=list)


class PostgenEditPlanValidationOutput(BaseModel):
    validated_plans: list[PostgenEditPlanValidationItem]


class PostgenCompositionItem(BaseModel):
    episode_key: str
    output_video_path: str
    validated_plan_path: str
    estimated_duration_seconds: float
    actual_duration_seconds: float | None = None
    clip_count: int
    ffmpeg_path: str


class PostgenCompositionOutput(BaseModel):
    composed_videos: list[PostgenCompositionItem]
    skipped_episodes: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "POSTGEN_EDIT_PLAN_SCHEMA_VERSION",
    "PostgenAudioLayer",
    "PostgenCompositionItem",
    "PostgenCompositionOutput",
    "PostgenEditPlan",
    "PostgenEditPlanGenerationItem",
    "PostgenEditPlanGenerationOutput",
    "PostgenEditPlanValidationItem",
    "PostgenEditPlanValidationOutput",
    "PostgenOutputSpec",
    "PostgenSourceClip",
    "PostgenSourceCollectItem",
    "PostgenSourceCollectOutput",
    "PostgenSubtitleCue",
    "PostgenTimelineItem",
    "PostgenTransition",
]
