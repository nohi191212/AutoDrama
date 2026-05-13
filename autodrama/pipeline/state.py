"""LangGraph state definition for the AutoDrama pipeline."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class DramaState(TypedDict):
    """State that flows through the LangGraph pipeline.

    Each node reads relevant keys and returns a partial dict to merge back.
    """

    # === Input ===
    docx_path: NotRequired[str]  # Path to .docx input document
    concept: NotRequired[str]  # High-level concept or raw text from docx

    # === Script registries (SCRIPT singleton) ===
    raw_script: NotRequired[str]  # SCRIPT.raw_script — raw document text
    detailed_script: NotRequired[dict]  # SCRIPT.detailed_script = {episode_N: text}
    final_script: NotRequired[dict]  # SCRIPT.final_script (after rebuttle polishing)
    simple_script: NotRequired[dict]  # SCRIPT.simple_script = {episode_N: compressed}
    global_script: NotRequired[str]  # SCRIPT.global_script — overall summary

    # === Character registries (ROLES) ===
    roles: NotRequired[dict]  # ROLES['name'] = {intro, audio: {emotion: {desc, path}}, appearance: {variant: {desc, image}}}

    # === Props registry (PROPS) ===
    props: NotRequired[dict]  # PROPS['name'] = {desc, image}

    # === Scene / Layout registry (DESIGN_LAYOUT) ===
    design_layout: NotRequired[dict]  # DESIGN_LAYOUT['scene_name'] = {variant_name: {desc, image}}

    # === BGM registry (BGMS) ===
    bgms: NotRequired[list[dict]]  # BGMS = [{track_name, file_path, category, volume, loop}]

    # === Storyboard registry (STORYBOARDS) ===
    storyboards: NotRequired[dict]  # STORYBOARDS['episode_X']['shot_Y'] = Shot data

    # === Legacy / simplified stage outputs (kept for backward compat) ===
    script: NotRequired[dict | None]  # Script.model_dump()
    scene_plans: NotRequired[list[dict] | None]  # [ScenePlan.model_dump()]
    image_assets: NotRequired[list[dict] | None]  # [ImageAsset.model_dump()]
    audio_assets: NotRequired[list[dict] | None]  # [AudioAsset.model_dump()]
    video_segments: NotRequired[list[dict] | None]  # [VideoSegment.model_dump()]
    final_video_path: NotRequired[str | None]

    # === Editing stage outputs ===
    editing_plan: NotRequired[dict | None]  # Structured editing plan from LLM
    scoring_plan: NotRequired[dict | None]  # BGM scoring plan from LLM
    av_separated: NotRequired[bool]  # Whether audio-visual separation was applied

    # === Pipeline metadata ===
    errors: NotRequired[list[dict]]  # {stage, error, retry_count}
    warnings: NotRequired[list[str]]
    current_stage: NotRequired[str]
    retry_count: NotRequired[int]
    max_retries: NotRequired[int]

    # === Loop control for dynamic generation ===
    remaining_episodes: NotRequired[list[str]]  # Episodes still to process
    current_episode: NotRequired[str]  # Currently processing episode
    loop_count: NotRequired[int]  # Iteration counter for storyboard→solidify loop
