from __future__ import annotations

import re

from autodrama.core.schemas import ProjectState, ShotManifestEpisodeOutput, ShotManifestItem
from autodrama.services.script_service import ScriptService


def expected_episode_keys(state: ProjectState) -> list[str]:
    return ScriptService.state_episode_keys(state)


def parse_episode_keys(value: str | None) -> list[str] | None:
    if not value:
        return None

    episode_keys: list[str] = []
    for raw_item in value.replace("，", ",").split(","):
        item = raw_item.strip()
        if not item:
            continue
        range_parts = [part.strip() for part in item.split("-", 1)]
        if len(range_parts) == 2 and range_parts[0].isdigit() and range_parts[1].isdigit():
            start = int(range_parts[0])
            end = int(range_parts[1])
            step = 1 if end >= start else -1
            for index in range(start, end + step, step):
                episode_keys.append(f"episode_{index:03d}")
            continue
        normalized = item.lower().replace("-", "_")
        if normalized.isdigit():
            episode_keys.append(f"episode_{int(normalized):03d}")
            continue
        if normalized.startswith("episode_"):
            suffix = normalized.rsplit("_", 1)[-1]
            if suffix.isdigit():
                episode_keys.append(f"episode_{int(suffix):03d}")
                continue
        episode_keys.append(item)
    return episode_keys or None


def parse_shot_selectors(value: str | None) -> list[str] | None:
    if not value:
        return None

    selectors: list[str] = []
    for raw_item in value.replace("，", ",").split(","):
        item = raw_item.strip()
        if not item:
            continue
        range_parts = [part.strip() for part in item.split("-", 1)]
        if len(range_parts) == 2 and range_parts[0].isdigit() and range_parts[1].isdigit():
            start = int(range_parts[0])
            end = int(range_parts[1])
            step = 1 if end >= start else -1
            for index in range(start, end + step, step):
                selectors.append(str(index))
            continue
        selectors.append(item.lower().replace("-", "_"))
    return selectors or None


def parse_clip_selectors(value: str | None) -> list[str] | None:
    return parse_shot_selectors(value)


def select_episode_keys(state: ProjectState, episode_keys: list[str] | None) -> list[str]:
    expected = expected_episode_keys(state)
    if not episode_keys:
        return expected

    requested = [key for key in episode_keys if key]
    unknown = sorted(set(requested).difference(expected))
    if unknown:
        raise ValueError(f"Unknown episode keys: {', '.join(unknown)}")
    requested_set = set(requested)
    return [key for key in expected if key in requested_set]


def sort_episode_keys_in_story_order(state: ProjectState, episode_keys: list[str]) -> list[str]:
    selected = set(episode_keys)
    ordered = [episode_key for episode_key in expected_episode_keys(state) if episode_key in selected]
    seen = set(ordered)
    ordered.extend(episode_key for episode_key in episode_keys if episode_key not in seen)
    return ordered


def normalize_shot_selectors(selectors: list[str] | set[str] | None) -> set[str]:
    return {
        str(selector).strip().lower().replace("-", "_")
        for selector in selectors or []
        if str(selector).strip()
    }


def normalize_clip_selectors(selectors: list[str] | set[str] | None) -> set[str]:
    return normalize_shot_selectors(selectors)


def shot_index_from_selector(selector: str) -> int | None:
    normalized = str(selector or "").strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    match = re.search(r"(?:^|_)shot_(\d+)$", normalized)
    if match:
        return int(match.group(1))
    return None


def shot_selector_index_bounds(selectors: list[str] | set[str] | None) -> tuple[int, int] | None:
    normalized_selectors = normalize_shot_selectors(selectors)
    if not normalized_selectors:
        return None
    indexes: list[int] = []
    unresolved: list[str] = []
    for selector in sorted(normalized_selectors):
        index = shot_index_from_selector(selector)
        if index is None:
            unresolved.append(selector)
        elif index < 1:
            unresolved.append(selector)
        else:
            indexes.append(index)
    if unresolved:
        raise ValueError(
            "Shot selection --shots must use numeric selectors so the selection bounds can be derived; "
            f"unsupported selectors: {', '.join(unresolved)}"
        )
    return min(indexes), max(indexes)


def shot_matches_selectors(
    episode: ShotManifestEpisodeOutput,
    shot: ShotManifestItem,
    selectors: set[str],
) -> bool:
    shot_id = str(shot.shot_id).lower().replace("-", "_")
    keys = {
        shot_id,
        str(shot.index),
        f"{shot.index:03d}",
        f"shot_{shot.index}",
        f"shot_{shot.index:03d}",
        f"{episode.episode_key}_shot_{shot.index}",
        f"{episode.episode_key}_shot_{shot.index:03d}",
    }
    return bool(keys.intersection(selectors))


def clip_matches_selectors(
    episode_key: str,
    clip_id: str,
    clip_index: int,
    selectors: list[str] | set[str] | None,
) -> bool:
    normalized_selectors = normalize_clip_selectors(selectors)
    if not normalized_selectors:
        return True
    normalized_episode_key = str(episode_key or "").strip().lower().replace("-", "_")
    normalized_clip_id = str(clip_id or "").strip().lower().replace("-", "_")
    keys = {
        normalized_clip_id,
        str(clip_index),
        f"{clip_index:03d}",
        f"clip_{clip_index}",
        f"clip_{clip_index:03d}",
        f"{normalized_episode_key}_clip_{clip_index}",
        f"{normalized_episode_key}_clip_{clip_index:03d}",
    }
    return bool(keys.intersection(normalized_selectors))


def active_shots_for_episode(
    episode: ShotManifestEpisodeOutput,
    selectors: list[str] | set[str] | None,
) -> list[ShotManifestItem]:
    normalized_selectors = normalize_shot_selectors(selectors)
    if not normalized_selectors:
        return list(episode.shots)

    selected = [
        shot
        for shot in episode.shots
        if shot_matches_selectors(episode, shot, normalized_selectors)
    ]
    if not selected:
        raise ValueError(
            f"No shots matched selectors {', '.join(sorted(normalized_selectors))} "
            f"for {episode.episode_key}"
        )
    return selected
