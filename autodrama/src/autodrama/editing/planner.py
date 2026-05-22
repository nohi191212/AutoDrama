from __future__ import annotations

from typing import Any


def positive_duration(value: float | int | None) -> float:
    try:
        duration = float(value or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return max(0.25, round(duration, 3))


def parse_dialogue_line(raw_line: str) -> tuple[str | None, str]:
    line = str(raw_line).strip()
    if not line:
        return None, ""
    for separator in ("::", ":", "："):
        if separator in line:
            role_name, text = line.split(separator, 1)
            role_name = role_name.strip()
            text = text.strip()
            if role_name and text:
                return role_name, text
    return None, line


def select_bgm_id(state: Any, episode: Any) -> str | None:
    del episode
    return next(iter(state.bgms), None)


__all__ = ["parse_dialogue_line", "positive_duration", "select_bgm_id"]
