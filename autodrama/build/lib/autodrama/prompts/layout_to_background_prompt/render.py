from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Bind the spatial-anchor and annotated-shot references for final generation."""
    sections = [
        "Image 1 is the authoritative scene spatial anchor. Image 2 is the annotated shot-reference diagram derived from that same scene.",
        model_output.strip(),
        "Translate Image 2's camera position, optical direction, field of view, height, pitch, and reserved character staging into one normal cinematic camera view while preserving Image 1's topology, scale, materials, fixed architecture, and stable set dressing.",
        "Generate one empty single-frame background plate. Remove every diagram overlay: no camera icon, cones, arrows, markers, labels, compass, inset map, grid, UI, split view, or annotation may remain.",
        "No people, bodies, body parts, silhouettes, readable text, subtitles, logos, or watermarks.",
    ]
    unique: list[str] = []
    for section in sections:
        section = re.sub(r"\s+", " ", section).strip()
        if section and section not in unique:
            unique.append(section)
    return "\n\n".join(unique)
