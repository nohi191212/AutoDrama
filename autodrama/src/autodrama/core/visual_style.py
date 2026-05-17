from __future__ import annotations

from typing import Literal, TypedDict


VisualStyle = Literal["live_action", "anime_2d", "anime_3d"]


class VisualStylePreset(TypedDict):
    label: str
    prompt: str


VISUAL_STYLE_PRESETS: dict[VisualStyle, VisualStylePreset] = {
    "live_action": {
        "label": "真人电影质感",
        "prompt": (
            "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理、写实空间透视和电影镜头语言；"
            "不要动漫化、卡通化或3D动画化。"
        ),
    },
    "anime_2d": {
        "label": "2D动漫",
        "prompt": (
            "2D动漫：二维手绘动画质感、清晰线稿、赛璐璐或精致平涂上色、动画式表演和镜头调度；"
            "避免真人摄影质感、真实皮肤纹理和3D渲染质感。"
        ),
    },
    "anime_3d": {
        "label": "3D动漫",
        "prompt": (
            "3D动漫：高质量三维动画质感、风格化3D角色、体积光、动画电影式材质和镜头运动；"
            "避免真人摄影质感和2D线稿手绘质感。"
        ),
    },
}

_VISUAL_STYLE_ALIASES: dict[str, VisualStyle] = {
    "live_action": "live_action",
    "live-action": "live_action",
    "live action": "live_action",
    "cinematic": "live_action",
    "realistic": "live_action",
    "真人电影质感": "live_action",
    "真人电影": "live_action",
    "真人": "live_action",
    "写实": "live_action",
    "anime_2d": "anime_2d",
    "2d_anime": "anime_2d",
    "2d-anime": "anime_2d",
    "2d anime": "anime_2d",
    "2d": "anime_2d",
    "2D动漫": "anime_2d",
    "2d动漫": "anime_2d",
    "二维动漫": "anime_2d",
    "二维动画": "anime_2d",
    "anime_3d": "anime_3d",
    "3d_anime": "anime_3d",
    "3d-anime": "anime_3d",
    "3d anime": "anime_3d",
    "3d": "anime_3d",
    "3D动漫": "anime_3d",
    "3d动漫": "anime_3d",
    "三维动漫": "anime_3d",
    "三维动画": "anime_3d",
}


def normalize_visual_style(value: object) -> VisualStyle:
    if value is None:
        return "live_action"

    key = str(value).strip()
    if not key:
        return "live_action"

    normalized = key.lower().replace("／", "/").replace("＿", "_")
    normalized = normalized.replace(" ", "_")
    if normalized in _VISUAL_STYLE_ALIASES:
        return _VISUAL_STYLE_ALIASES[normalized]
    if key in _VISUAL_STYLE_ALIASES:
        return _VISUAL_STYLE_ALIASES[key]

    allowed = ", ".join(VISUAL_STYLE_PRESETS)
    raise ValueError(f"Unsupported visual_style: {value!r}. Allowed values: {allowed}")


def visual_style_metadata(style: VisualStyle) -> dict[str, str]:
    preset = VISUAL_STYLE_PRESETS[style]
    return {
        "visual_style": style,
        "visual_style_label": preset["label"],
        "visual_style_prompt": preset["prompt"],
    }
