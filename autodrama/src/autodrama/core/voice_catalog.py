from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VoiceCatalogSampleItem(BaseModel):
    emotion: str
    sample_text: str
    asset_path: str
    sample_hash: str
    provider: str
    model: str
    voice_type: str
    voice_resource_id: str | None = None
    audio_params: dict[str, Any] = Field(default_factory=dict)


class VoiceCatalogProfile(BaseModel):
    summary: str
    gender_presentation: str | None = None
    age_impression: str | None = None
    texture: list[str] = Field(default_factory=list)
    performance_style: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    best_role_types: list[str] = Field(default_factory=list)
    avoid_role_types: list[str] = Field(default_factory=list)
    emotion_quality: dict[str, float] = Field(default_factory=dict)


class VoiceCatalogVoiceItem(BaseModel):
    voice_label: str
    voice_type: str
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    voice_catalog_key: str
    official: dict[str, Any] = Field(default_factory=dict)
    samples: dict[str, VoiceCatalogSampleItem] = Field(default_factory=dict)
    omni_profile: VoiceCatalogProfile | None = None
    profile_hash: str | None = None


class VoiceCatalogManifest(BaseModel):
    schema_version: int = 1
    catalog_version: str
    provider: str
    model: str
    sample_emotions: list[str]
    voices: list[VoiceCatalogVoiceItem]


class VoiceCandidateItem(BaseModel):
    voice_label: str
    voice_type: str
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    voice_catalog_key: str
    score: float | None = None
    reason: str
    profile_summary: str | None = None
    sample_paths: dict[str, str] = Field(default_factory=dict)


class VoiceSelectShortlistCandidate(BaseModel):
    voice_label: str
    voice_type: str
    score: float | None = None
    reason: str


class VoiceSelectShortlistOutput(BaseModel):
    candidates: list[VoiceSelectShortlistCandidate] = Field(default_factory=list)


class RoleVoiceSelectionItem(BaseModel):
    role_id: str
    role_name: str
    selected_voice_label: str
    selected_voice_type: str
    selected_voice_resource_id: str | None = None
    selected_voice_model_family: str | None = None
    selected_voice_catalog_key: str
    selected_reason: str
    selection_source: Literal[
        "manual_override",
        "cache",
        "text_shortlist",
        "omni_judge",
        "catalog_heuristic",
        "provider_fallback",
    ] | str = "omni_judge"
    top_candidates: list[VoiceCandidateItem] = Field(default_factory=list)
    role_design_hash: str
    catalog_version: str
    catalog_hash: str
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class VoiceSelectOutput(BaseModel):
    selected_voices: list[RoleVoiceSelectionItem]


class VoiceSelectAudioJudgeRankedItem(BaseModel):
    voice_label: str
    voice_type: str
    score: float | None = None
    reason: str


class VoiceSelectAudioJudgeOutput(BaseModel):
    selected_voice_type: str
    selected_voice_label: str
    selected_reason: str
    ranked_candidates: list[VoiceSelectAudioJudgeRankedItem] = Field(default_factory=list)


__all__ = [
    "RoleVoiceSelectionItem",
    "VoiceCandidateItem",
    "VoiceCatalogManifest",
    "VoiceCatalogProfile",
    "VoiceCatalogSampleItem",
    "VoiceCatalogVoiceItem",
    "VoiceSelectAudioJudgeOutput",
    "VoiceSelectAudioJudgeRankedItem",
    "VoiceSelectOutput",
    "VoiceSelectShortlistCandidate",
    "VoiceSelectShortlistOutput",
]
