from __future__ import annotations

import re


_KLING_PLACEHOLDER_PATTERN = re.compile(r"<<<\s*(?:image|element|video|audio)_\d+\s*>>>", re.IGNORECASE)


def sanitize_video_prompt_text(value: object) -> str:
    """Remove exact provider protocol placeholders without changing shot semantics."""

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = _KLING_PLACEHOLDER_PATTERN.sub("", text)
    return " ".join(text.split()).strip()
