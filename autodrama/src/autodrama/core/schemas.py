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


class ClipPromptModelOutput(BaseModel):
    clip_prompt: str
    target_duration_seconds: int


class ClipPromptItem(BaseModel):
    episode_key: str
    clip_id: str
    clip_index: int
    source_clip_key: str
    clip_text: str
    role_names: list[str] = Field(default_factory=list)
    layout_names: list[str] = Field(default_factory=list)
    prop_names: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    layout_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    target_duration_seconds: int
    clip_prompt: str
    reference_image_context: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ClipPromptEpisode(BaseModel):
    episode_key: str
    clips: list[ClipPromptItem] = Field(default_factory=list)


class ClipPromptOutput(BaseModel):
    clip_prompts: list[ClipPromptEpisode]


class StoryboardPromptClip(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    clip_id: str = Field(validation_alias=AliasChoices("clip_id", "shot_id"))
    clip_title: str | None = None
    clip_duration_hint: str | None = None
    clip_text: str | None = None
    duration_seconds: float
    role_ids: list[str] = Field(default_factory=list)
    layout_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    camera_shots: list[dict[str, Any]] = Field(default_factory=list)
    panel_plan: dict[str, Any] = Field(default_factory=dict)
    video_prompt: str
    negative_prompt: str | None = None

    @property
    def shot_id(self) -> str:
        return self.clip_id

    @shot_id.setter
    def shot_id(self, value: str) -> None:
        self.clip_id = value


StoryboardPromptShot = StoryboardPromptClip


class StoryboardPromptEpisode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_key: str
    clips: list[StoryboardPromptClip] = Field(default_factory=list, validation_alias=AliasChoices("clips", "shots"))

    @property
    def shots(self) -> list[StoryboardPromptClip]:
        return self.clips

    @shots.setter
    def shots(self, value: list[StoryboardPromptClip]) -> None:
        self.clips = value


class StoryboardPromptOutput(BaseModel):
    storyboards: list[StoryboardPromptEpisode]


class StoryboardSheetGenerationItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_key: str
    clip_id: str = Field(validation_alias=AliasChoices("clip_id", "shot_id"))
    asset_id: str
    prompt: str
    duration_seconds: float | None = None
    panel_count: int = 12
    grid: str = "4x3"
    panel_aspect_ratio: str = "3:4"
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)

    @property
    def shot_id(self) -> str:
        return self.clip_id

    @shot_id.setter
    def shot_id(self, value: str) -> None:
        self.clip_id = value


class StoryboardSheetGenerationOutput(BaseModel):
    generated_storyboards: list[StoryboardSheetGenerationItem]


class StoryboardKeyframeGenerationItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_key: str
    clip_id: str = Field(validation_alias=AliasChoices("clip_id", "shot_id"))
    frame_role: Literal["start", "end"] | str
    panel_ref: str
    source_storyboard_asset_id: str
    prompt: str
    asset_id: str
    asset_path: str | None = None
    asset_url: str | None = None
    provider: str
    model: str
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("response", "raw_response"))
    usage: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None

    @property
    def shot_id(self) -> str:
        return self.clip_id

    @shot_id.setter
    def shot_id(self, value: str) -> None:
        self.clip_id = value


class StoryboardKeyframeGenerationOutput(BaseModel):
    generated_keyframes: list[StoryboardKeyframeGenerationItem]


class PropExtractItem(BaseModel):
    name: str
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)


class PropExtractOutput(BaseModel):
    generated_prop_intro: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_props(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "generated_prop_intro" in value:
            return value
        props = value.get("props")
        if not isinstance(props, list):
            return value
        generated_prop_intro: dict[str, str] = {}
        for item in props:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            intro = str(item.get("brief") or item.get("desc") or "").strip()
            notes = item.get("appearance_notes")
            if not intro and isinstance(notes, list):
                intro = "；".join(str(note).strip() for note in notes if str(note).strip())
            generated_prop_intro[name] = intro
        return {**value, "generated_prop_intro": generated_prop_intro}


class PropDedupeOutput(BaseModel):
    generated_prop_intro: dict[str, str] = Field(default_factory=dict)
    merge_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_props(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "generated_prop_intro" in value:
            return value
        props = value.get("props")
        if not isinstance(props, list):
            return value
        generated_prop_intro: dict[str, str] = {}
        for item in props:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            intro = str(item.get("desc") or item.get("brief") or "").strip()
            if name:
                generated_prop_intro[name] = intro
        return {**value, "generated_prop_intro": generated_prop_intro}


class PropDesignItem(BaseModel):
    name: str
    desc: str
    prompt: str
    status: str = "normal"
    episode_keys: list[str] = Field(default_factory=list)


class PropDesignOutput(BaseModel):
    props: list[PropDesignItem]


class PropPromptOutput(BaseModel):
    prop_prompts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_props(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "prop_prompts" in value:
            return value
        props = value.get("props")
        if not isinstance(props, list):
            return value
        prop_prompts: dict[str, str] = {}
        for item in props:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if name and prompt:
                prop_prompts[name] = prompt
        return {**value, "prop_prompts": prop_prompts}


class LayoutExtractItem(BaseModel):
    name: str
    episode_keys: list[str] = Field(default_factory=list)
    source_chapters: list[str] = Field(default_factory=list)
    brief: str | None = None
    appearance_notes: list[str] = Field(default_factory=list)


class LayoutExtractOutput(BaseModel):
    generated_layout_intro: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_layouts(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "generated_layout_intro" in value:
            return value
        layouts = value.get("layouts")
        if not isinstance(layouts, list):
            return value
        generated_layout_intro: dict[str, str] = {}
        for item in layouts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            intro = str(item.get("brief") or item.get("desc") or "").strip()
            notes = item.get("appearance_notes")
            if not intro and isinstance(notes, list):
                intro = "；".join(str(note).strip() for note in notes if str(note).strip())
            generated_layout_intro[name] = intro
        return {**value, "generated_layout_intro": generated_layout_intro}


class LayoutPromptOutput(BaseModel):
    layout_prompts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_layouts(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "layout_prompts" in value:
            return value
        layouts = value.get("layouts")
        if not isinstance(layouts, list):
            return value
        layout_prompts: dict[str, str] = {}
        for item in layouts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if name and prompt:
                layout_prompts[name] = prompt
        return {**value, "layout_prompts": layout_prompts}


class LayoutDedupeReviewOutput(BaseModel):
    generated_layout_intro: dict[str, str] = Field(default_factory=dict)
    merge_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_layouts(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "generated_layout_intro" in value:
            return value
        layouts = value.get("layouts")
        if not isinstance(layouts, list):
            return value
        generated_layout_intro: dict[str, str] = {}
        for item in layouts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            intro = str(item.get("desc") or item.get("brief") or "").strip()
            if name:
                generated_layout_intro[name] = intro
        return {**value, "generated_layout_intro": generated_layout_intro}


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


class ClipVideoInput(BaseModel):
    slot: str
    type: Literal["image"] = "image"
    asset_type: Literal["clip_start_frame", "clip_end_frame", "storyboard", "roleboard", "layout", "prop"] | str
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


class StoryboardClip(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    clip_id: str = Field(validation_alias=AliasChoices("clip_id", "shot_id"))
    index: int
    layout_id: str | None = None
    layout_ids: list[str] = Field(default_factory=list)
    title: str
    segment_index: int | None = None
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
    start_frame_source: Literal["new_reference_frame", "previous_shot_last_frame", "own_start_frame", "previous_clip_end_frame"] | str = "new_reference_frame"
    start_frame_inheritance_reason: str | None = None
    dialogue: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    role_appearance_ids: list[str] = Field(default_factory=list)
    role_audio_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    storyboard_asset_id: str | None = None
    storyboard_asset_path: str | None = None
    source_storyboard_asset_path: str | None = None
    video_prompt: str
    final_video_prompt: str | None = None
    clip_video_inputs: list[ClipVideoInput] = Field(default_factory=list)
    start_frame_asset_id: str | None = None
    start_frame_asset_path: str | None = None
    start_frame_asset_url: str | None = None
    start_frame_source_clip_id: str | None = None
    end_frame_asset_id: str | None = None
    end_frame_asset_path: str | None = None
    end_frame_asset_url: str | None = None
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

    @property
    def shot_id(self) -> str:
        return self.clip_id

    @shot_id.setter
    def shot_id(self, value: str) -> None:
        self.clip_id = value


StoryboardShot = StoryboardClip


class StoryboardEpisodeOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_key: str
    clips: list[StoryboardClip] = Field(default_factory=list, validation_alias=AliasChoices("clips", "shots"))

    @property
    def shots(self) -> list[StoryboardClip]:
        return self.clips

    @shots.setter
    def shots(self, value: list[StoryboardClip]) -> None:
        self.clips = value


class ClipManifestGenerationEpisodeItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_key: str
    clip_count: int = Field(validation_alias=AliasChoices("clip_count", "shot_count"))
    clip_path: str = Field(validation_alias=AliasChoices("clip_path", "shot_path"))
    warnings: list[str] = Field(default_factory=list)

    @property
    def shot_count(self) -> int:
        return self.clip_count

    @shot_count.setter
    def shot_count(self, value: int) -> None:
        self.clip_count = value

    @property
    def shot_path(self) -> str:
        return self.clip_path

    @shot_path.setter
    def shot_path(self, value: str) -> None:
        self.clip_path = value


class ClipManifestGenerationOutput(BaseModel):
    episodes: list[ClipManifestGenerationEpisodeItem]


class ShotDialogueAudioGenerationItem(BaseModel):
    episode_key: str
    shot_id: str
    asset: ShotDialogueAudioAsset


class ShotDialogueAudioGenerationOutput(BaseModel):
    generated_dialogue_audios: list[ShotDialogueAudioGenerationItem]
    skipped_dialogue_lines: list[dict[str, Any]] = Field(default_factory=list)


class ClipVideoGenerationItem(BaseModel):
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


class ClipVideoGenerationOutput(BaseModel):
    generated_videos: list[ClipVideoGenerationItem]


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


class DynamicAssetSolidificationItem(BaseModel):
    asset_id: str
    asset_type: Literal["shot_dialogue_audio", "shot_bgm", "clip_video"]
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
