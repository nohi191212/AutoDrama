from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, RootModel, model_validator

ScriptContentRef = str | Literal[False]


class ScriptBundle(BaseModel):
    raw_script: str
    outline: str | None = None
    episode_outlines: dict[str, ScriptContentRef] = Field(default_factory=dict)
    novel_full: dict[str, ScriptContentRef] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("novel_full", "novel_script"),
    )
    novel_extract: dict[str, ScriptContentRef] = Field(default_factory=dict)


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
    id: str
    role_id: str
    name: str = "base"
    asset_role: Literal["base", "variant"] = "base"
    reference_asset_name: str | None = None
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    clothing: str | None = Field(default=None, exclude=True)
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


class ScriptImportRoleOutput(BaseModel):
    name: str
    role_tier: Literal["primary", "functional"] | str = "primary"
    intro: str
    aliases: list[str] = Field(default_factory=list)
    appearance_notes: list[str] = Field(default_factory=list)
    has_dialogue: bool = True
    visual_reuse_required: bool = True
    evidence: str | None = None


class ScriptImportPropOutput(BaseModel):
    name: str
    desc: str
    status: str = "normal"
    owner_role_name: str | None = None
    evidence: str | None = None


class ScriptImportLayoutOutput(BaseModel):
    name: str
    desc: str
    prompt: str
    evidence: str | None = None


class ScriptImportOutput(BaseModel):
    outline: str
    episode_outlines: list[str] = Field(default_factory=list)
    roles: list[ScriptImportRoleOutput] = Field(default_factory=list)
    props: list[ScriptImportPropOutput] = Field(default_factory=list)
    layouts: list[ScriptImportLayoutOutput] = Field(default_factory=list)
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


class ClipSegmentOutput(RootModel[dict[str, ClipSegment]]):
    pass


class ClipSegmentNodeOutput(RootModel[dict[str, dict[str, ClipSegment]]]):
    pass


class KeyVisionPromptOutput(BaseModel):
    prompt: str


class ScriptNovelEpisodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_full: str


class RoleAppearanceExtractItem(BaseModel):
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
    role_brief: str | None = None
    appearance_desc: str | None = None
    clothing: str | None = None
    visual_features: str | None = None
    core_roleboard_prompt: str | None = None
    roleboard_prompt: str
    roleboard_negative_prompt: str | None = None
    voice_profile_prompt: str | None = None
    design_notes: str | None = None


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
    dialogue: list[str] = Field(default_factory=list)
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

class ShotManifestEpisodeOutput(BaseModel):
    """The persisted shot manifest for one episode.

    This deliberately accepts only the current `shots` shape.  Earlier
    manifest fields must be regenerated upstream rather than converted.
    """

    model_config = ConfigDict(extra="forbid")
    episode_key: str
    schema_version: Literal[4] = 4
    shots: list[ShotManifestItem] = Field(default_factory=list)


class ClipToShotsModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_description: str
    narrative_angle: str
    opening_state: str
    ref_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    duration_seconds: int = Field(ge=3, le=15)
    dialogue: list[str] = Field(default_factory=list)


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
    dialogue: list[str] = Field(default_factory=list)


class ClipShotPlan(BaseModel):
    clip_id: str
    clip_index: int
    shots: list[ShotPlanItem] = Field(default_factory=list)


class ClipToShotsEpisodeOutput(BaseModel):
    episode_key: str
    clips: list[ClipShotPlan] = Field(default_factory=list)


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
