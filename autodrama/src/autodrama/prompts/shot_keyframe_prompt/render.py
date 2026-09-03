from __future__ import annotations

import re
from typing import Any


def render(model_output: str, context: dict[str, Any]) -> str:
    """Deterministically bind references for the rough-stage and final-edit phases."""
    phase = str(context.get("phase") or "").strip()
    if phase not in {"stage", "final"}:
        raise ValueError(f"shot_keyframe_prompt renderer has invalid phase: {phase!r}")
    if phase == "stage":
        phase_rules = [
            "生成一张角色感知的粗排关键帧。严格锁定 Image 1 的机位、透视、固定背景和光线。",
            "Image 2 只提供匿名空间几何：读取位置、尺度、脚点、朝向、视线和遮挡，不得复制其深色底、彩色线框、色块、箭头或技术图形。",
            "按照后续身份参考完整生成真实人物和道具，不得保留代理人或控制图痕迹。",
        ]
    else:
        phase_rules = [
            "对 Image 1 的粗排画面做原位电影级精修，不重新设计或重新生成整幅构图，不改变人物位置、尺度、姿态、朝向、视线、遮挡或景深层级。",
            "把 Image 1 当作唯一编辑底图：仅在各主体既有轮廓内部及紧邻边缘做局部重绘；输出与 Image 1 叠加时，主体轮廓、头顶、脚点和画框裁切关系必须重合。",
            "Image 1 中未进入画框的身体部分继续保持画外，不得为了展示完整身份而补成全身；不得把近景或半身改成中远景，也不得把中远景主体放大成近景。",
            "锁定 Image 1 中每个 binding 已经占据的空间槽位与身份对应关系；后续身份参考只修复同一 binding 的既有主体表面，不得交换不同主体的身份、前后景、左右位置或彼此遮挡关系。",
            "后续身份参考只提供脸、发型、年龄、体型、服装和道具外观，不提供构图、景别、姿态、站位或裁切；这些空间信息一律以 Image 1 为准。",
            "用 Image 2 恢复全部非人物区域，固定建筑、地面、通道、陈设、透视和光线必须与干净背景一致。",
            "用后续身份参考完整修复脸、发型、年龄、体型、服装、道具、手部、接触关系和人物与环境的光影融合，不得残留粗排瑕疵。",
        ]
    sections = [
        str(context.get("reference_guide") or "").strip(),
        model_output.strip(),
        *phase_rules,
        "只生成一个稳定的开场关键帧。不得增加无关人物、可读文字、字幕、logo 或水印。",
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
