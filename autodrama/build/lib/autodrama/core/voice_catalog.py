from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


VoiceLanguage = Literal["zh", "en", "ja", "es", "other", "unspecified"]
VoiceGenderPresentation = Literal["female", "male", "neutral", "unspecified"]
VoiceAgeImpression = Literal[
    "child",
    "teen",
    "young_adult",
    "adult",
    "mature",
    "elderly",
    "unspecified",
]
VoiceMetadataSource = Literal["official_metadata", "audio_judge", "manual_review", "migration", "unspecified"]

_OFFICIAL_LANGUAGE_MAP: dict[str, VoiceLanguage] = {
    "unspecified": "unspecified",
    "other": "other",
    "zh": "zh",
    "zh-cn": "zh",
    "cmn": "zh",
    "中文": "zh",
    "汉语": "zh",
    "chinese": "zh",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "英语": "en",
    "english": "en",
    "ja": "ja",
    "ja-jp": "ja",
    "jp": "ja",
    "日语": "ja",
    "日文": "ja",
    "japanese": "ja",
    "es": "es",
    "es-es": "es",
    "西语": "es",
    "西班牙语": "es",
    "spanish": "es",
}
_OFFICIAL_GENDER_MAP: dict[str, VoiceGenderPresentation] = {
    "unspecified": "unspecified",
    "female": "female",
    "woman": "female",
    "girl": "female",
    "女": "female",
    "女性": "female",
    "女声": "female",
    "male": "male",
    "man": "male",
    "boy": "male",
    "男": "male",
    "男性": "male",
    "男声": "male",
    "neutral": "neutral",
    "nonbinary": "neutral",
    "中性": "neutral",
}
_LEGACY_AGE_MAP: dict[str, VoiceAgeImpression] = {
    "child": "child",
    "teen": "teen",
    "young_adult": "young_adult",
    "young_adult_to_adult": "young_adult",
    "adult": "adult",
    "mature": "mature",
    "elderly": "elderly",
    "unspecified": "unspecified",
}


def normalize_official_language(value: object) -> VoiceLanguage:
    """Map an exact official metadata value to the internal language enum."""

    return _OFFICIAL_LANGUAGE_MAP.get(str(value or "").strip().casefold(), "unspecified")


def normalize_official_gender(value: object) -> VoiceGenderPresentation:
    """Map an exact official metadata value to the internal presentation enum."""

    return _OFFICIAL_GENDER_MAP.get(str(value or "").strip().casefold(), "unspecified")


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
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    summary: str
    language: VoiceLanguage = "unspecified"
    gender_presentation: VoiceGenderPresentation = "unspecified"
    age_impression: VoiceAgeImpression = "unspecified"
    texture: list[str] = Field(default_factory=list)
    performance_style: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    best_role_types: list[str] = Field(default_factory=list)
    avoid_role_types: list[str] = Field(default_factory=list)
    emotion_quality: dict[str, float] = Field(default_factory=dict)
    field_sources: dict[str, VoiceMetadataSource] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_profile(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data["schema_version"] = 2
        data["language"] = normalize_official_language(data.get("language"))
        data["gender_presentation"] = normalize_official_gender(data.get("gender_presentation"))
        age_key = str(data.get("age_impression") or "").strip().casefold()
        data["age_impression"] = _LEGACY_AGE_MAP.get(age_key, "unspecified")
        data.setdefault("field_sources", {})
        data.setdefault("conflicts", [])
        return data


class VoiceCatalogVoiceItem(BaseModel):
    voice_label: str
    voice_type: str
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    voice_catalog_key: str
    official: dict[str, Any] = Field(default_factory=dict)
    language: VoiceLanguage = "unspecified"
    gender_presentation: VoiceGenderPresentation = "unspecified"
    metadata_sources: dict[str, VoiceMetadataSource] = Field(default_factory=dict)
    samples: dict[str, VoiceCatalogSampleItem] = Field(default_factory=dict)
    omni_profile: VoiceCatalogProfile | None = None
    profile_hash: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_official_metadata(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        official = data.get("official")
        if not isinstance(official, dict):
            official = {}
        sources = dict(data.get("metadata_sources") or {})
        language = normalize_official_language(data.get("language"))
        if language == "unspecified":
            language = normalize_official_language(official.get("language"))
            if language != "unspecified":
                sources["language"] = "official_metadata"
        data["language"] = language
        gender = normalize_official_gender(data.get("gender_presentation"))
        if gender == "unspecified":
            gender = normalize_official_gender(official.get("gender"))
            if gender != "unspecified":
                sources["gender_presentation"] = "official_metadata"
        data["gender_presentation"] = gender
        data["metadata_sources"] = sources
        return data


class VoiceCatalogManifest(BaseModel):
    schema_version: int = 2
    catalog_version: str
    provider: str
    model: str
    sample_emotions: list[str]
    voices: list[VoiceCatalogVoiceItem]


class VoiceCandidateItem(BaseModel):
    candidate_id: str | None = None
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
    candidate_id: str | None = None
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
        "structured_catalog",
        "provider_fallback",
    ] | str = "omni_judge"
    top_candidates: list[VoiceCandidateItem] = Field(default_factory=list)
    role_profile_hash: str
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
    candidate_id: str | None = None
    voice_label: str
    voice_type: str
    score: float | None = None
    reason: str


class VoiceSelectAudioJudgeOutput(BaseModel):
    selected_candidate_id: str | None = None
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
    "VoiceAgeImpression",
    "VoiceGenderPresentation",
    "VoiceLanguage",
    "VoiceMetadataSource",
    "VoiceSelectAudioJudgeOutput",
    "VoiceSelectAudioJudgeRankedItem",
    "VoiceSelectOutput",
    "VoiceSelectShortlistCandidate",
    "VoiceSelectShortlistOutput",
    "normalize_official_gender",
    "normalize_official_language",
]
