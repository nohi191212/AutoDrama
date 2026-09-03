from __future__ import annotations

from typing import Any


def positive_duration(value: float | int | None) -> float:
    try:
        duration = float(value or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return max(0.25, round(duration, 3))


def select_bgm_id(state: Any, episode: Any) -> str | None:
    del episode
    return next(iter(state.bgms), None)


__all__ = ["positive_duration", "select_bgm_id"]
