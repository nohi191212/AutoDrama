from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Bind scene multiview references without letting the model reinterpret slots."""
    sections = [
        str(context.get("reference_guide") or "").strip(),
        model_output.strip(),
        "以 Image 1 的机位和可见空间为主，使用 Image 2 锁定同一场景的总体拓扑；若提供 Image 3，只用它补充高重叠区域中被 Image 1 遮挡的固定结构。",
        "生成一张完整、连续、无分格的电影级空背景。不得复制母版的面板边界，不得拼接多个视角，也不得重新设计建筑、地面、通道、固定陈设、材质、天气或主光方向。",
        "若构图来自过肩、反打或主观镜头，只保留相应机位、视线方向、景深和空白构图区，不得用任何头部、肩背、身体轮廓或人物形遮挡物来表现镜头术语。",
        "不得出现人物、人体、身体部位、剪影、代理人、可读文字、字幕、logo 或水印。",
    ]
    unique: list[str] = []
    for section in sections:
        section = re.sub(r"\s+", " ", section).strip()
        if section and section not in unique:
            unique.append(section)
    return "\n\n".join(unique)
