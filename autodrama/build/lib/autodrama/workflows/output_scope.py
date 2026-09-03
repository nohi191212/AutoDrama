from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autodrama.core.schemas import ClipShotPlan, ClipToShotsEpisodeOutput, ProjectState


EXPECTED_OUTPUT_PREGEN_NODES = (
    "layout_to_background_prompt",
    "shot_background_shot_reference",
    "shot_background_image_generation",
    "shot_background_image_audit",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_keyframe_image_audit",
    "shot_manifest_generation",
)
EXPECTED_OUTPUT_GENERATION_NODES = (
    "shot_dialogue_audio_generation",
    "shot_video_generation",
    "shot_video_audit",
    "dynamic_asset_solidification",
)
EXPECTED_OUTPUT_POSTGEN_NODES = (
    "postgen_source_collect",
    "postgen_source_asr",
    "postgen_source_audit",
    "postgen_edit_plan_generation",
    "postgen_edit_plan_validation",
    "postgen_video_composition",
    "postgen_audio_separation",
    "postgen_speaker_diarization",
    "postgen_voice_conversion",
    "postgen_audio_remix",
    "postgen_subtitle_asr",
    "postgen_subtitle_render",
    "postgen_final_audit",
)
EXPECTED_OUTPUT_PENDING_METADATA_KEY = "expected_output_scope_pending_shot_ids"


@dataclass(slots=True)
class ExpectedOutputScopeSync:
    previous_expected_output_seconds: int | None
    expected_output_seconds: int
    config_changed: bool = False
    selection_changed: bool = False
    added_shot_ids_by_episode: dict[str, list[str]] = field(default_factory=dict)
    removed_shot_ids_by_episode: dict[str, list[str]] = field(default_factory=dict)
    invalidated_nodes: list[str] = field(default_factory=list)


class ExpectedOutputEpisodeSelection(BaseModel):
    """Deterministic whole-clip prefix selected from a normalized shot plan."""

    model_config = ConfigDict(extra="forbid")

    episode_key: str
    mode: Literal["all", "duration_prefix"]
    expected_output_seconds: int
    planned_output_seconds: float = Field(ge=0)
    total_planned_seconds: float = Field(ge=0)
    target_reached: bool
    overshoot_seconds: float = Field(default=0, ge=0)
    selected_clip_ids: list[str] = Field(default_factory=list)
    selected_shot_ids: list[str] = Field(default_factory=list)
    selected_clip_count: int = Field(ge=0)
    total_clip_count: int = Field(ge=0)
    selected_shot_count: int = Field(ge=0)
    total_shot_count: int = Field(ge=0)
    first_excluded_clip_id: str | None = None
    duration_source: Literal["clip_to_shots.normalized_shot_durations"] = (
        "clip_to_shots.normalized_shot_durations"
    )


class ExpectedOutputSelectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    expected_output_seconds: int
    episodes: list[ExpectedOutputEpisodeSelection] = Field(default_factory=list)


def _validate_expected_output_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected_output_seconds must be an integer")
    if value == 0 or value < -1:
        raise ValueError("expected_output_seconds must be -1 or a positive integer")
    return value


def _ordered_clips(plan: ClipToShotsEpisodeOutput) -> list[ClipShotPlan]:
    clips = sorted(plan.clips, key=lambda item: item.clip_index)
    if not clips:
        raise ValueError(f"clip_to_shots contains no clips for {plan.episode_key}")
    indexes = [clip.clip_index for clip in clips]
    if len(indexes) != len(set(indexes)):
        raise ValueError(f"clip_to_shots contains duplicate clip_index values for {plan.episode_key}")
    return clips


def _clip_duration_seconds(clip: ClipShotPlan) -> float:
    return float(sum(float(shot.duration_seconds) for shot in clip.shots))


def select_expected_output_prefix(
    plan: ClipToShotsEpisodeOutput,
    expected_output_seconds: int,
) -> ExpectedOutputEpisodeSelection:
    """Select the smallest complete clip prefix whose planned duration reaches the target."""

    expected = _validate_expected_output_seconds(expected_output_seconds)
    clips = _ordered_clips(plan)
    clip_durations = [_clip_duration_seconds(clip) for clip in clips]
    total_seconds = round(sum(clip_durations), 3)
    total_shots = sum(len(clip.shots) for clip in clips)

    if expected == -1:
        selected_clips = clips
        planned_seconds = total_seconds
        target_reached = True
        mode: Literal["all", "duration_prefix"] = "all"
    else:
        selected_clips = []
        running_seconds = 0.0
        for clip, duration in zip(clips, clip_durations):
            selected_clips.append(clip)
            running_seconds += duration
            if running_seconds + 1e-9 >= expected:
                break
        planned_seconds = round(running_seconds, 3)
        target_reached = planned_seconds + 1e-9 >= expected
        mode = "duration_prefix"

    selected_clip_ids = [clip.clip_id for clip in selected_clips]
    selected_shot_ids = [shot.shot_id for clip in selected_clips for shot in clip.shots]
    first_excluded = clips[len(selected_clips)].clip_id if len(selected_clips) < len(clips) else None
    overshoot = 0.0 if expected == -1 else max(0.0, planned_seconds - expected)
    return ExpectedOutputEpisodeSelection(
        episode_key=plan.episode_key,
        mode=mode,
        expected_output_seconds=expected,
        planned_output_seconds=planned_seconds,
        total_planned_seconds=total_seconds,
        target_reached=target_reached,
        overshoot_seconds=round(overshoot, 3),
        selected_clip_ids=selected_clip_ids,
        selected_shot_ids=selected_shot_ids,
        selected_clip_count=len(selected_clips),
        total_clip_count=len(clips),
        selected_shot_count=len(selected_shot_ids),
        total_shot_count=total_shots,
        first_excluded_clip_id=first_excluded,
    )


def persist_expected_output_selection(
    repo: Any,
    project_dir: Path,
    selection: ExpectedOutputEpisodeSelection,
) -> Path:
    path = repo.layout.expected_output_selection_path(project_dir)
    existing_episodes: list[ExpectedOutputEpisodeSelection] = []
    if path.exists():
        try:
            existing = ExpectedOutputSelectionOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            existing = None
        if (
            existing is not None
            and existing.expected_output_seconds == selection.expected_output_seconds
        ):
            existing_episodes = list(existing.episodes)

    by_episode = {item.episode_key: item for item in existing_episodes}
    by_episode[selection.episode_key] = selection
    output = ExpectedOutputSelectionOutput(
        expected_output_seconds=selection.expected_output_seconds,
        episodes=[by_episode[key] for key in sorted(by_episode)],
    )
    repo.write_json(path, output)
    return path


def configured_episode_output_selection(
    repo: Any,
    project_dir: Path,
    plan: ClipToShotsEpisodeOutput,
    *,
    persist: bool = True,
) -> ExpectedOutputEpisodeSelection:
    expected = repo.settings.generation.expected_output_seconds
    selection = select_expected_output_prefix(plan, expected)
    if persist:
        persist_expected_output_selection(repo, project_dir, selection)
    return selection


def load_configured_episode_output_selection(
    repo: Any,
    project_dir: Path,
    episode_key: str,
    *,
    persist: bool = True,
) -> ExpectedOutputEpisodeSelection:
    path = repo.layout.node_episode_output_path(project_dir, "clip_to_shots", episode_key)
    if not path.exists():
        raise FileNotFoundError(
            f"expected_output_seconds requires clip_to_shots output for {episode_key}: {path}"
        )
    plan = ClipToShotsEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
    return configured_episode_output_selection(repo, project_dir, plan, persist=persist)


def configured_output_shot_ids(
    repo: Any,
    project_dir: Path,
    episode_keys: list[str],
) -> tuple[set[str], list[ExpectedOutputEpisodeSelection]]:
    selections = [
        load_configured_episode_output_selection(repo, project_dir, episode_key)
        for episode_key in episode_keys
    ]
    shot_ids = {
        shot_id
        for selection in selections
        for shot_id in selection.selected_shot_ids
    }
    return shot_ids, selections


def _stored_expected_output_seconds(
    repo: Any,
    project_dir: Path,
    state: ProjectState,
) -> int | None:
    value = state.metadata.get("expected_output_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and (value == -1 or value > 0):
        return value
    selection_path = repo.layout.expected_output_selection_path(project_dir)
    output = None
    if selection_path.exists():
        try:
            output = ExpectedOutputSelectionOutput.model_validate_json(
                selection_path.read_text(encoding="utf-8")
            )
        except Exception:
            output = None
    if output is not None:
        return output.expected_output_seconds
    # Projects created before this setting existed were full-output projects.
    # Infer that legacy scope only when a completed full clip plan is present.
    legacy_plan_dir = project_dir / "assets" / "json" / "nodes" / "clip_to_shots"
    if (
        "clip_to_shots" in state.completed_nodes
        and legacy_plan_dir.exists()
        and any(legacy_plan_dir.glob("*.json"))
    ):
        return -1
    return None


def _episode_plans(
    repo: Any,
    project_dir: Path,
    episode_keys: list[str],
) -> dict[str, ClipToShotsEpisodeOutput]:
    plans: dict[str, ClipToShotsEpisodeOutput] = {}
    for episode_key in episode_keys:
        path = repo.layout.node_episode_output_path(
            project_dir,
            "clip_to_shots",
            episode_key,
        )
        if path.exists():
            plans[episode_key] = ClipToShotsEpisodeOutput.model_validate_json(
                path.read_text(encoding="utf-8")
            )
    return plans


def pending_expected_output_shot_ids(
    state: ProjectState,
    episode_keys: list[str] | None = None,
) -> dict[str, list[str]]:
    raw = state.metadata.get(EXPECTED_OUTPUT_PENDING_METADATA_KEY)
    if not isinstance(raw, dict):
        return {}
    wanted = set(episode_keys) if episode_keys is not None else None
    pending: dict[str, list[str]] = {}
    for episode_key, values in raw.items():
        key = str(episode_key)
        if wanted is not None and key not in wanted:
            continue
        if not isinstance(values, list):
            continue
        shot_ids = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if shot_ids:
            pending[key] = shot_ids
    return pending


def clear_pending_expected_output_shot_ids(
    state: ProjectState,
    episode_keys: list[str],
) -> None:
    pending = pending_expected_output_shot_ids(state)
    for episode_key in episode_keys:
        pending.pop(episode_key, None)
    if pending:
        state.metadata[EXPECTED_OUTPUT_PENDING_METADATA_KEY] = pending
    else:
        state.metadata.pop(EXPECTED_OUTPUT_PENDING_METADATA_KEY, None)


def synchronize_expected_output_scope(
    repo: Any,
    project_dir: Path,
    state: ProjectState,
    episode_keys: list[str],
) -> ExpectedOutputScopeSync:
    """Persist a changed duration scope and invalidate only its deterministic dependents.

    The clip segmentation and clip-to-shots plans deliberately remain valid. When
    a scope expands, the newly included shot IDs are kept as a pending selector so
    the pre-generation workflow can append assets without regenerating the already
    completed prefix.
    """

    current = _validate_expected_output_seconds(
        repo.settings.generation.expected_output_seconds
    )
    previous = _stored_expected_output_seconds(repo, project_dir, state)
    initialized = previous is None
    changed = previous is not None and previous != current
    result = ExpectedOutputScopeSync(
        previous_expected_output_seconds=previous,
        expected_output_seconds=current,
        config_changed=changed,
    )
    state.metadata["expected_output_seconds"] = current

    if not changed:
        if initialized:
            repo.save_state(project_dir, state)
        return result

    plans = _episode_plans(repo, project_dir, episode_keys)
    selected_ids_by_episode: dict[str, list[str]] = {}
    selection_summaries = state.metadata.get("expected_output_selection")
    if not isinstance(selection_summaries, dict):
        selection_summaries = {}
        state.metadata["expected_output_selection"] = selection_summaries
    for episode_key in episode_keys:
        plan = plans.get(episode_key)
        if plan is None:
            continue
        old_selection = select_expected_output_prefix(plan, previous)
        new_selection = select_expected_output_prefix(plan, current)
        persist_expected_output_selection(repo, project_dir, new_selection)
        selection_summaries[episode_key] = {
            "planned_output_seconds": new_selection.planned_output_seconds,
            "selected_clip_count": new_selection.selected_clip_count,
            "selected_shot_count": new_selection.selected_shot_count,
            "target_reached": new_selection.target_reached,
        }
        old_ids = set(old_selection.selected_shot_ids)
        new_ids = set(new_selection.selected_shot_ids)
        added = [
            shot_id
            for shot_id in new_selection.selected_shot_ids
            if shot_id not in old_ids
        ]
        removed = [
            shot_id
            for shot_id in old_selection.selected_shot_ids
            if shot_id not in new_ids
        ]
        selected_ids_by_episode[episode_key] = new_selection.selected_shot_ids
        if added:
            result.added_shot_ids_by_episode[episode_key] = added
        if removed:
            result.removed_shot_ids_by_episode[episode_key] = removed
    if plans:
        state.metadata["expected_output_selection_path"] = repo.layout.project_relative(
            project_dir,
            repo.layout.expected_output_selection_path(project_dir),
        )

    result.selection_changed = bool(
        result.added_shot_ids_by_episode or result.removed_shot_ids_by_episode
    )

    existing_pending = pending_expected_output_shot_ids(state)
    pending: dict[str, list[str]] = {}
    for episode_key, selected_ids in selected_ids_by_episode.items():
        selected_set = set(selected_ids)
        merged = [
            *existing_pending.get(episode_key, []),
            *result.added_shot_ids_by_episode.get(episode_key, []),
        ]
        retained = list(
            dict.fromkeys(shot_id for shot_id in merged if shot_id in selected_set)
        )
        if retained:
            pending[episode_key] = retained
    for episode_key, values in existing_pending.items():
        if episode_key not in selected_ids_by_episode and values:
            pending[episode_key] = values
    if pending:
        state.metadata[EXPECTED_OUTPUT_PENDING_METADATA_KEY] = pending
    else:
        state.metadata.pop(EXPECTED_OUTPUT_PENDING_METADATA_KEY, None)

    invalidated = set(EXPECTED_OUTPUT_POSTGEN_NODES if result.selection_changed else ())
    if result.added_shot_ids_by_episode:
        invalidated.update(EXPECTED_OUTPUT_PREGEN_NODES)
        invalidated.update(EXPECTED_OUTPUT_GENERATION_NODES)
    result.invalidated_nodes = [
        node_name for node_name in state.completed_nodes if node_name in invalidated
    ]
    if invalidated:
        state.completed_nodes = [
            node_name for node_name in state.completed_nodes if node_name not in invalidated
        ]
        if state.current_node in invalidated:
            state.current_node = state.completed_nodes[-1] if state.completed_nodes else None

    state.metadata["expected_output_scope_change"] = {
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "previous_expected_output_seconds": previous,
        "expected_output_seconds": current,
        "selection_changed": result.selection_changed,
        "added_shot_ids_by_episode": result.added_shot_ids_by_episode,
        "removed_shot_ids_by_episode": result.removed_shot_ids_by_episode,
        "invalidated_nodes": result.invalidated_nodes,
    }
    repo.save_state(project_dir, state)

    if result.added_shot_ids_by_episode:
        from autodrama.workflows.generation_checklist import (
            mark_episode_keys_for_regeneration,
        )

        mark_episode_keys_for_regeneration(
            repo,
            project_dir,
            list(result.added_shot_ids_by_episode),
            expected_output_seconds=current,
        )
    return result


__all__ = [
    "EXPECTED_OUTPUT_GENERATION_NODES",
    "EXPECTED_OUTPUT_PENDING_METADATA_KEY",
    "EXPECTED_OUTPUT_POSTGEN_NODES",
    "EXPECTED_OUTPUT_PREGEN_NODES",
    "ExpectedOutputEpisodeSelection",
    "ExpectedOutputScopeSync",
    "ExpectedOutputSelectionOutput",
    "clear_pending_expected_output_shot_ids",
    "configured_episode_output_selection",
    "configured_output_shot_ids",
    "load_configured_episode_output_selection",
    "pending_expected_output_shot_ids",
    "persist_expected_output_selection",
    "select_expected_output_prefix",
    "synchronize_expected_output_scope",
]
