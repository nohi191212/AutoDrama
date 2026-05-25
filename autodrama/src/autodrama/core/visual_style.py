from __future__ import annotations

from typing import Literal, TypedDict


VisualStyle = Literal["live_action", "anime_2d", "anime_3d", "cg_animation"]


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
    "cg_animation": {
        "label": "爱死机写实CG风",
        "prompt": (
            # 第一行：画风基调
            "爱死机风格写实CG：Netflix《爱·死亡·机器人》级高保真三维CG质感，"
            
            # 第二行：光影体系
            "电影级三点布光（主光伦勃朗侧逆光+补光柔化阴影+轮廓光分离背景），"
            "体积光弥漫，胶片颗粒与镜头光晕，画面有明确的明暗层次与戏剧张力，"
            
            # 第三行：材质与物理细节
            "人物为高质量风格化CG/CG动漫角色建模，明显不像真人；"
            "皮肤使用动画电影级材质和柔和SSS次表面散射，避免毛孔、汗渍、微血管、斑点等真人隐私级面部细节，"
            "毛发为CG发束塑形，布料纤维纹理清晰，"
            "衣物、发丝、挂饰有自然重力下垂、惯性摆动与落地缓冲，物理反馈真实，严禁穿模与网格穿插，"
            
            # 第四行：人物表现力
            "人物表情专注而自然，眼神有明确聚焦点与情绪传递，面部肌肉微动，"
            "自然的呼吸起伏与微动作，拒绝死鱼眼与面瘫，角色有灵魂感，"
            
            # 第五行：动作与打斗表现力
            "动作行云流水兼具力量感与柔韧性，打击感扎实，攻防节奏清晰，"
            "出招有蓄力-爆发-收招的完整力传导链，受击有相应硬直与反馈，"
            "升格慢动作穿插，动作幅度饱满但不过度夸张，符合物理直觉，"
            
            # 第六行：场景表现
            "场景有明确的前中后景深层次，环境粒子漂浮（尘埃/火星/灵气），"
            "空间透视感强，背景不抢夺人物主体，氛围感叙事感并重，"
            
            # 第七行：建模风格
            "人物建模风格偏向《凡人修仙传》国风美型CG动漫比例，五官精致骨相清晰但不过度拟真，"
            "体型匀称修长，服饰考究有东方修仙韵味，材质区分明确（丝绸/皮革/金属），"
            
            # 第八行：基础硬约束
            "说话时唇形与音频严格同步，口型准确自然，音画一致，"
            "肢体无异常扭曲，手指骨骼清晰无粘连，面部五官稳定不畸变，"
            "画面无闪烁抖动，人物边缘无锯齿与抖动伪影，输出稳定连贯。"
        )
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
    "cg_animation": "cg_animation",
    "cg-animation": "cg_animation",
    "cg animation": "cg_animation",
    "cg": "cg_animation",
    "cg animated film": "cg_animation",
    "cg动画电影风": "cg_animation",
    "CG动画电影风": "cg_animation",
    "cg动画": "cg_animation",
    "CG动画": "cg_animation",
    "动画电影": "cg_animation",
    "动画电影风": "cg_animation",
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
