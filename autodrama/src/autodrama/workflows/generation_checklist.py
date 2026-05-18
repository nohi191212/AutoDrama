from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from autodrama.core.schemas import ProjectState, StoryboardEpisodeOutput
from autodrama.repositories.project_repo import ProjectRepository

CHECKLIST_FILENAME = "generation_checklist.json"

EpisodeGenerationStatus = Literal["pending", "completed", "skipped", "failed", "missing_slot"]

DYNAMIC_STATUS_FIELDS = (
    "dialogue_audio",
    "ref_frame",
    "shot_video",
    "solidified",
)


def checklist_path(project_dir: Path) -> Path:
    return project_dir / CHECKLIST_FILENAME


def _episode_display_name(state: ProjectState, episode_key: str) -> str:
    outline = state.script.episode_outlines.get(episode_key) or state.script.final_script.get(episode_key) or ""
    first_line = str(outline).strip().splitlines()[0] if str(outline).strip() else ""
    if first_line:
        return first_line[:80]
    try:
        index = int(episode_key.rsplit("_", 1)[1])
        return f"第{index}集"
    except (IndexError, ValueError):
        return episode_key


def _expected_episode_keys(state: ProjectState) -> list[str]:
    count = int(state.metadata.get("episode_count") or 1)
    return [f"episode_{index:03d}" for index in range(1, count + 1)]


def _load_slot(project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput | None:
    path = project_dir / "slots" / f"{episode_key}.json"
    if not path.exists():
        return None
    return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))


def _episode_status(project_dir: Path, episode_key: str) -> dict[str, EpisodeGenerationStatus]:
    episode = _load_slot(project_dir, episode_key)
    if episode is None:
        return {field: "missing_slot" for field in DYNAMIC_STATUS_FIELDS}
    if not episode.shots:
        return {field: "pending" for field in DYNAMIC_STATUS_FIELDS}

    has_dialogue = any(shot.dialogue for shot in episode.shots)
    dialogue_done = all(
        (not shot.dialogue) or bool(shot.dialogue_audio_assets)
        for shot in episode.shots
    )
    ref_done = all(bool(shot.ref_frame_asset_path) for shot in episode.shots)
    video_done = all(bool(shot.video_asset_path or shot.video_task_id) for shot in episode.shots)
    solidified_done = all(bool(shot.solidified_asset_ids) for shot in episode.shots)
    return {
        "dialogue_audio": "completed" if (not has_dialogue or dialogue_done) else "pending",
        "ref_frame": "completed" if ref_done else "pending",
        "shot_video": "completed" if video_done else "pending",
        "solidified": "completed" if solidified_done else "pending",
    }


def _generated_counts(project_dir: Path, episode_key: str) -> dict[str, int]:
    episode = _load_slot(project_dir, episode_key)
    if episode is None:
        return {
            "shots": 0,
            "dialogue_audios": 0,
            "ref_frames": 0,
            "shot_videos": 0,
            "solidified_assets": 0,
        }
    return {
        "shots": len(episode.shots),
        "dialogue_audios": sum(len(shot.dialogue_audio_assets) for shot in episode.shots),
        "ref_frames": sum(1 for shot in episode.shots if shot.ref_frame_asset_path),
        "shot_videos": sum(1 for shot in episode.shots if shot.video_asset_path or shot.video_task_id),
        "solidified_assets": sum(len(shot.solidified_asset_ids) for shot in episode.shots),
    }


def load_checklist(project_dir: Path) -> dict[str, Any]:
    path = checklist_path(project_dir)
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def write_checklist(repo: ProjectRepository, project_dir: Path, checklist: dict[str, Any]) -> None:
    repo.write_json(checklist_path(project_dir), checklist)


def update_checklist_from_state(
    repo: ProjectRepository,
    project_dir: Path,
    state: ProjectState,
    *,
    default_generate: bool = True,
    processed_episode_keys: set[str] | None = None,
    failed_episode_keys: set[str] | None = None,
) -> dict[str, Any]:
    existing = load_checklist(project_dir)
    existing_items = {
        str(item.get("episode_key")): item
        for item in existing.get("episodes", [])
        if isinstance(item, dict) and item.get("episode_key")
    }
    now = datetime.now().isoformat(timespec="seconds")
    episodes: list[dict[str, Any]] = []
    processed_episode_keys = processed_episode_keys or set()
    failed_episode_keys = failed_episode_keys or set()

    for index, episode_key in enumerate(_expected_episode_keys(state), start=1):
        previous = existing_items.get(episode_key, {})
        status = _episode_status(project_dir, episode_key)
        counts = _generated_counts(project_dir, episode_key)
        node_status: EpisodeGenerationStatus
        if episode_key in failed_episode_keys:
            node_status = "failed"
        elif episode_key in processed_episode_keys:
            node_status = "completed"
        elif status["shot_video"] == "completed" and status["ref_frame"] == "completed":
            node_status = "completed"
        elif previous.get("generation_status") == "skipped":
            node_status = "skipped"
        else:
            node_status = "pending"

        generate = bool(previous.get("generate", default_generate))
        if episode_key in processed_episode_keys and episode_key not in failed_episode_keys:
            generate = False

        last_generated_at = previous.get("last_generated_at")
        if episode_key in processed_episode_keys and episode_key not in failed_episode_keys:
            last_generated_at = now

        episodes.append(
            {
                "episode_key": episode_key,
                "episode_id": episode_key,
                "episode_index": index,
                "name": _episode_display_name(state, episode_key),
                "slot_path": f"slots/{episode_key}.json",
                "generate": generate,
                "generation_status": node_status,
                "node_status": status,
                "generated_counts": counts,
                "last_generated_at": last_generated_at,
                "notes": previous.get("notes", ""),
            }
        )

    checklist = {
        "project_id": state.project_id,
        "title": state.title,
        "updated_at": now,
        "instructions": (
            "把某集的 generate 改为 true 后，run generation 会生成或重新生成该集动态资产；"
            "成功后系统会自动把 generate 改回 false。"
        ),
        "episodes": episodes,
    }
    write_checklist(repo, project_dir, checklist)
    return checklist


def selected_episode_keys_from_checklist(
    repo: ProjectRepository,
    project_dir: Path,
    state: ProjectState,
    *,
    episode_keys: list[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    checklist = update_checklist_from_state(repo, project_dir, state, default_generate=True)
    requested = [episode_key for episode_key in (episode_keys or []) if episode_key]
    selected: list[str] = []
    if requested:
        return requested, checklist
    for item in checklist.get("episodes", []):
        key = str(item.get("episode_key"))
        if bool(item.get("generate", False)):
            selected.append(key)
    return selected, checklist
