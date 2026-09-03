from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Deterministically bind supplied background, character, and prop references."""
    sections = [
        "Image 1 is the mandatory background, camera, field-of-view, perspective, spatial-structure, and lighting anchor.",
        str(context.get("reference_guide") or "").strip(),
        model_output.strip(),
        "Generate one cinematic opening keyframe with stable identity, complete anatomy, believable scale, and the specified occlusion relationships.",
        "Do not change the background camera or narrative angle. Do not add unrelated text, subtitles, logos, or watermarks.",
    ]
    negative = str(context.get("negative_prompt") or "").strip()
    if negative:
        sections.append(f"Avoid: {negative}")
    unique: list[str] = []
    for section in sections:
        section = re.sub(r"\s+", " ", section).strip()
        if section and section not in unique:
            unique.append(section)
    return "\n\n".join(unique)
