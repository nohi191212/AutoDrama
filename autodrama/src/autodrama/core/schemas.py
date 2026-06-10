from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field

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
    desc: str | None = Field(default=None, exclude=True)
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
    desc: str | None = Field(default=None, exclude=True)
    prompt: str | None = Field(default=None, exclude=True)
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
    aliases: list[str] = Field(default_factory=list)
    appearances: dict[str, RoleAppearance] = Field(default_factory=dict)
    audio: dict[str, RoleAudio] = Field(default_factory=dict)


class Prop(BaseModel):
    id: str
    name: str
    desc: str
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)
    owner_role_id: str | None = None
    owner_role_name: str | None = None
    source: str | None = None
    design_path: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    prompt: str | None = Field(default=None, exclude=True)
    asset_id: str | None = Field(default=None, exclude=True)
    provider: str | None = Field(default=None, exclude=True)
    model: str | None = Field(default=None, exclude=True)
    request_id: str | None = Field(default=None, exclude=True)
    usage: dict[str, Any] = Field(default_factory=dict, exclude=True)


class Layout(BaseModel):
    id: str
    name: str
    desc: str
    prompt: str
    episode_keys: list[str] = Field(default_factory=list)
    asset_id: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


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


class ScriptOutlineOutput(BaseModel):
    logline: str
    outline: str
    episode_count: int = 1
    target_duration_seconds: int = 30
    episode_outlines: dict[str, str] = Field(default_factory=dict)


class ScriptNovelOutput(BaseModel):
    novel_full: dict[str, str] = Field(validation_alias=AliasChoices("novel_full", "novel_script"))


class ScriptDetailExpandOutput(BaseModel):
    episode_key: str
    expanded_script: str = Field(validation_alias=AliasChoices("expanded_script", "script", "novel_full"))
    source_char_count: int | None = None
    expanded_char_count: int | None = None


class ScriptNovelExtractBatchOutput(BaseModel):
    novel_extract: dict[str, str]


class ScriptNovelExtractOutput(BaseModel):
    novel_extract: dict[str, str]


class DirectorShotBeat(BaseModel):
    beat_index: int
    title: str
    source_anchor: str | None = None
    dramatic_intent: str
    what_to_shoot: str
    camera_language: str | None = None
    emotion: str | None = None
    must_keep: list[str] = Field(default_factory=list)
    must_not_change: list[str] = Field(default_factory=list)


class DirectorCharacterLock(BaseModel):
    name: str
    identity: str
    arc: str | None = None
    visual_invariants: list[str] = Field(default_factory=list)
    performance_invariants: list[str] = Field(default_factory=list)
    must_not_change: list[str] = Field(default_factory=list)


class DirectorSceneLock(BaseModel):
    name: str
    description: str
    spatial_facts: list[str] = Field(default_factory=list)
    lighting_mood: str | None = None
    must_not_change: list[str] = Field(default_factory=list)


class DirectorEpisodePrep(BaseModel):
    episode_key: str
    story_function: str
    emotional_curve: list[str] = Field(default_factory=list)
    shot_beats: list[DirectorShotBeat] = Field(default_factory=list)
    must_keep: list[str] = Field(default_factory=list)
    must_not_change: list[str] = Field(default_factory=list)


class DirectorPrepOutput(BaseModel):
    story_core: str
    worldview: str
    visual_tone: str | None = None
    immutable_rules: list[str] = Field(default_factory=list)
    character_locks: list[DirectorCharacterLock] = Field(default_factory=list)
    scene_locks: list[DirectorSceneLock] = Field(default_factory=list)
    episodes: list[DirectorEpisodePrep] = Field(default_factory=list)


class KeyVisionPromptOutput(BaseModel):
    prompt: str


class ScriptNovelEpisodeOutput(BaseModel):
    episode_key: str
    target_char_count: int
    novel_full: str = Field(validation_alias=AliasChoices("novel_full", "novel_text"))


class RoleExtractItem(BaseModel):
    name: str
    role_tier: Literal["primary", "functional"] | str = "primary"
    aliases: list[str] = Field(default_factory=list)
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)
    has_dialogue: bool = False
    visual_reuse_required: bool = False


class AmbientEntityItem(BaseModel):
    name: str
    entity_type: Literal["crowd", "faction_presence", "background_actor_group"] | str
    episode_keys: list[str] = Field(default_factory=list)
    description: str
    visual_notes: list[str] = Field(default_factory=list)
    usage: str | None = None


class AmbientEntityOutput(BaseModel):
    entities: list[AmbientEntityItem]


class RoleExtractOutput(BaseModel):
    roles: list[RoleExtractItem]


class RoleEpisodeKeyAuditReviewOutput(BaseModel):
    role_name: str
    missing_episode_keys: list[str] = Field(default_factory=list)
    missing_source_chapters: list[str] = Field(default_factory=list)
    evidence: str | None = None
    confidence: float | None = None


class RoleEpisodeKeyAuditItem(BaseModel):
    role_id: str
    role_name: str
    role_json_path: str | None = None
    original_episode_keys: list[str] = Field(default_factory=list)
    missing_episode_keys: list[str] = Field(default_factory=list)
    added_episode_keys: list[str] = Field(default_factory=list)
    final_episode_keys: list[str] = Field(default_factory=list)
    original_source_chapters: list[str] = Field(default_factory=list)
    added_source_chapters: list[str] = Field(default_factory=list)
    final_source_chapters: list[str] = Field(default_factory=list)
    ignored_episode_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None
    confidence: float | None = None


class RoleEpisodeKeyAuditOutput(BaseModel):
    concurrency: int
    audited_roles: list[RoleEpisodeKeyAuditItem] = Field(default_factory=list)


class RoleDuplicateGroupReviewOutput(BaseModel):
    role_names: list[str] = Field(default_factory=list)
    evidence: str | None = None
    confidence: float | None = None


class RoleDuplicateAuditReviewOutput(BaseModel):
    duplicate_groups: list[RoleDuplicateGroupReviewOutput] = Field(default_factory=list)


class RoleDuplicateMergeItem(BaseModel):
    kept_role_name: str
    removed_role_names: list[str] = Field(default_factory=list)
    original_episode_keys_by_role: dict[str, list[str]] = Field(default_factory=dict)
    final_episode_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None
    confidence: float | None = None


class RoleDuplicateAuditOutput(BaseModel):
    checked_roles: int
    merged_groups: list[RoleDuplicateMergeItem] = Field(default_factory=list)
    remaining_role_names: list[str] = Field(default_factory=list)


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
    role_tier: Literal["primary", "functional"] | str | None = None
    has_dialogue: bool = True
    visual_reuse_required: bool = True
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    role_brief: str | None = None
    appearance_desc: str | None = None
    roleboard_prompt: str
    roleboard_negative_prompt: str | None = None
    voice_profile_prompt: str | None = None
    design_notes: str | None = None


class RoleboardPromptOutput(BaseModel):
    prompts: list[RoleboardPromptItem]


class RoleVoiceGenerationItem(BaseModel):
    role_id: str
    role_name: str
    emotion: str
    audio_id: str
    generation_method: Literal["design", "clone", "reuse", "synthesis"]
    voice: str
    voice_name: str | None = None
    voice_resource_id: str | None = None
    voice_model_family: str | None = None
    voice_selection_reason: str | None = None
    source_audio_id: str | None = None
    source_audio_path: str | None = None
    voice_prompt: str
    preview_text: str
    emotion_instruction: str | None = None
    emotion_params: dict[str, Any] = Field(default_factory=dict)
    preview_audio_path: str | None = None
    duration_seconds: float | None = None
    original_duration_seconds: float | None = None
    duration_limited: bool = False
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


class PropExtractItem(BaseModel):
    name: str
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)


class PropExtractOutput(BaseModel):
    props: list[PropExtractItem]


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
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)


class LayoutExtractOutput(BaseModel):
    layouts: list[LayoutExtractItem]


class LayoutDesignItem(BaseModel):
    name: str
    desc: str
    prompt: str
    episode_keys: list[str] = Field(default_factory=list)


class LayoutDesignOutput(BaseModel):
    layouts: list[LayoutDesignItem]


class LayoutDedupeReviewOutput(BaseModel):
    layouts: list[LayoutDesignItem]
    merge_notes: list[str] = Field(default_factory=list)


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


class StoryboardSourceCoverage(BaseModel):
    start_text: str
    end_text: str
    next_start_text: str | None = None
    note: str


class StoryboardShotDraft(BaseModel):
    layout_id: str
    title: str
    source_coverage: StoryboardSourceCoverage
    duration_seconds: float
    transition: str | None = None
    dialogue: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    role_appearance_ids: list[str] = Field(default_factory=list)
    role_audio_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    anchor_frame_prompt: str
    video_prompt: str


class StoryboardShot(BaseModel):
    shot_id: str
    index: int
    layout_id: str
    title: str
    source_coverage: StoryboardSourceCoverage | None = None
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
    start_frame_source: Literal["new_reference_frame", "previous_shot_last_frame"] = "new_reference_frame"
    start_frame_inheritance_reason: str | None = None
    dialogue: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    role_appearance_ids: list[str] = Field(default_factory=list)
    role_audio_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    ref_frame_prompt: str
    video_prompt: str
    dialogue_audio_assets: list[ShotDialogueAudioAsset] = Field(default_factory=list)
    shot_bgm_assets: list[ShotBGMAsset] = Field(default_factory=list)
    ref_frame_asset_id: str | None = None
    ref_frame_asset_path: str | None = None
    ref_frame_asset_url: str | None = None
    ref_frame_provider: str | None = None
    ref_frame_model: str | None = None
    ref_frame_request_id: str | None = None
    ref_frame_usage: dict[str, Any] = Field(default_factory=dict)
    ref_frame_raw_response: dict[str, Any] = Field(default_factory=dict)
    physical_space_key: str | None = None
    physical_space_note: str | None = None
    spatial_continuity_mode: str | None = None
    spatial_reference_shot_ids: list[str] = Field(default_factory=list)
    spatial_structure_summary: str | None = None
    spatial_constraints: list[str] = Field(default_factory=list)
    spatial_movement_allowed: bool | None = None
    spatial_movement_reason: str | None = None
    spatial_plan_confidence: float | None = None
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


class StoryboardEpisodeOutput(BaseModel):
    episode_key: str
    shots: list[StoryboardShot]


class StoryboardShotGenerationOutput(BaseModel):
    episode_key: str
    shot: StoryboardShot
    is_episode_complete: bool = False
    completion_reason: str | None = None


class StoryboardNextShotOutput(BaseModel):
    episode_key: str
    shot: StoryboardShotDraft
    is_chapter_complete: bool = False
    completion_reason: str | None = None


class StoryboardGenerationOutput(BaseModel):
    generated_episodes: list[str]


class ShotDialogueAudioGenerationItem(BaseModel):
    episode_key: str
    shot_id: str
    asset: ShotDialogueAudioAsset


class ShotDialogueAudioGenerationOutput(BaseModel):
    generated_dialogue_audios: list[ShotDialogueAudioGenerationItem]
    skipped_dialogue_lines: list[dict[str, Any]] = Field(default_factory=list)


class RefFrameSpatialPlan(BaseModel):
    same_physical_space_as_previous: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    continuity_mode: Literal["previous_shot", "reuse_prior_space", "new_space"] = "new_space"
    physical_space_key: str = ""
    physical_space_note: str = ""
    reference_shot_ids: list[str] = Field(default_factory=list)
    spatial_structure_summary: str = ""
    spatial_constraints: list[str] = Field(default_factory=list)
    movement_allowed: bool = False
    movement_reason: str | None = None


class RefFrameGenerationItem(BaseModel):
    episode_key: str
    shot_id: str
    asset_id: str
    prompt: str
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    physical_space_key: str | None = None
    physical_space_note: str | None = None
    spatial_continuity_mode: str | None = None
    spatial_reference_shot_ids: list[str] = Field(default_factory=list)
    spatial_structure_summary: str | None = None
    spatial_constraints: list[str] = Field(default_factory=list)


class RefFrameGenerationOutput(BaseModel):
    generated_ref_frames: list[RefFrameGenerationItem]


class ShotVideoGenerationItem(BaseModel):
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
    generated_videos: list[ShotVideoGenerationItem]


class DynamicAssetSolidificationItem(BaseModel):
    asset_id: str
    asset_type: Literal["shot_dialogue_audio", "shot_bgm", "ref_frame", "shot_video"]
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
