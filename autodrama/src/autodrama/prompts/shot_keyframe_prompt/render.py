from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Deterministically bind the already supplied references for image generation."""
    sections = [
        "图1是必须保持的背景、机位、透视、空间结构和光线锚点。",
        str(context.get("reference_guide") or "").strip(),
        model_output.strip(),
        "只生成一张电影级起始关键帧；人物身份、完整肢体、尺度和遮挡关系必须稳定。",
        "不得改变背景机位或叙事角度；不要添加与镜头事件无关的字幕、logo 或水印。",
    ]
    negative = str(context.get("negative_prompt") or "").strip()
    if negative:
        sections.append(f"避免：{negative}")
    unique: list[str] = []
    for section in sections:
        section = re.sub(r"\s+", " ", section).strip()
        if section and section not in unique:
            unique.append(section)
    return "\n\n".join(unique)
