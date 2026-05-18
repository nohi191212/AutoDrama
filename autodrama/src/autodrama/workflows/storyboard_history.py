from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from autodrama.core.schemas import ProjectState, StoryboardEpisodeOutput, StoryboardShot
from autodrama.repositories.project_repo import ProjectRepository

HISTORY_PATH = Path("assets") / "json" / "storyboard_history.json"


def storyboard_history_path(project_dir: Path) -> Path:
    return project_dir / HISTORY_PATH


def load_storyboard_history(project_dir: Path) -> dict[str, Any]:
    path = storyboard_history_path(project_dir)
    if not path.exists():
        return {"episodes": []}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def write_storyboard_history(repo: ProjectRepository, project_dir: Path, history: dict[str, Any]) -> None:
    repo.write_json(storyboard_history_path(project_dir), history)


def _ordered_unique(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _shot_history_item(shot: StoryboardShot) -> dict[str, Any]:
    return {
        "shot_id": shot.shot_id,
        "index": shot.index,
        "title": shot.title,
        "content": _truncate(shot.content, 180),
        "layout_id": shot.layout_id,
        "role_ids": shot.role_ids,
        "prop_ids": shot.prop_ids,
        "camera": "，".join(
            item
            for item in (
                shot.camera_shooting_angle,
                shot.camera_movement,
                shot.focal_length,
            )
            if item
        ),
    }


def build_episode_storyboard_history_item(episode: StoryboardEpisodeOutput) -> dict[str, Any]:
    summary_parts = [
        f"{shot.index}.{shot.title}: {_truncate(shot.content, 80)}"
        for shot in episode.shots[:5]
    ]
    return {
        "episode_key": episode.episode_key,
        "shot_count": len(episode.shots),
        "duration_seconds": round(sum(float(shot.duration_seconds or 0) for shot in episode.shots), 2),
        "summary": _truncate("；".join(summary_parts), 700),
        "layouts": _ordered_unique([shot.layout_id for shot in episode.shots]),
        "roles": _ordered_unique([role_id for shot in episode.shots for role_id in shot.role_ids]),
        "props": _ordered_unique([prop_id for shot in episode.shots for prop_id in shot.prop_ids]),
        "shots": [_shot_history_item(shot) for shot in episode.shots],
    }


def _expected_episode_keys(state: ProjectState) -> list[str]:
    count = int(state.metadata.get("episode_count") or 1)
    return [f"episode_{index:03d}" for index in range(1, count + 1)]


def update_storyboard_history_from_episode(
    repo: ProjectRepository,
    project_dir: Path,
    state: ProjectState,
    episode: StoryboardEpisodeOutput,
) -> dict[str, Any]:
    existing = load_storyboard_history(project_dir)
    existing_items = {
        str(item.get("episode_key")): item
        for item in existing.get("episodes", [])
        if isinstance(item, dict) and item.get("episode_key")
    }
    existing_items[episode.episode_key] = build_episode_storyboard_history_item(episode)

    expected_order = _expected_episode_keys(state)
    ordered_items = [
        existing_items[episode_key]
        for episode_key in expected_order
        if episode_key in existing_items
    ]
    extra_items = [
        item
        for key, item in sorted(existing_items.items())
        if key not in set(expected_order)
    ]
    history = {
        "project_id": state.project_id,
        "title": state.title,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "episodes": ordered_items + extra_items,
    }
    write_storyboard_history(repo, project_dir, history)
    return history


def history_before_episode(
    project_dir: Path,
    state: ProjectState,
    episode_key: str,
    *,
    max_episodes: int = 5,
    max_shots_per_episode: int = 9,
) -> dict[str, Any]:
    history = load_storyboard_history(project_dir)
    expected_order = _expected_episode_keys(state)
    try:
        current_index = expected_order.index(episode_key)
        allowed_episode_keys = set(expected_order[:current_index])
    except ValueError:
        allowed_episode_keys = set()

    episodes = [
        item
        for item in history.get("episodes", [])
        if isinstance(item, dict) and item.get("episode_key") in allowed_episode_keys
    ]
    order_lookup = {key: index for index, key in enumerate(expected_order)}
    episodes.sort(key=lambda item: order_lookup.get(str(item.get("episode_key")), 10_000))
    episodes = episodes[-max_episodes:]
    trimmed_episodes: list[dict[str, Any]] = []
    for item in episodes:
        copied = dict(item)
        copied["shots"] = list(copied.get("shots", []))[:max_shots_per_episode]
        trimmed_episodes.append(copied)

    return {
        "project_id": state.project_id,
        "current_episode_key": episode_key,
        "episodes": trimmed_episodes,
    }
