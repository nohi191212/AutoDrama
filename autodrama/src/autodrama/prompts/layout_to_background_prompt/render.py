from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Add only invariant single-background image constraints."""
    sections = [
        model_output.strip(),
        str(context.get("visual_quality") or "").strip(),
        "严格参考场景母版的空间结构、材质、比例和光线；只生成一张单画面电影背景。",
        "保留固定建筑与固定陈设；不得生成三视图、拼图、宫格、参考表或 UI。",
        "画面中不得出现人物、人体局部、文字、字幕、logo 或水印。",
    ]
    unique: list[str] = []
    for section in sections:
        section = re.sub(r"\s+", " ", section).strip()
        if section and section not in unique:
            unique.append(section)
    return "\n\n".join(unique)
