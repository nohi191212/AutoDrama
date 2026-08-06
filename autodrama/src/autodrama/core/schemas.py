from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, RootModel, model_validator

ScriptContentRef = str | Literal[False]


class SemanticProvenance(BaseModel):
    """Traceable origin for a value produced at a semantic extraction boundary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal[
        "user",
        "model",
        "official_metadata",
        "migration",
        "manual_review",
    ]
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    model: str | None = None


class ShotEntityState(BaseModel):
    """Temporary entity state that is valid for one shot only."""

    schema_version: Literal[1] = 1
    entity_id: str
    appearance_id: str | None = None
    pose: str | None = None
    emotion: str | None = None
    injury: str | None = None
    held_props: list[str] = Field(default_factory=list)
    energy_state: str | None = None
    event_refs: list[str] = Field(default_factory=list)


class RoleVoiceRequirements(BaseModel):
    """Versioned voice-casting requirements produced at a semantic boundary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    language: Literal["zh", "en", "ja", "es", "other", "unspecified"] = "unspecified"
    gender_presentation: Literal["female", "male", "neutral", "unspecified"] = "unspecified"
    age_impression: Literal[
        "child",
        "teen",
        "young_adult",
        "adult",
        "mature",
        "elderly",
        "unspecified",
    ] = "unspecified"
    performance_traits: list[str] = Field(default_factory=list)
    baseline_emotion: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    provenance: SemanticProvenance = Field(
        default_factory=lambda: SemanticProvenance(
            source="migration",
            evidence=["No structured voice requirements were provided."],
            confidence=0.0,
        )
    )


class DialogueLine(BaseModel):
    """A single, explicitly attributed line of spoken dialogue."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    line_index: int = Field(ge=1)
    speaker_role_id: str | None = None
    speaker_name: str | None = None
    text: str = Field(min_length=1)
    emotion: Literal["normal", "angry", "sad", "happy", "tense", "whisper", "other"] = "normal"
    intensity: float | None = Field(default=None, ge=0.0, le=1.0)
    delivery_mode: Literal["on_screen", "offscreen", "voiceover"] = "on_screen"
    source_text: str | None = None
    provenance: SemanticProvenance


class OverlayTextSpec(BaseModel):
    """Exact readable text requested by shot planning."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    text: str = Field(min_length=1)
    render_mode: Literal["postproduction", "in_scene"]
    placement_hint: str | None = None
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)
    provenance: SemanticProvenance

    @model_validator(mode="after")
    def validate_time_range(self) -> "OverlayTextSpec":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("overlay end_seconds must be greater than or equal to start_seconds")
        return self


class GateResult(BaseModel):
    name: str
    required: bool = True
    status: Literal["accepted", "rejected", "skipped"]
    details: str = ""


class ScriptBundle(BaseModel):
    raw_script: str
    outline: str | None = None
    episode_outlines: dict[str, ScriptContentRef] = Field(default_factory=dict)
    novel_full: dict[str, ScriptContentRef] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("novel_full", "novel_script"),
    )
    novel_extract: dict[str, ScriptContentRef] = Field(default_factory=dict)
    facts: StoryFactBundle | None = None


class RoleAudio(BaseModel):
    id: str
    role_id: str
    emotion: str
    desc: str | None = None
    sample_text: str | None = Field(default=None, exclude=True)
    generation_status: str = "pending"
    asset_id: str | None = None
    asset_path: str | None = None
    duration_seconds: float | None = None
    original_duration_seconds: float | None = None
    duration_limited: bool = False
    voice_name: str | None = None
    voice_type: str | None = None
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    emotion_instruction: str | None = None
    emotion_params: dict[str, Any] = Field(default_factory=dict)


class RoleAppearance(BaseModel):
    schema_version: Literal[2] = 2
    id: str
    role_id: str
    name: str = "base"
    asset_role: Literal["base", "variant"] = "base"
    reference_asset_name: str | None = None
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    clothing: str | None = Field(default=None, exclude=True)
    identity_invariants: list[str] = Field(default_factory=list)
    wardrobe: list[str] = Field(default_factory=list)
    time_period: str | None = None
    age_band: str | None = None
    valid_from_event: str | None = None
    valid_to_event: str | None = None
    provenance: SemanticProvenance | None = None
    migration_warnings: list[str] = Field(default_factory=list)
    legacy_source: str | None = Field(default=None, exclude=True)
    visual_features: str | None = None
    desc: str | None = None
    prompt: str | None = None
    roleboard_prompt: str | None = Field(default=None, exclude=True)
    roleboard_negative_prompt: str | None = Field(default=None, exclude=True)
    voice_profile_prompt: str | None = Field(default=None, exclude=True)
    design_image_generation_status: str = "pending"
    design_image_asset_id: str | None = None
    design_image_asset_path: str | None = None
    design_image_asset_url: str | None = None
    asset_id: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    subject_frontal_image_asset_id: str | None = None
    subject_frontal_image_asset_path: str | None = None
    subject_frontal_image_asset_url: str | None = None
    subject_frontal_image_provider: str | None = None
    subject_frontal_image_model: str | None = None
    subject_frontal_image_request_id: str | None = None
    subject_frontal_image_usage: dict[str, Any] = Field(default_factory=dict)
    subject_frontal_image_raw_response: dict[str, Any] = Field(default_factory=dict)
    subject_video_asset_id: str | None = None
    subject_video_asset_path: str | None = None
    subject_video_asset_url: str | None = None
    subject_video_intro_text: str | None = None
    subject_video_provider: str | None = None
    subject_video_model: str | None = None
    subject_video_task_id: str | None = None
    subject_video_task_status: str | None = None
    subject_video_request_id: str | None = None
    subject_video_usage: dict[str, Any] = Field(default_factory=dict)
    subject_video_raw_response: dict[str, Any] = Field(default_factory=dict)
    subject_element_provider: str | None = None
    subject_element_model: str | None = None
    subject_element_reference_type: str | None = None
    subject_element_voice_id: str | None = None
    subject_element_id: str | None = None
    subject_element_task_id: str | None = None
    subject_element_task_status: str | None = None
    subject_element_request_id: str | None = None
    subject_element_usage: dict[str, Any] = Field(default_factory=dict)
    subject_element_raw_response: dict[str, Any] = Field(default_factory=dict)


class Role(BaseModel):
    id: str
    name: str
    intro: str
    design_path: str | None = None
    personality: str | None = None
    role_tier: Literal["primary", "functional"] | str = "primary"
    has_dialogue: bool = True
    visual_reuse_required: bool = True
    importance: str | None = None
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    voice_requirements: RoleVoiceRequirements = Field(default_factory=RoleVoiceRequirements)
    voice_summary: str | None = None
    voice_name: str | None = None
    voice_type: str | None = None
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    voice_selection_reason: str | None = None
    kling_voice_id: str | None = None
    kling_voice_name: str | None = None
    kling_voice_source: Literal["preset", "custom"] | None = None
    kling_voice_trial_url: str | None = None
    kling_voice_provider: str | None = None
    kling_voice_model: str | None = None
    kling_voice_task_id: str | None = None
    kling_voice_task_status: str | None = None
    kling_voice_request_id: str | None = None
    kling_voice_usage: dict[str, Any] = Field(default_factory=dict)
    kling_voice_raw_response: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    appearances: dict[str, RoleAppearance] = Field(default_factory=dict)
    audio: dict[str, RoleAudio] = Field(default_factory=dict)


class PropAsset(BaseModel):
    id: str
    prop_id: str
    name: str = "base"
    asset_role: Literal["base", "variant"] = "base"
    reference_asset_name: str | None = None
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    desc: str | None = None
    visual_features: str | None = None
    state_change: str | None = None
    prompt_hint: str | None = None
    prompt_type: Literal["text_to_image", "image_edit"] | str | None = None
    prompt: str | None = None
    design_path: str | None = None
    asset_id: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class Prop(BaseModel):
    id: str
    name: str
    intro: str
    aliases: list[str] = Field(default_factory=list)
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    owner_role_id: str | None = None
    owner_role_name: str | None = None
    source: str | None = None
    design_path: str | None = None
    assets: dict[str, PropAsset] = Field(default_factory=dict)

    @property
    def base_asset(self) -> PropAsset | None:
        return self.assets.get("base") or next(iter(self.assets.values()), None)

    @property
    def desc(self) -> str:
        return self.intro

    @property
    def status(self) -> str:
        asset = self.base_asset
        return asset.status if asset is not None else "normal"

    @property
    def prompt(self) -> str | None:
        asset = self.base_asset
        return asset.prompt if asset is not None else None

    @property
    def asset_id(self) -> str | None:
        asset = self.base_asset
        return asset.asset_id if asset is not None else None

    @property
    def asset_path(self) -> str | None:
        asset = self.base_asset
        return asset.asset_path if asset is not None else None

    @property
    def asset_url(self) -> str | None:
        asset = self.base_asset
        return asset.asset_url if asset is not None else None

    @property
    def provider(self) -> str | None:
        asset = self.base_asset
        return asset.provider if asset is not None else None

    @property
    def model(self) -> str | None:
        asset = self.base_asset
        return asset.model if asset is not None else None

    @property
    def request_id(self) -> str | None:
        asset = self.base_asset
        return asset.request_id if asset is not None else None

    @property
    def usage(self) -> dict[str, Any]:
        asset = self.base_asset
        return dict(asset.usage) if asset is not None else {}


class Layout(BaseModel):
    id: str
    name: str
    group: str = ""
    asset_role: Literal["base", "variant"] = "base"
    reference_asset_name: str | None = None
    desc: str
    prompt: str
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    space_features: list[str] = Field(default_factory=list)
    state_delta: str = ""
    asset_id: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    # Legacy projects are deliberately treated as single-view until their
    # layout prompt/image pair is regenerated for the shot-first pipeline.
    reference_image_kind: Literal["single_view", "three_view"] = "single_view"
    prompt_language: str | None = None


class BGM(BaseModel):
    id: str
    name: str
    mood: str
    prompt: str
    usage_hint: str | None = None
    asset_id: str | None = None
    asset_path: str | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    duration_seconds: float | None = None
    lyrics: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


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
    props: dict[str, Prop] = Field(default_factory=dict)
    layouts: dict[str, Layout] = Field(default_factory=dict)
    bgms: dict[str, BGM] = Field(default_factory=dict)
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


class ScriptSourceSpan(BaseModel):
    """A deterministic character and line range in the imported source script."""

    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ScriptSourceSpan":
        if self.end_char <= self.start_char:
            raise ValueError("source span end_char must be greater than start_char")
        if self.end_line < self.start_line:
            raise ValueError("source span end_line must not precede start_line")
        return self


class StoryFactEntity(BaseModel):
    """Stable, source-backed entity reference derived by workflow code."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    entity_type: Literal["role", "prop", "layout", "group"]
    name: str = Field(min_length=1)
    time_periods: list[str] = Field(default_factory=list)
    source_spans: list[ScriptSourceSpan] = Field(min_length=1)


class StoryFactEvent(BaseModel):
    """Narrative event with deterministic IDs and source spans."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    time_period: str | None = None
    participant_entity_ids: list[str] = Field(default_factory=list)
    prop_entity_ids: list[str] = Field(default_factory=list)
    layout_entity_ids: list[str] = Field(default_factory=list)
    precondition: str | None = None
    result: str | None = None
    source_spans: list[ScriptSourceSpan] = Field(min_length=1)


class StoryFactPropObservation(BaseModel):
    """A source-ordered prop appearance, transfer, or state transition."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    prop_entity_id: str = Field(min_length=1)
    prop_name: str = Field(min_length=1)
    action: str = Field(min_length=1)
    state: str | None = None
    holder_entity_id: str | None = None
    holder_name: str | None = None
    time_period: str | None = None
    event_id: str | None = None
    source_spans: list[ScriptSourceSpan] = Field(min_length=1)


class StoryFactBundle(BaseModel):
    """The source-verifiable fact base produced by script_import."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    events: list[StoryFactEvent] = Field(min_length=1)
    entity_mentions: list[StoryFactEntity] = Field(min_length=1)
    prop_observations: list[StoryFactPropObservation] = Field(default_factory=list)
    timeline_order: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "StoryFactBundle":
        event_ids = [event.event_id for event in self.events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("story fact event IDs must be unique")
        if self.timeline_order != event_ids:
            raise ValueError("story fact timeline_order must match the source-ordered event IDs")
        entity_ids = {entity.entity_id for entity in self.entity_mentions}
        for event in self.events:
            for entity_id in (
                *event.participant_entity_ids,
                *event.prop_entity_ids,
                *event.layout_entity_ids,
            ):
                if entity_id not in entity_ids:
                    raise ValueError(f"story fact event references unknown entity: {entity_id}")
        for observation in self.prop_observations:
            if observation.prop_entity_id not in entity_ids:
                raise ValueError(
                    f"story fact prop observation references unknown prop: {observation.prop_entity_id}"
                )
            if observation.holder_entity_id and observation.holder_entity_id not in entity_ids:
                raise ValueError(
                    "story fact prop observation references unknown holder: "
                    f"{observation.holder_entity_id}"
                )
            if observation.event_id and observation.event_id not in event_ids:
                raise ValueError(
                    f"story fact prop observation references unknown event: {observation.event_id}"
                )
        return self


class ScriptImportRoleStageOutput(BaseModel):
    """A model-supplied, source-backed appearance stage for one role."""

    model_config = ConfigDict(extra="forbid")

    time_period: str = Field(min_length=1)
    appearance_notes: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportRoleOutput(BaseModel):
    """Stable role identity plus explicitly separated time-specific appearances."""

    model_config = ConfigDict(extra="forbid")

    name: str
    role_tier: Literal["primary", "functional"] | str = "primary"
    intro: str
    aliases: list[str] = Field(default_factory=list)
    identity_notes: list[str] = Field(default_factory=list)
    appearance_stages: list[ScriptImportRoleStageOutput] = Field(default_factory=list)
    has_dialogue: bool = True
    visual_reuse_required: bool = True
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportPropOutput(BaseModel):
    """A stable prop identity; time-varying state lives in prop_observations."""

    model_config = ConfigDict(extra="forbid")

    name: str
    identity_description: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportLayoutOutput(BaseModel):
    """A physical space identity, without actions or future visual state."""

    model_config = ConfigDict(extra="forbid")

    name: str
    spatial_description: str = Field(min_length=1)
    time_periods: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportEntityMentionOutput(BaseModel):
    """Model-supplied source evidence from which code creates stable entity IDs."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    entity_type: Literal["role", "prop", "layout", "group"]
    time_period: str | None = None
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportEventOutput(BaseModel):
    """Semantic event data; IDs and spans are generated by workflow code."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    time_period: str | None = None
    participant_names: list[str] = Field(default_factory=list)
    prop_names: list[str] = Field(default_factory=list)
    layout_names: list[str] = Field(default_factory=list)
    precondition: str | None = None
    result: str | None = None
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportPropObservationOutput(BaseModel):
    """A semantic prop occurrence with source evidence, before code assigns IDs."""

    model_config = ConfigDict(extra="forbid")

    prop_name: str = Field(min_length=1)
    action: str = Field(min_length=1)
    state: str | None = None
    holder_name: str | None = None
    time_period: str | None = None
    evidence_quotes: list[str] = Field(min_length=1)


class ScriptImportFactsOutput(BaseModel):
    """Model semantic output; all operational IDs and spans are code-owned."""

    model_config = ConfigDict(extra="forbid")

    events: list[ScriptImportEventOutput] = Field(min_length=1)
    entity_mentions: list[ScriptImportEntityMentionOutput] = Field(min_length=1)
    prop_observations: list[ScriptImportPropObservationOutput] = Field(default_factory=list)


class ScriptImportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline: str
    episode_outlines: list[str] = Field(default_factory=list)
    roles: list[ScriptImportRoleOutput] = Field(default_factory=list)
    props: list[ScriptImportPropOutput] = Field(default_factory=list)
    layouts: list[ScriptImportLayoutOutput] = Field(default_factory=list)
    facts: ScriptImportFactsOutput
    notes: str | None = None

class ScriptOutlineOutput(BaseModel):
    outline: str
    episode_outlines: dict[str, str] = Field(default_factory=dict)


class ScriptNovelOutput(BaseModel):
    novel_full: dict[str, str] = Field(validation_alias=AliasChoices("novel_full", "novel_script"))


class ScriptDetailExpandOutput(BaseModel):
    expanded_script: str = Field(validation_alias=AliasChoices("expanded_script", "script", "novel_full"))


class ScriptNovelExtractModelOutput(BaseModel):
    script_novel_extract: str


class ScriptNovelExtractOutput(BaseModel):
    novel_extract: dict[str, str]


class ClipSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    role_names: list[str] = Field(default_factory=list)
    prop_names: list[str] = Field(default_factory=list)
    layout_names: list[str] = Field(default_factory=list)
    allocated_seconds: float | None = Field(default=None, gt=0)
    event_ids: list[str] = Field(default_factory=list)
    required_beats: list[str] = Field(default_factory=list)
    coverage_goal: str | None = None
    pace_class: str | None = None


class ClipSegmentOutput(RootModel[dict[str, ClipSegment]]):
    pass


class ClipSegmentNodeOutput(RootModel[dict[str, dict[str, ClipSegment]]]):
    pass


class KeyVisionPromptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_contract: str
    scene_style_contract: str
    prompt: str


class ScriptNovelEpisodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_full: str


class RoleAppearanceExtractItem(BaseModel):
    schema_version: Literal[2] = 2
    name: str = "base"
    asset_role: Literal["base", "variant"] = Field(
        default="base",
        description=(
            "base for an independently generated stable asset; variant for clothing, makeup, hairstyle, "
            "injury, dirt, wet, disguise, or other visual states that reuse a reference asset."
        ),
    )
    reference_asset_name: str | None = Field(
        default=None,
        description="For variant assets, the same-role reference asset to lock face/body/hair from; usually base.",
    )
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    clothing: str | None = None
    visual_features: str | None = None
    appearance_desc: str | None = None
    prompt_hint: str | None = None
    time_period: str | None = None
    age_band: str | None = None
    identity_invariants: list[str] = Field(default_factory=list)
    wardrobe: list[str] = Field(default_factory=list)
    valid_from_event: str | None = None
    valid_to_event: str | None = None
    provenance: SemanticProvenance

    @model_validator(mode="after")
    def validate_visual_contract(self) -> "RoleAppearanceExtractItem":
        self.identity_invariants = list(
            dict.fromkeys(
                text
                for value in self.identity_invariants
                if (text := " ".join(str(value).split()).strip())
            )
        )
        self.wardrobe = list(
            dict.fromkeys(
                text
                for value in self.wardrobe
                if (text := " ".join(str(value).split()).strip())
            )
        )
        if not self.identity_invariants:
            raise ValueError("role appearance extraction requires explicit identity_invariants")
        if self.asset_role == "base" and self.reference_asset_name:
            raise ValueError("base role appearance must not reference another appearance")
        if self.asset_role == "variant":
            if not str(self.reference_asset_name or "").strip():
                raise ValueError("variant role appearance requires reference_asset_name")
            if not (self.valid_from_event or self.valid_to_event):
                raise ValueError("variant role appearance requires an explicit event validity range")
        return self


class RoleExtractItem(BaseModel):
    name: str
    role_tier: Literal["primary", "functional"] | str = "primary"
    aliases: list[str] = Field(default_factory=list)
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)
    appearance_assets: list[RoleAppearanceExtractItem] = Field(default_factory=list)
    has_dialogue: bool = False
    visual_reuse_required: bool = False



class RoleExtractOutput(BaseModel):
    roles: list[RoleExtractItem]



class RoleFinalizeEpisodeUpdate(BaseModel):
    role_name: str
    add_episode_keys: list[str] = Field(default_factory=list)
    add_source_chapters: list[str] = Field(default_factory=list)
    evidence: str | None = None
    confidence: float | None = None


class RoleFinalizeDuplicateGroup(BaseModel):
    role_names: list[str] = Field(default_factory=list)
    kept_role_name: str | None = None
    evidence: str | None = None
    confidence: float | None = None


class RoleFinalizeDropItem(BaseModel):
    role_name: str
    reason: str


class RoleFinalizeAuditReviewOutput(BaseModel):
    episode_updates: list[RoleFinalizeEpisodeUpdate] = Field(default_factory=list)
    duplicate_groups: list[RoleFinalizeDuplicateGroup] = Field(default_factory=list)
    drop_roles: list[RoleFinalizeDropItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RoleFinalizeOutput(BaseModel):
    final_roles: list[RoleExtractItem] = Field(default_factory=list)
    role_refs: dict[str, str] = Field(default_factory=dict)
    episode_updates: list[RoleFinalizeEpisodeUpdate] = Field(default_factory=list)
    duplicate_groups: list[RoleFinalizeDuplicateGroup] = Field(default_factory=list)
    dropped_roles: list[RoleFinalizeDropItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RoleboardPromptModelOutput(BaseModel):
    roleboard_prompt: str
    roleboard_negative_prompt: str | None = None
    voice_profile_prompt: str | None = None
    design_notes: str | None = None


class RoleboardPromptItem(BaseModel):
    role_id: str
    role_name: str
    appearance_id: str
    appearance_name: str = "base"
    asset_role: Literal["base", "variant"] = "base"
    reference_asset_name: str | None = None
    role_tier: Literal["primary", "functional"] | str | None = None
    has_dialogue: bool = True
    visual_reuse_required: bool = True
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    time_period: str | None = None
    age_band: str | None = None
    valid_from_event: str | None = None
    valid_to_event: str | None = None
    role_brief: str | None = None
    appearance_desc: str | None = None
    clothing: str | None = None
    visual_features: str | None = None
    core_roleboard_prompt: str | None = None
    roleboard_prompt: str
    roleboard_negative_prompt: str | None = None
    voice_profile_prompt: str | None = None
    design_notes: str | None = None
    included_fields: list[str] = Field(default_factory=list)
    excluded_state_fields: list[str] = Field(default_factory=list)


class RoleboardPromptOutput(BaseModel):
    prompts: list[RoleboardPromptItem]


class PropAssetExtractItem(BaseModel):
    name: str = "base"
    asset_role: Literal["base", "variant"] = "base"
    status: str = "normal"
    reference_asset_name: str | None = None
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    desc: str
    visual_features: str | None = None
    state_change: str | None = None
    prompt_hint: str | None = None


class PropExtractItem(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    intro: str
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    owner_role_name: str | None = None
    assets: list[PropAssetExtractItem] = Field(default_factory=list)


class PropExtractOutput(BaseModel):
    props: list[PropExtractItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PropDedupeOutput(BaseModel):
    props: list[PropExtractItem] = Field(default_factory=list)
    merge_notes: list[str] = Field(default_factory=list)


class PropAssetPromptItem(BaseModel):
    prop_name: str
    asset_name: str = "base"
    prompt_type: Literal["text_to_image", "image_edit"] | str
    reference_asset_name: str | None = None
    prompt: str


class PropPromptOutput(BaseModel):
    prop_asset_prompts: list[PropAssetPromptItem] = Field(default_factory=list)


class PropDesignItem(BaseModel):
    name: str
    desc: str
    prompt: str
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)


class PropDesignOutput(BaseModel):
    props: list[PropDesignItem]

class LayoutExtractItem(BaseModel):
    name: str
    group: str
    asset_role: Literal["base", "variant"]
    reference_asset_name: str | None = None
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str
    space_features: list[str] = Field(default_factory=list)
    state_delta: str = ""


class LayoutExtractOutput(BaseModel):
    layouts: list[LayoutExtractItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutPromptItem(BaseModel):
    name: str
    group: str
    asset_role: Literal["base", "variant"]
    reference_asset_name: str | None = None
    prompt_type: Literal["text_to_image", "image_edit"]
    prompt: str


class LayoutPromptOutput(BaseModel):
    layout_prompts: list[LayoutPromptItem] = Field(default_factory=list)


class LayoutDedupeReviewOutput(BaseModel):
    layouts: list[LayoutExtractItem] = Field(default_factory=list)
    merge_notes: list[str] = Field(default_factory=list)


class LayoutPropBoundaryReviewOutput(BaseModel):
    props: list[PropExtractItem] = Field(default_factory=list)
    layouts: list[LayoutExtractItem] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)


class BGMDesignItem(BaseModel):
    name: str
    mood: str
    prompt: str
    usage_hint: str | None = None


class BGMDesignOutput(BaseModel):
    bgms: list[BGMDesignItem]


class StaticAssetGenerationItem(BaseModel):
    asset_id: str
    asset_type: Literal[
        "roleboard",
        "key_vision",
        "prop",
        "layout",
        "bgm",
    ]
    owner_id: str
    name: str
    prompt: str
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class StaticAssetGenerationOutput(BaseModel):
    generated_assets: list[StaticAssetGenerationItem]


class ImageAuditRegion(BaseModel):
    """Normalized visible evidence region for an image-audit defect."""

    label: str = ""
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> "ImageAuditRegion":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("image audit region must have positive normalized width and height")
        return self


class ImageAuditDimensionAssessment(BaseModel):
    """Visible-evidence score for one production image-audit dimension."""

    dimension_id: str
    category: Literal["physical", "contract", "cinematic", "style"]
    weight: float = Field(gt=0)
    gate: bool = False
    applicable: bool = True
    score: float | None = Field(default=None, ge=0, le=10)
    severity: Literal["none", "minor", "major", "critical"] = "none"
    evidence: str = Field(min_length=1)
    defect: str = ""
    regions: list[ImageAuditRegion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assessment(self) -> "ImageAuditDimensionAssessment":
        if self.applicable and self.score is None:
            raise ValueError("applicable image-audit dimensions require a score")
        if not self.applicable and self.score is not None:
            raise ValueError("non-applicable image-audit dimensions require score=null")
        if self.applicable and self.score is not None and self.score < 10 and not self.defect.strip():
            raise ValueError("every sub-perfect image-audit score requires a concrete defect")
        if self.severity in {"major", "critical"} and not self.regions:
            raise ValueError("major and critical image-audit defects require normalized regions")
        return self


class ImageAssetAuditItem(BaseModel):
    """Visual acceptance decision for one generated image asset."""

    asset_id: str
    asset_type: str
    episode_key: str | None = None
    approved: bool
    issues: list[str] = Field(default_factory=list)
    revised_prompt: str = ""
    rationale: str = ""
    attempts: int = Field(default=1, ge=1)
    rubric_name: str = ""
    rubric_revision: str = ""
    weighted_score: float | None = Field(default=None, ge=0, le=10)
    category_scores: dict[str, float] = Field(default_factory=dict)
    dimension_assessments: list[ImageAuditDimensionAssessment] = Field(default_factory=list)


class ImageAssetAuditOutput(BaseModel):
    source_node: str
    audited_assets: list[ImageAssetAuditItem] = Field(default_factory=list)


class SafeImagePromptRewriteOutput(BaseModel):
    prompt: str
    notes: str = ""


class ShotDialogueAudioAsset(BaseModel):
    asset_id: str
    role_id: str | None = None
    role_name: str | None = None
    line_index: int
    text: str
    emotion: str | None = None
    voice: str | None = None
    voice_name: str | None = None
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    emotion_instruction: str | None = None
    emotion_params: dict[str, Any] = Field(default_factory=dict)
    asset_path: str | None = None
    provider: str | None = None
    model: str | None = None
    duration_seconds: float | None = None
    original_duration_seconds: float | None = None
    duration_limited: bool = False
    sample_rate: int | None = None
    response_format: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShotBGMAsset(BaseModel):
    asset_id: str
    sound_description: str
    asset_path: str | None = None
    provider: str | None = None
    model: str | None = None
    duration_seconds: float | None = None
    response_format: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShotSourceCoverage(BaseModel):
    start_text: str
    end_text: str
    next_start_text: str | None = None
    note: str


class ShotVideoInput(BaseModel):
    slot: str
    type: Literal["image"] = "image"
    asset_type: Literal["shot_keyframe", "shot_last_frame", "roleboard", "layout", "prop"] | str
    asset_id: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    source_node: str
    label: str | None = None
    role_id: str | None = None
    role_name: str | None = None
    appearance_id: str | None = None
    appearance_name: str | None = None
    layout_id: str | None = None
    layout_name: str | None = None
    prop_id: str | None = None
    prop_name: str | None = None
    required: bool = True
    order: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShotManifestItem(BaseModel):
    """The persisted, shot-first generation contract for one video shot."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str
    index: int
    layout_id: str | None = None
    layout_ids: list[str] = Field(default_factory=list)
    clip_id: str | None = None
    clip_index: int | None = None
    shot_index_in_clip: int | None = None
    ref_ids: list[str] = Field(default_factory=list)
    title: str
    segment_index: int | None = None
    source_coverage: ShotSourceCoverage | None = None
    content: str | None = None
    scene_description: str | None = None
    composition: str | None = None
    lighting: str | None = None
    sound_design: str | None = None
    camera_shooting_angle: str | None = None
    camera_movement: str | None = None
    focal_length: str | None = None
    duration_seconds: float
    transition: str | None = None
    start_frame_source: Literal["new_reference_frame", "previous_shot_last_frame", "own_start_frame", "previous_clip_end_frame"] | str = "new_reference_frame"
    start_frame_inheritance_reason: str | None = None
    dialogue_lines: list[DialogueLine] = Field(default_factory=list)
    dialogue: list[str] = Field(default_factory=list, description="Derived display-only dialogue text")
    role_ids: list[str] = Field(default_factory=list)
    role_appearance_ids: list[str] = Field(default_factory=list)
    role_audio_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    final_video_prompt: str | None = None
    video_inputs: list[ShotVideoInput] = Field(default_factory=list)
    start_frame_asset_id: str | None = None
    start_frame_asset_path: str | None = None
    start_frame_asset_url: str | None = None
    start_frame_source_clip_id: str | None = None
    end_frame_asset_id: str | None = None
    end_frame_asset_path: str | None = None
    end_frame_asset_url: str | None = None
    background_asset_id: str | None = None
    background_asset_path: str | None = None
    background_asset_url: str | None = None
    shot_description: str | None = None
    narrative_angle: str | None = None
    opening_state: str | None = None
    is_first_clip: bool = False
    requires_initial_hard_cut: bool = False
    dialogue_audio_assets: list[ShotDialogueAudioAsset] = Field(default_factory=list)
    shot_bgm_assets: list[ShotBGMAsset] = Field(default_factory=list)
    video_asset_id: str | None = None
    video_asset_path: str | None = None
    video_provider: str | None = None
    video_model: str | None = None
    video_task_id: str | None = None
    video_task_status: str | None = None
    video_request_id: str | None = None
    video_last_frame_asset_path: str | None = None
    video_usage: dict[str, Any] = Field(default_factory=dict)
    video_raw_response: dict[str, Any] = Field(default_factory=dict)
    solidified_asset_ids: list[str] = Field(default_factory=list)
    contract_version: Literal[2] = 2
    gate_results: list[GateResult] = Field(default_factory=list)
    entity_state_snapshot: list[ShotEntityState] = Field(default_factory=list)
    reference_budget: int | None = None
    text_overlay_spec: OverlayTextSpec | None = None
    input_fingerprints: dict[str, str] = Field(default_factory=dict)
    ready_for_video: bool = False

    @model_validator(mode="after")
    def derive_dialogue_display(self) -> "ShotManifestItem":
        expected = [line.text for line in self.dialogue_lines]
        if self.dialogue and self.dialogue != expected:
            raise ValueError("dialogue is display-only and must match dialogue_lines text")
        self.dialogue = expected
        return self

class ShotManifestEpisodeOutput(BaseModel):
    """The persisted shot manifest for one episode.

    This deliberately accepts only the current `shots` shape.  Earlier
    manifest fields must be regenerated upstream rather than converted.
    """

    model_config = ConfigDict(extra="forbid")
    episode_key: str
    schema_version: Literal[5] = 5
    shots: list[ShotManifestItem] = Field(default_factory=list)
    gate_results: list[GateResult] = Field(default_factory=list)
    ready_for_video: bool = False


class ClipToShotsModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_description: str
    narrative_angle: str
    opening_state: str
    ref_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    duration_seconds: int = Field(ge=3, le=15)
    entity_states: list[ShotEntityState] = Field(default_factory=list)
    dialogue_lines: list[DialogueLine] = Field(default_factory=list)
    overlay_text_spec: OverlayTextSpec | None = None
    allowed_props: list[str] = Field(default_factory=list)


class ClipToShotsModelOutput(RootModel[dict[str, ClipToShotsModelItem]]):
    pass


class ShotPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    clip_id: str
    clip_index: int
    shot_index_in_clip: int
    episode_shot_index: int
    shot_description: str
    narrative_angle: str
    opening_state: str
    ref_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    duration_seconds: int = Field(ge=3, le=15)
    dialogue_lines: list[DialogueLine] = Field(default_factory=list)
    dialogue: list[str] = Field(default_factory=list, description="Derived display-only dialogue text")
    duration_budget: float | None = Field(default=None, gt=0)
    event_ids: list[str] = Field(default_factory=list)
    coverage_type: str | None = None
    entity_states: list[ShotEntityState] = Field(default_factory=list)
    allowed_props: list[str] = Field(default_factory=list)
    reference_budget: int | None = Field(default=None, ge=1)
    overlay_text_spec: OverlayTextSpec | None = None

    @model_validator(mode="after")
    def derive_legacy_display_fields(self) -> "ShotPlanItem":
        expected = [line.text for line in self.dialogue_lines]
        if self.dialogue and self.dialogue != expected:
            raise ValueError("dialogue is display-only and must match dialogue_lines text")
        self.dialogue = expected
        return self


class ClipShotPlan(BaseModel):
    clip_id: str
    clip_index: int
    shots: list[ShotPlanItem] = Field(default_factory=list)


class ClipToShotsEpisodeOutput(BaseModel):
    episode_key: str
    clips: list[ClipShotPlan] = Field(default_factory=list)
    target_duration_seconds: float | None = None
    total_duration_seconds: float | None = None
    duration_gate_status: Literal["accepted", "rejected"] | None = None


class ClipToShotsOutput(BaseModel):
    episodes: list[ClipToShotsEpisodeOutput] = Field(default_factory=list)


class LayoutBackgroundPromptModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_content: str
    shot_indices: list[int] = Field(min_length=1)
    description: str


class LayoutBackgroundPromptModelOutput(RootModel[dict[str, LayoutBackgroundPromptModelItem]]):
    pass


class ShotBackgroundPromptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background_id: str
    layout_id: str
    shot_ids: list[str] = Field(min_length=1)
    description: str
    prompt: str


class LayoutToBackgroundPromptEpisodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_key: str
    backgrounds: list[ShotBackgroundPromptItem] = Field(default_factory=list)


class LayoutToBackgroundPromptOutput(BaseModel):
    episodes: list[LayoutToBackgroundPromptEpisodeOutput] = Field(default_factory=list)


class ShotBackgroundImageGenerationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background_id: str
    layout_id: str
    shot_ids: list[str] = Field(min_length=1)
    description: str
    prompt: str
    fingerprint: str
    asset_path: str
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShotBackgroundImageGenerationEpisodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_key: str
    generated_backgrounds: list[ShotBackgroundImageGenerationItem] = Field(default_factory=list)


class ShotBackgroundImageGenerationOutput(BaseModel):
    episodes: list[ShotBackgroundImageGenerationEpisodeOutput] = Field(default_factory=list)


class ShotKeyframePromptModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_content: str


class ShotKeyframePromptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_key: str
    clip_id: str
    shot_id: str
    background_id: str
    background_asset_path: str
    background_asset_url: str | None = None
    ref_ids: list[str] = Field(default_factory=list)
    prompt: str
    negative_prompt: str | None = None
    included_fields: list[str] = Field(default_factory=list)
    excluded_state_fields: list[str] = Field(default_factory=list)
    prompt_provenance: dict[str, Any] = Field(default_factory=dict)
    clean_plate: bool = False
    overlay_text_spec: OverlayTextSpec | None = None


class ShotKeyframePromptOutput(BaseModel):
    prompts: list[ShotKeyframePromptItem] = Field(default_factory=list)


class ShotKeyframeImageGenerationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_key: str
    clip_id: str
    shot_id: str
    background_id: str
    ref_ids: list[str] = Field(default_factory=list)
    keyframe_asset_id: str
    keyframe_asset_path: str
    keyframe_asset_url: str | None = None
    prompt: str
    fingerprint: str
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    input_fingerprint: str | None = None
    audit_status: Literal["generated", "accepted", "rejected"] = "generated"
    resume_provenance: dict[str, Any] = Field(default_factory=dict)


class ShotKeyframeImageGenerationOutput(BaseModel):
    generated_keyframes: list[ShotKeyframeImageGenerationItem] = Field(default_factory=list)


class ShotManifestGenerationEpisodeItem(BaseModel):
    episode_key: str
    shot_count: int
    shot_path: str
    warnings: list[str] = Field(default_factory=list)


class ShotManifestGenerationOutput(BaseModel):
    episodes: list[ShotManifestGenerationEpisodeItem] = Field(default_factory=list)


class ShotDialogueAudioGenerationItem(BaseModel):
    episode_key: str
    shot_id: str
    asset: ShotDialogueAudioAsset


class ShotDialogueAudioGenerationOutput(BaseModel):
    generated_dialogue_audios: list[ShotDialogueAudioGenerationItem]
    skipped_dialogue_lines: list[dict[str, Any]] = Field(default_factory=list)


class ShotVideoGenerationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_key: str
    shot_id: str
    asset_id: str
    prompt: str
    duration_seconds: float | None = None
    asset_path: str | None = None
    last_frame_asset_path: str | None = None
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShotVideoGenerationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_videos: list[ShotVideoGenerationItem]


class VideoAssetAuditItem(BaseModel):
    episode_key: str
    shot_id: str
    asset_id: str
    approved: bool
    issues: list[str] = Field(default_factory=list)
    revised_prompt: str = ""
    rationale: str = ""
    attempts: int = Field(default=1, ge=1)


class VideoAssetAuditOutput(BaseModel):
    audited_videos: list[VideoAssetAuditItem] = Field(default_factory=list)


class RoleSubjectVideoGenerationItem(BaseModel):
    role_id: str
    role_name: str
    appearance_id: str
    appearance_name: str
    asset_id: str
    prompt: str
    intro_text: str | None = None
    duration_seconds: float | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class RoleSubjectFrontalImageGenerationItem(BaseModel):
    role_id: str
    role_name: str
    appearance_id: str
    appearance_name: str
    asset_id: str
    prompt: str
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class RoleSubjectFrontalImageGenerationOutput(BaseModel):
    generated_frontal_images: list[RoleSubjectFrontalImageGenerationItem]
    skipped_frontal_images: list[dict[str, Any]] = Field(default_factory=list)


class RoleSubjectVideoGenerationOutput(BaseModel):
    generated_subject_videos: list[RoleSubjectVideoGenerationItem]
    skipped_subject_videos: list[dict[str, Any]] = Field(default_factory=list)


class RoleSubjectVideoIntroTextOutput(BaseModel):
    intro_text: str


class RoleSubjectElementGenerationItem(BaseModel):
    role_id: str
    role_name: str
    appearance_id: str
    appearance_name: str
    reference_type: str
    element_id: str | None = None
    voice_id: str | None = None
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class RoleSubjectElementGenerationOutput(BaseModel):
    generated_subject_elements: list[RoleSubjectElementGenerationItem]
    skipped_subject_elements: list[dict[str, Any]] = Field(default_factory=list)


class RoleKlingVoiceGenerationItem(BaseModel):
    role_id: str
    role_name: str
    voice_id: str
    voice_name: str | None = None
    source: Literal["preset", "custom"]
    trial_url: str | None = None
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class RoleKlingVoiceGenerationOutput(BaseModel):
    generated_voices: list[RoleKlingVoiceGenerationItem] = Field(default_factory=list)
    skipped_roles: list[dict[str, Any]] = Field(default_factory=list)


class DynamicAssetSolidificationItem(BaseModel):
    asset_id: str
    asset_type: Literal["shot_dialogue_audio", "shot_bgm", "shot_video"]
    episode_key: str
    shot_id: str
    asset_path: str | None = None
    source_node: str
    reuse_scope: str = "shot"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DynamicAssetSolidificationOutput(BaseModel):
    solidified_assets: list[DynamicAssetSolidificationItem]


class DynamicAssetIndex(BaseModel):
    schema_version: int = 1
    assets: list[DynamicAssetSolidificationItem] = Field(default_factory=list)
