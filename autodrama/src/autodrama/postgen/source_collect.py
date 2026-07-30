from __future__ import annotations

from pathlib import Path

from autodrama.core.schemas import ShotManifestEpisodeOutput, VideoAssetAuditOutput
from autodrama.postgen.schemas import PostgenSourceClip
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.selection import active_shots_for_episode


def project_relative(project_dir: Path, path: Path) -> str:
    return str(path.relative_to(project_dir)).replace("\\", "/")


def resolve_project_path(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def collect_episode_source_clips(
    repo: ProjectRepository,
    project_dir: Path,
    episode_key: str,
    *,
    shot_selectors: list[str] | set[str] | None = None,
    max_clips: int | None = 9,
) -> tuple[list[PostgenSourceClip], list[str]]:
    shot_path = repo.layout.shot_path(project_dir, episode_key)
    if not shot_path.exists():
        raise FileNotFoundError(f"Shot manifest is missing for {episode_key}: {shot_path}")

    episode = ShotManifestEpisodeOutput.model_validate_json(shot_path.read_text(encoding="utf-8"))
    audit_path = repo.layout.node_output_path(project_dir, "shot_video_audit")
    if not audit_path.exists():
        raise FileNotFoundError(
            f"Postgen source collection requires accepted shot video audits: {audit_path}"
        )
    audit_output = VideoAssetAuditOutput.model_validate_json(audit_path.read_text(encoding="utf-8"))
    accepted_audits = {
        item.shot_id: item
        for item in audit_output.audited_videos
        if item.episode_key == episode_key and item.approved
    }
    warnings: list[str] = []
    clips: list[PostgenSourceClip] = []
    for shot in sorted(active_shots_for_episode(episode, shot_selectors), key=lambda item: item.index):
        audit = accepted_audits.get(shot.shot_id)
        if audit is None or audit.asset_id != shot.video_asset_id:
            warnings.append(f"{shot.shot_id}: video audit missing, rejected, or for a stale asset")
            continue
        source_path = resolve_project_path(project_dir, shot.video_asset_path)
        if source_path is None or not source_path.is_file() or source_path.stat().st_size <= 0:
            warnings.append(f"{shot.shot_id}: missing shot video asset; run generation through shot_video_generation")
            continue
        if max_clips is not None and len(clips) >= max_clips:
            warnings.append(f"clip limit reached: using first {max_clips} usable clips for {episode_key}")
            break
        clips.append(
            PostgenSourceClip(
                episode_key=episode_key,
                shot_id=shot.shot_id,
                shot_index=shot.index,
                title=shot.title,
                source_path=project_relative(project_dir, source_path),
                duration_seconds=max(0.25, float(shot.duration_seconds or 0)),
                dialogue_lines=[line.text for line in shot.dialogue_lines],
                role_ids=list(shot.role_ids),
                prop_ids=list(shot.prop_ids),
                content=shot.content,
                video_prompt=shot.video_prompt,
                camera_movement=shot.camera_movement,
                transition_hint=shot.transition,
                video_audit_status="accepted",
                quality_tier="deliverable",
                allowed_ranges=[(0.0, max(0.25, float(shot.duration_seconds or 0)))],
                expected_dialogue=[line.text for line in shot.dialogue_lines],
                gate_provenance={
                    "audit_node": "shot_video_audit",
                    "asset_id": audit.asset_id,
                    "attempts": audit.attempts,
                },
            )
        )
    if not clips:
        detail = "; ".join(warnings) or f"No usable clips found for {episode_key}"
        raise ValueError(detail)
    return clips, warnings


__all__ = ["collect_episode_source_clips", "project_relative", "resolve_project_path"]
