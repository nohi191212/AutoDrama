from __future__ import annotations

from pathlib import Path

from autodrama.postgen.schemas import (
    POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
    PostgenEditPlan,
    PostgenOutputSpec,
    PostgenSourceClip,
    PostgenTimelineItem,
)


def _inside_project(project_dir: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(project_dir.resolve())
        return True
    except ValueError:
        return False


def resolve_project_path(project_dir: Path, path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    candidate = candidate.resolve()
    if not _inside_project(project_dir, candidate):
        raise ValueError(f"Postgen path must stay inside project_dir: {path}")
    return candidate


def _project_relative(project_dir: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_dir.resolve())).replace("\\", "/")


def estimate_timeline_duration(plan: PostgenEditPlan) -> float:
    total = 0.0
    for item in plan.timeline:
        total += (item.source_out - item.source_in) / item.speed
    return round(total, 3)


def validate_edit_plan(
    raw_plan: PostgenEditPlan,
    *,
    project_dir: Path,
    episode_key: str,
    expected_source_clips: list[PostgenSourceClip],
    output_path: str,
    width: int,
    height: int,
    fps: int,
) -> PostgenEditPlan:
    if raw_plan.schema_version != POSTGEN_EDIT_PLAN_SCHEMA_VERSION:
        raise ValueError(f"Unsupported postgen edit plan schema: {raw_plan.schema_version}")
    if raw_plan.episode_key != episode_key:
        raise ValueError(f"Edit plan episode_key mismatch: expected {episode_key}, got {raw_plan.episode_key}")

    expected_by_id = {clip.shot_id: clip for clip in expected_source_clips}
    if not 1 <= len(expected_by_id) < 10:
        raise ValueError("postgen source_clips must contain 1-9 usable clips")
    if not raw_plan.timeline:
        raise ValueError("postgen edit plan timeline is empty")

    seen_clip_ids: set[str] = set()
    timeline: list[PostgenTimelineItem] = []
    for index, item in enumerate(raw_plan.timeline, start=1):
        clip_id = item.clip_id.strip() or f"{episode_key}_cut_{index:03d}"
        if clip_id in seen_clip_ids:
            raise ValueError(f"Duplicate timeline clip_id: {clip_id}")
        seen_clip_ids.add(clip_id)

        source = expected_by_id.get(item.shot_id)
        if source is None:
            raise ValueError(f"Timeline item {clip_id} references unknown shot_id: {item.shot_id}")
        if item.source_in < 0:
            raise ValueError(f"{clip_id}.source_in must be >= 0")
        if item.source_out <= item.source_in:
            raise ValueError(f"{clip_id}.source_out must be greater than source_in")
        if item.source_out > source.duration_seconds + 0.05:
            raise ValueError(
                f"{clip_id}.source_out {item.source_out:.3f}s exceeds source duration "
                f"{source.duration_seconds:.3f}s for {item.shot_id}"
            )
        if item.speed < 0.5 or item.speed > 2.0:
            raise ValueError(f"{clip_id}.speed must be between 0.5 and 2.0")
        if item.transition_after.type != "cut":
            raise ValueError(f"{clip_id}.transition_after.type only supports cut in v1")
        timeline.append(item.model_copy(update={"clip_id": clip_id}))

    output_resolved = resolve_project_path(project_dir, output_path)
    raw_output_path = raw_plan.output.path if raw_plan.output and raw_plan.output.path else output_path
    raw_output_resolved = resolve_project_path(project_dir, raw_output_path)
    if raw_output_resolved != output_resolved:
        warnings = list(raw_plan.warnings)
        warnings.append(f"output.path was reset from {raw_plan.output.path!r} to configured path {output_path!r}")
    else:
        warnings = list(raw_plan.warnings)

    source_clips = [expected_by_id[clip.shot_id] for clip in expected_source_clips if clip.shot_id in expected_by_id]
    validated = PostgenEditPlan(
        schema_version=POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
        episode_key=episode_key,
        source_clips=source_clips,
        timeline=timeline,
        output=PostgenOutputSpec(
            path=_project_relative(project_dir, output_resolved),
            width=width,
            height=height,
            fps=fps,
            burn_subtitles=raw_plan.output.burn_subtitles,
            audio=raw_plan.output.audio,
        ),
        audio_layers=list(raw_plan.audio_layers),
        subtitle_cues=list(raw_plan.subtitle_cues),
        warnings=warnings,
        metadata={
            **dict(raw_plan.metadata),
            "estimated_duration_seconds": estimate_timeline_duration(
                raw_plan.model_copy(update={"timeline": timeline})
            ),
        },
    )
    if estimate_timeline_duration(validated) <= 0:
        raise ValueError("postgen edit plan estimated duration must be positive")
    return validated


__all__ = ["estimate_timeline_duration", "resolve_project_path", "validate_edit_plan"]
