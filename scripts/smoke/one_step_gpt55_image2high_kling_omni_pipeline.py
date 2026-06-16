from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, load_settings  # noqa: E402
from autodrama.providers.base import AssetRef, ImageGenerationResult, VideoGenerationResult  # noqa: E402
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402


TEXT_MODEL = "gpt-5.5"
IMAGE_MODEL = "gpt-image-2-high"
VIDEO_MODEL = "kling-v3-omni"
DEFAULT_TARGET_SECONDS = 120
DEFAULT_SHOT_SECONDS = 15
DEFAULT_FINAL_FPS = 25


class ScriptCharacter(BaseModel):
    name: str
    role: str
    visual_notes: str


class ScriptScene(BaseModel):
    name: str
    visual_notes: str


class OptimizedScriptOutput(BaseModel):
    title: str
    logline: str
    target_duration_seconds: int
    optimized_script: str
    beat_summary: list[str] = Field(default_factory=list)
    characters: list[ScriptCharacter] = Field(default_factory=list)
    scenes: list[ScriptScene] = Field(default_factory=list)
    style_notes: str


class CharacterAssetPlan(BaseModel):
    character_id: str
    name: str
    brief: str
    appearance_lock: str
    three_view_prompt: str
    negative_prompt: str | None = None


class SceneAssetPlan(BaseModel):
    scene_id: str
    name: str
    brief: str
    spatial_lock: str
    three_view_prompt: str
    negative_prompt: str | None = None


class VisualAssetPlanOutput(BaseModel):
    characters: list[CharacterAssetPlan]
    scenes: list[SceneAssetPlan]
    global_visual_style: str
    notes: list[str] = Field(default_factory=list)


class SecondContent(BaseModel):
    second: int
    content: str
    camera_position: str
    camera_machine: str
    lens: str
    lighting: str
    composition: str
    action: str
    emotion: str
    dialogue: str = ""
    sound: str


class ShotUnit(BaseModel):
    shot_id: str
    index: int
    title: str
    duration_seconds: float
    scene_id: str
    scene_name: str
    character_ids: list[str] = Field(default_factory=list)
    character_names: list[str] = Field(default_factory=list)
    camera_machine: str
    lens: str
    shooting_method: str
    shot_language: str
    lighting_design: str
    composition_design: str
    story_intent: str
    storyboard_prompt: str
    video_prompt: str
    edit_note: str
    per_second: list[SecondContent]


class ShotPlanOutput(BaseModel):
    shots: list[ShotUnit]


class GeneratedAsset(BaseModel):
    asset_id: str
    asset_type: str
    name: str
    prompt_path: str
    result_json_path: str
    asset_path: str
    asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    task_id: str | None = None
    task_status: str | None = None


class GeneratedVideo(BaseModel):
    shot_id: str
    prompt_path: str
    refs_json_path: str
    payload_preview_path: str
    result_json_path: str
    video_path: str
    provider: str
    model: str
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None


class EditTransition(BaseModel):
    from_shot_id: str
    to_shot_id: str
    transition_type: str
    transition_duration_seconds: float
    visual_continuity: str
    audio_continuity: str
    smoothness_guardrail: str


class EditPlanOutput(BaseModel):
    editing_model: str
    editing_tool: str
    final_fps: int
    timeline_strategy: str
    shot_order: list[str]
    transitions: list[EditTransition] = Field(default_factory=list)
    anti_stutter_checks: list[str] = Field(default_factory=list)
    render_settings: dict[str, Any] = Field(default_factory=dict)


class PipelineManifest(BaseModel):
    run_id: str
    title: str
    output_dir: str
    models: dict[str, str]
    script_file: str | None = None
    target_duration_seconds: int
    shot_duration_seconds: int
    shot_count: int
    optimized_script_json: str
    visual_asset_plan_json: str
    shot_plan_json: str
    edit_plan_json: str | None = None
    operation_guide_md: str | None = None
    generated_assets: list[GeneratedAsset] = Field(default_factory=list)
    generated_videos: list[GeneratedVideo] = Field(default_factory=list)
    final_video_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


def sanitize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return sanitize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text.lower() in {"authorization", "api_key", "access_key", "secret_key", "token"}:
                sanitized[key_text] = "<redacted>"
                continue
            sanitized[key_text] = sanitize(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        if value.startswith("data:"):
            return f"<data url omitted; chars={len(value)}>"
        if len(value) > 1000 and not value.startswith(("http://", "https://", "asset://")):
            return f"<long string omitted; chars={len(value)}>"
    return value


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    return path


def slugify(value: str, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text, flags=re.UNICODE)
    text = text.strip("_")
    return text or fallback


def ensure_unique_ids(items: list[Any], field_name: str, prefix: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        raw_value = str(getattr(item, field_name) or "").strip()
        candidate = slugify(raw_value, f"{prefix}_{index:02d}")
        if not candidate.startswith(f"{prefix}_"):
            candidate = f"{prefix}_{candidate}"
        base = candidate
        suffix = 2
        while candidate in seen:
            candidate = f"{base}_{suffix}"
            suffix += 1
        setattr(item, field_name, candidate)
        seen.add(candidate)


def data_uri_extension(data: str, default: str) -> str:
    match = re.match(r"^data:[^/]+/([a-zA-Z0-9.+-]+);base64,", data)
    if not match:
        return default
    extension = match.group(1).lower()
    if extension == "jpeg":
        return "jpg"
    return extension if extension in {"png", "jpg", "webp", "gif", "mp4"} else default


def response_extension(response: httpx.Response, url: str, default: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "application/octet-stream": default,
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "webp", "gif", "mp4"}:
        return "jpg" if suffix == "jpeg" else suffix
    return default


async def download_to_path(url: str, path_without_extension: Path, timeout_seconds: float, default_extension: str) -> Path:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
    response.raise_for_status()
    extension = response_extension(response, url, default_extension)
    output_path = path_without_extension.with_suffix(f".{extension}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return output_path


async def save_image_result(
    *,
    result: ImageGenerationResult,
    output_base: Path,
    timeout_seconds: float,
) -> tuple[Path, str | None]:
    if result.image_data:
        data = result.image_data[0]
        extension = data_uri_extension(data, "png")
        if ";base64," in data:
            data = data.split(";base64,", 1)[1]
        output_path = output_base.with_suffix(f".{extension}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(data))
        return output_path, None
    if result.image_urls:
        output_path = await download_to_path(
            result.image_urls[0],
            output_base,
            timeout_seconds,
            default_extension="png",
        )
        return output_path, result.image_urls[0]
    raise RuntimeError(f"Image generation returned no image data or URL: {result.model_dump(mode='json')}")


async def save_video_result(
    *,
    result: VideoGenerationResult,
    output_base: Path,
    timeout_seconds: float,
) -> Path:
    if result.video_data:
        data = result.video_data
        if ";base64," in data:
            data = data.split(";base64,", 1)[1]
        output_path = output_base.with_suffix(".mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(data))
        return output_path
    if result.video_url:
        return await download_to_path(result.video_url, output_base, timeout_seconds, default_extension="mp4")
    raise RuntimeError(f"Video generation returned no video data or URL: {result.model_dump(mode='json')}")


def load_source_script(args: argparse.Namespace, settings) -> tuple[str, Path | None]:
    if args.raw_script:
        return args.raw_script.strip(), None

    script_path: Path | None = None
    if args.script_file:
        script_path = Path(args.script_file).expanduser()
    elif settings.project.script_outline_file:
        script_path = settings.project.script_outline_file
    else:
        candidate = ROOT_DIR / "inputs" / "story_outline.md"
        if candidate.exists():
            script_path = candidate

    if script_path is None:
        raise ValueError("Provide --script-file or --raw-script, or set project.script_outline_file in config.yaml.")
    if not script_path.is_absolute():
        script_path = (ROOT_DIR / script_path).resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")
    source = script_path.read_text(encoding="utf-8").strip()
    if not source:
        raise ValueError(f"Script file is empty: {script_path}")
    return source, script_path


def build_text_provider(settings, args: argparse.Namespace) -> RightCodeTextProvider:
    if "rightcode" not in settings.providers:
        raise ValueError("config.yaml must define providers.rightcode for GPT-5.5 text generation.")
    provider_settings = settings.providers["rightcode"].model_copy(deep=True)
    provider_settings.api_keys = settings.api_keys
    provider_settings.models = dict(provider_settings.models)
    provider_settings.models["text"] = args.text_model
    provider_settings.models["storyboard"] = args.text_model
    provider_settings.options = dict(provider_settings.options)
    provider_settings.options["text_response_format"] = True
    provider_settings.options.setdefault("reasoning_effort", "xhigh")
    provider = RightCodeTextProvider(provider_settings, settings.runtime, model_key="text")
    provider.model = args.text_model
    return provider


def build_image_provider(settings, args: argparse.Namespace) -> ToAPIImageProvider:
    if "toapi" not in settings.providers:
        raise ValueError("config.yaml must define providers.toapi for gpt-image-2-high image generation.")
    provider_settings = settings.providers["toapi"].model_copy(deep=True)
    provider_settings.api_keys = settings.api_keys
    provider_settings.models = dict(provider_settings.models)
    for key in ("image", "key_vision", "roleboard", "role_design", "layout", "storyboard", "prop"):
        provider_settings.models[key] = args.image_model
    provider_settings.options = dict(provider_settings.options)
    provider_settings.options["resolution"] = args.image_resolution
    provider_settings.options["response_format"] = args.image_response_format
    provider_settings.options["n"] = 1
    provider = ToAPIImageProvider(provider_settings, settings.runtime)
    provider.model = args.image_model
    return provider


def build_kling_provider(settings, args: argparse.Namespace) -> KlingOmniVideoProvider:
    if "kling" in settings.providers:
        provider_settings = settings.providers["kling"].model_copy(deep=True)
    else:
        provider_settings = ProviderSettings(
            base_url="https://api-beijing.klingai.com",
            access_key_env="KLING_ACCESS_KEY",
            secret_key_env="KLING_SECRET_KEY",
            models={"video": args.video_model, "subject_element": "advanced-custom-elements"},
            options={},
        )
    provider_settings.api_keys = settings.api_keys
    provider_settings.models = dict(provider_settings.models)
    provider_settings.models["video"] = args.video_model
    provider_settings.options = dict(provider_settings.options)
    provider_settings.options["aspect_ratio"] = args.aspect_ratio
    provider_settings.options["duration"] = int(args.shot_duration_seconds)
    provider_settings.options["sound"] = args.kling_sound
    provider_settings.options["watermark"] = False
    provider_settings.options["max_reference_images"] = int(args.max_video_reference_images)
    provider_settings.options["video_min_duration_seconds"] = int(args.shot_duration_seconds)
    provider_settings.options["video_max_duration_seconds"] = int(args.shot_duration_seconds)
    provider = KlingOmniVideoProvider(provider_settings, settings.runtime)
    provider.model = args.video_model
    return provider


ROLE_IDENTITY_BOARD_REQUIREMENTS = """
创建一张艺术性的 16:9 角色身份板。
[主体]：使用参考图像纯白色 / 柔和的米白色背景。
无环境、无道具、无标志、无水印。

设计方向：不要创建标准的角色参考表。
创建一张电影般的身份板，感觉像是高端动画工作室的角色研究与艺术书布局的结合。

布局应不对称、优雅且视觉上令人难忘。使用大片留白、多样化的图像比例和有意的不平衡。避免网格、蓝图设计、目录布局和重复的转场展示。

重要布局规则：不要重叠任何角色图像。
每个视角必须有清晰的分离和呼吸空间。
保持所有身体、肖像、轮廓和细节研究的视觉区分。
无裁剪面部、无隐藏肢体、无堆叠人物、无合并姿势。

主要构图：放置一个大型英雄全身视角，略微偏离中心作为视觉锚点。

围绕它，以干净的间距排列较小的辅助研究：
中性全身视角、背面视角、侧面视角、坐姿、倾斜姿势、蹲姿、俯视身体角度、仰视身体角度、富有表现力的肖像研究。
每个视角应感觉像是一个独立的干净角色研究，而不是来自一个场景的帧。

身份锁定：在所有视角中保持严格的身份一致性：
相同面部、相同面部比例、相同发型、相同服装、相同身体比例、相同姿势语言、相同视觉个性。

有用参考细节：
使角色便于未来的图像和视频生成：
清晰的面部形状、清晰的发型轮廓、清晰的服装轮廓、清晰的身体形状、清晰的手部、清晰的姿势、清晰的表情范围。

艺术性部分：
包含一个小轮廓研究区域，带有 2-3 个简化的黑色角色轮廓。
包含一个小表情研究区域，带有细微的情感变化。
包含一个小细节研究区域，展示面部、头发和服装的关键视觉特征。

文本设计：添加一个时尚的角色 ID 块。
保持简约、大胆且艺术导向。
仅使用：名称、角色、核心情绪、视觉标志。
仅在有帮助的地方使用小型手写风格标签。
允许使用细微的编辑箭头和标注标记，但保持简约和优雅。

风格：
简约、电影感、高端、艺术书般、干净、富有表现力、适用于制作。
最终图像应感觉像一张艺术性的角色身份板，旨在帮助 AI 模型理解角色的面部、轮廓、服装、姿势和情感范围。
""".strip()


STORYBOARD_BOARD_REQUIREMENTS = """
[固定要求]
16:9故事板表格，12个电影风格面板。实际故事板绘图必须仅为黑白：粗糙的铅笔线条、最小细节、快速手势绘图能量、简单的解剖结构构建以及强烈的轮廓可读性，保持艺术作品轻量、动态且未完成，像早期的影视预演分镜。

请将当前15秒分镜拆解成12个连续推进的关键镜头。每个面板都需要清楚体现画面内容、人物动作、镜头关系和情绪节奏。画面之间应具有明确的叙事推进感，而不是彼此孤立的静态图片。每个面板必须包含可见的动作、状态变化或镜头推进。避免重复、呆板或静态站立构图。角色动作、表情、姿态和场景变化应服务于剧情发展，强化连续性、节奏感和视觉张力。

使用电影感摄影方式，包含但不限于：手持感、快速平移、环绕运动、俯拍、仰拍侧面轮廓、侵略性特写、长焦压缩、极端负空间等。镜头语言需要服务剧情，不要平均分配，要根据情绪和叙事重点变化。环境保持简洁，只保留对剧情有帮助的关键场景元素。避免无关杂乱背景。重点突出人物、动作、空间关系、光线方向和氛围。

标注颜色系统：
红色箭头=身体运动
蓝色箭头=摄影机运动
绿色标记=取景/构图笔记
橙色标记=灯光方向
紫色标记=情绪/声音/叙事强调视觉冲击和情绪收束
黑色文本=简短镜头笔记和面板标签
""".strip()


def character_identity_board_prompt(character: CharacterAssetPlan, *, global_visual_style: str) -> str:
    return f"""
模型：{IMAGE_MODEL}
用途：角色背景板 / 人物身份板 / 后续图像与视频统一身份参考。

角色信息：
- 名称：{character.name}
- 角色：{character.brief}
- 年龄、体态、面部特征、发型、服饰、姿势语言和视觉个性锁定：{character.appearance_lock}
- 全局视觉风格：{global_visual_style}
- 角色原始视觉提示：{character.three_view_prompt}

{ROLE_IDENTITY_BOARD_REQUIREMENTS}

负面约束：
{character.negative_prompt or "不要出现环境、道具、logo、水印、无关文字、重叠人物、裁剪脸部、隐藏肢体、堆叠姿势、身份不一致。"}
""".strip()


def scene_three_view_prompt(scene: SceneAssetPlan, *, global_visual_style: str) -> str:
    return f"""
模型：{IMAGE_MODEL}
用途：场景三视图 / 后续故事板与视频空间统一参考。

创建一张电影级 16:9 无人物场景三视图设定板，纯净制作资料风格，无人物、无手、无剪影、无水印、无logo、无无关可读文字。

场景信息：
- 名称：{scene.name}
- 剧情功能：{scene.brief}
- 空间锁定：{scene.spatial_lock}
- 全局视觉风格：{global_visual_style}
- 场景原始视觉提示：{scene.three_view_prompt}

三视图结构：
左侧：顶视平面 / 空间动线图，必须标明入口、窗/墙/柱/地面边界、主要固定装置、角色可站位区域、摄影机可站位区域、关键道具可摆放位置和行动动线。
中间：正向主视 / 入口朝向立面图，必须交代空间高度、前中后景层次、背景锚点、主视觉焦点、主要光源、墙面/天花/地面关系和可供视频镜头复用的稳定构图。
右侧：侧向或45度透视图，必须交代空间纵深、遮挡关系、可绕行路径、人物站位前后关系、材质厚度、反射/阴影和空气粒子状态。

视觉要求：
电影级真人剧空间设定，结构清楚、材质真实、光源方向稳定、色彩关系克制，便于后续故事板和 kling-v3-omni 视频生成锁定空间。三格必须表现同一个空间、同一套固定结构、同一光源方向和同一材质设定，不要变成三个不同场景。

负面约束：
{scene.negative_prompt or "无人物、无道具误增、无可读招牌、无字幕、无水印、无logo、无杂乱背景、不要把三视图画成三张互不相关的场景。"}
""".strip()


def script_optimization_prompt(
    *,
    title: str,
    raw_script: str,
    target_duration_seconds: int,
) -> str:
    return f"""
你是顶级短剧编剧、导演和 AIGC 制片统筹。请把输入故事优化成一个完整、可拍摄、节奏明确的两分钟短剧脚本。

固定要求：
- 所有文本生成使用 GPT-5.5；本次输出只返回 JSON。
- 目标总时长：{target_duration_seconds} 秒。
- 不改变输入故事的核心设定、人物关系、关键冲突和结局方向。
- 剧本必须可直接用于后续角色身份板、场景三视图、15 秒分镜、黑白线故事板和视频生成。
- 每个关键动作都要可视化，避免只写心理活动。
- 控制角色数量和场景数量，优先服务两分钟成片。
- 对白要短、狠、清晰；没有必要的旁白不要加。
- 输出中文。

标题：
{title}

输入故事：
{raw_script}
""".strip()


def visual_asset_prompt(
    *,
    optimized: OptimizedScriptOutput,
    target_duration_seconds: int,
) -> str:
    return f"""
你是短剧美术总监和 AIGC 资产统筹。请基于两分钟剧本，拆解必须生成的视觉资产。

固定要求：
- 所有文本生成使用 GPT-5.5；本次输出只返回 JSON。
- 只输出后续视频必须复用的视觉资产，避免无关资产。
- 必须包含主要人物身份板/三视图资产，以及主要场景三视图资产。
- 人物资产必须从人物小传开始细化角色：年龄、体态、面部特征、发型、服饰、姿势语言、核心情绪、视觉标志。
- 人物资产是一张横向 16:9 艺术性角色身份板：大型英雄全身视角 + 中性全身、背面、侧面、坐姿、倾斜、蹲姿、俯视、仰视、肖像、轮廓、表情、细节研究。
- 场景资产是一张横向 16:9 无人物场景三视图：顶视平面/空间动线、正向主视、45 度侧向透视。
- 每个 prompt 必须可直接交给 gpt-image-2-high 生成，要求画面干净、结构明确、无水印、无 logo、无字幕、无无关可读文字。
- character_id 和 scene_id 必须短小稳定，使用英文或拼音/数字/下划线。
- 两分钟短剧总时长为 {target_duration_seconds} 秒。
- `three_view_prompt` 只写角色/场景本身的视觉锁定信息；脚本会自动拼入固定角色身份板模板和场景三视图模板。

优化后的剧本 JSON：
{optimized.model_dump_json(indent=2)}
""".strip()


def shot_plan_prompt(
    *,
    optimized: OptimizedScriptOutput,
    assets: VisualAssetPlanOutput,
    target_duration_seconds: int,
    shot_duration_seconds: int,
) -> str:
    shot_count = target_duration_seconds // shot_duration_seconds
    return f"""
你是获奖无数的顶级导演、摄影指导、AIGC 制作人和视频模型导演。请把两分钟 AI 真人剧严格拆成 {shot_count} 个连续分镜，每个分镜 {shot_duration_seconds} 秒。

固定要求：
- 所有文本生成使用 GPT-5.5；本次输出只返回 JSON。
- shot 数量必须严格为 {shot_count}。
- 每个 shot 的 duration_seconds 必须严格为 {shot_duration_seconds}。
- 每个 shot 的 per_second 必须严格包含 {shot_duration_seconds} 条，second 从 0 到 {shot_duration_seconds - 1} 连续递增。
- 每个 shot 必须填写 camera_machine、lens、shooting_method、shot_language、lighting_design、composition_design、edit_note。
- 每个 shot 的镜头语言必须对应具体拍摄机器，例如 ARRI Alexa 35、Sony Venice 2、RED V-Raptor、DJI Ronin 4D、手持稳定器、轨道车、摇臂、无人机、长焦压缩镜头、微距镜头等；不要笼统写“电影感镜头”。
- per_second 中每一秒都要写清楚 content、camera_position、camera_machine、lens、lighting、composition、action、emotion、dialogue、sound。
- 每秒内容必须精确到秒级，每一秒都有内容、机位、灯光、构图、动作、情绪。没有对白时 dialogue 为空字符串，不要写 null。
- 分镜必须按剧情顺序覆盖 0-{target_duration_seconds} 秒，不要跳戏、不要倒序、不要重复同一个动作。
- scene_id/scene_name 必须来自视觉资产计划里的 scenes。
- character_ids/character_names 必须来自视觉资产计划里的 characters；没有人物时输出空数组。
- storyboard_prompt 必须是一条可直接交给 gpt-image-2-high 生成的“当前 {shot_duration_seconds} 秒黑白线故事板”图像 prompt：一张 16:9 故事板表格，12 个电影风格面板，清晰显示 0-{shot_duration_seconds} 秒动作推进，禁止字幕对白气泡水印 logo。
- video_prompt 必须是给 kling-v3-omni 的主体视频描述，不写画幅比例词，不写模型名，不写图片占位符；必须包含这一镜的动作、镜头语言、声音和对白正文。
- 视频环节不允许角色直视镜头说话；使用三分之二侧脸、过肩、侧身、斜俯/斜仰、低头抬眼等角度。
- 禁止字幕、对白气泡、水印、logo、片段编号和无关可读文字。
- edit_note 写清楚本镜头与前后镜头如何顺滑衔接：动作方向、视线方向、光线方向、声音桥或硬切动机。

优化后的剧本 JSON：
{optimized.model_dump_json(indent=2)}

视觉资产计划 JSON：
{assets.model_dump_json(indent=2)}
""".strip()


def per_second_text(shot: ShotUnit) -> str:
    lines: list[str] = []
    for item in sorted(shot.per_second, key=lambda value: value.second):
        dialogue = item.dialogue.strip() or "无对白"
        lines.append(
            f"{item.second}-{item.second + 1}秒：内容：{item.content}；机位：{item.camera_position}；"
            f"机器：{item.camera_machine}；镜头：{item.lens}；灯光：{item.lighting}；"
            f"构图：{item.composition}；动作：{item.action}；情绪：{item.emotion}；"
            f"对白：{dialogue}；声音：{item.sound}"
        )
    return "\n".join(lines)


def storyboard_prompt_for_shot(shot: ShotUnit) -> str:
    return (
        f"模型：{IMAGE_MODEL}\n"
        "用途：故事背景板 / 分镜预演板 / 后续视频参考图。\n\n"
        f"{STORYBOARD_BOARD_REQUIREMENTS}\n\n"
        f"当前分镜标题：{shot.title}\n"
        f"场景：{shot.scene_name}\n"
        f"人物：{', '.join(shot.character_names) if shot.character_names else '无主要人物'}\n"
        f"拍摄机器：{shot.camera_machine}\n"
        f"镜头：{shot.lens}\n"
        f"拍摄手法：{shot.shooting_method}\n"
        f"镜头语言：{shot.shot_language}\n"
        f"灯光设计：{shot.lighting_design}\n"
        f"构图设计：{shot.composition_design}\n"
        f"分镜原始提示：{shot.storyboard_prompt.strip()}\n\n"
        f"秒级分镜内容：\n{per_second_text(shot)}\n\n"
        "请只生成故事板图像，不要生成成片画面。不要出现对白气泡、字幕、水印、logo、文件名、项目名或大段文字。"
    )


def build_kling_prompt(
    *,
    shot: ShotUnit,
    ref_descriptions: list[str],
    shot_duration_seconds: int,
) -> str:
    refs_block = "\n".join(f"- <<<image_{index}>>> {description}" for index, description in enumerate(ref_descriptions, start=1))
    return f"""
素材：
{refs_block}

请生成一个 {shot_duration_seconds} 秒电影短剧镜头，严格使用上述参考图保持人物外观、场景空间结构、构图节奏和动作连续性。不要改变角色服装、发型、体型、场景布局、主要光源方向和关键道具位置。

分镜标题：{shot.title}
剧情意图：{shot.story_intent}
场景：{shot.scene_name}
出现人物：{", ".join(shot.character_names) if shot.character_names else "无主要人物"}
拍摄机器：{shot.camera_machine}
镜头：{shot.lens}
拍摄手法：{shot.shooting_method}
镜头语言：{shot.shot_language}
灯光设计：{shot.lighting_design}
构图设计：{shot.composition_design}
剪辑衔接说明：{shot.edit_note}

每秒内容：
{per_second_text(shot)}

主体视频描述：
{shot.video_prompt}

生成要求：
- 角色不能突然直视镜头说话，保持三分之二侧脸、过肩、侧身、斜俯/斜仰或看向画面内对象。
- 对白如出现，口型必须匹配每秒内容中的对白正文；无对白秒数只保留呼吸、动作声和环境声。
- 严格按每秒内容执行，每一秒都要呈现对应的内容、机位、灯光、构图、动作、情绪。
- 保持真实短剧摄影质感，动作连续，镜头运动稳定，不要跳切造成身份或空间变化。
- 禁止字幕、对白气泡、水印、logo、片段编号、文件名、乱码和无关可读文字。
""".strip()


def edit_plan_prompt(
    *,
    shot_plan: ShotPlanOutput,
    final_fps: int,
) -> str:
    return f"""
你是电影级 AI 真人剧剪辑指导。请基于全部15秒分镜，输出最终成片剪辑方案。

固定要求：
- 剪辑方案文本模型使用 {TEXT_MODEL}。
- 全部分镜视频由 {VIDEO_MODEL} 生成，最终文件由 ffmpeg 进行恒定帧率重编码与拼接。
- final_fps 必须为 {final_fps}。
- 目标是保证视频剪辑完成后画面流转顺畅、不卡帧、不卡顿、不出现重复帧造成的突兀感。
- transitions 要按 shot 顺序逐个描述，从上一镜到下一镜如何衔接。
- 如果适合硬切，说明硬切动机；如果适合声音桥、动作接续、视线接续、光线接续或情绪接续，也要写清楚。
- anti_stutter_checks 必须包含：统一帧率、统一编码、统一像素格式、检查首尾帧运动方向、检查音频桥、避免低质量补帧、失败镜头重生成建议。
- editing_tool 固定写 ffmpeg_cfr_concat，editing_model 写 {TEXT_MODEL}。
- 输出只返回 JSON。

分镜 JSON：
{shot_plan.model_dump_json(indent=2)}
""".strip()


def write_operation_guide(
    *,
    path: Path,
    manifest: PipelineManifest,
    shot_plan: ShotPlanOutput | None = None,
) -> Path:
    lines = [
        f"# {manifest.title} 一键制片操作内容",
        "",
        "## 模型选择",
        f"- 文本规划 / 剧本优化 / 分镜 / 剪辑方案：{manifest.models['text']}",
        f"- 角色身份板、场景三视图、故事板：{manifest.models['image']}",
        f"- 15秒分镜视频：{manifest.models['video']}",
        "- 最终拼接：ffmpeg_cfr_concat，恒定帧率重编码，避免不同片段帧率/编码导致卡帧。",
        "",
        "## 角色背景板",
        f"- 固定模板：{ROLE_IDENTITY_BOARD_REQUIREMENTS[:160]}...",
        "- 生成文件：`prompts/images/*_three_view_prompt.txt` 和 `assets/images/characters/*`。",
        "",
        "## 故事背景板",
        f"- 固定模板：{STORYBOARD_BOARD_REQUIREMENTS[:160]}...",
        "- 生成文件：`prompts/storyboards/*_storyboard_prompt.txt` 和 `assets/images/storyboards/*`。",
        "",
        "## 视频生成输入",
        "- 每个15秒视频输入：人物身份板/三视图、场景三视图、当前15秒黑白线12面板故事板、秒级分镜内容。",
        "- 秒级分镜内容字段：content、camera_position、camera_machine、lens、lighting、composition、action、emotion、dialogue、sound。",
        "- 生成文件：`prompts/videos/*_kling_prompt.txt`、`json/videos/*_refs.json`、`payloads/videos/*_kling_payload_preview.json`。",
        "",
        "## 剪辑",
        "- 剪辑方案文件：`json/04_edit_plan.json`。",
        "- 成片默认输出：`outputs/final_2min_video.mp4`。",
    ]
    if shot_plan is not None:
        lines.extend(["", "## 15秒分镜列表"])
        for shot in shot_plan.shots:
            lines.append(
                f"- {shot.index:02d}. {shot.shot_id} | {shot.duration_seconds:g}s | "
                f"{shot.scene_name} | {shot.camera_machine} | {shot.lens} | {shot.title}"
            )
    return write_text(path, "\n".join(lines))


def validate_shot_plan(plan: ShotPlanOutput, *, target_seconds: int, shot_seconds: int) -> None:
    expected_shots = target_seconds // shot_seconds
    if len(plan.shots) != expected_shots:
        raise ValueError(f"Expected {expected_shots} shots, got {len(plan.shots)}")
    for expected_index, shot in enumerate(plan.shots, start=1):
        if shot.index != expected_index:
            raise ValueError(f"Shot index mismatch: expected {expected_index}, got {shot.index}")
        if round(float(shot.duration_seconds)) != shot_seconds:
            raise ValueError(f"{shot.shot_id} duration must be {shot_seconds}s, got {shot.duration_seconds}")
        if len(shot.per_second) != shot_seconds:
            raise ValueError(f"{shot.shot_id} must contain {shot_seconds} per-second entries, got {len(shot.per_second)}")
        seconds = [item.second for item in shot.per_second]
        if seconds != list(range(shot_seconds)):
            raise ValueError(f"{shot.shot_id} seconds must be 0..{shot_seconds - 1}, got {seconds}")


def select_refs_for_shot(
    *,
    shot: ShotUnit,
    characters: dict[str, CharacterAssetPlan],
    scenes: dict[str, SceneAssetPlan],
    image_paths: dict[str, Path],
    storyboard_path: Path,
) -> tuple[list[AssetRef], list[str], Path | None]:
    refs: list[AssetRef] = []
    descriptions: list[str] = []

    for character_id in shot.character_ids:
        character = characters.get(character_id)
        path = image_paths.get(character_id)
        if character is None or path is None:
            continue
        refs.append(
            AssetRef(
                id=character.character_id,
                type="image",
                path=str(path),
                metadata={"asset_type": "character_identity_board", "name": character.name},
            )
        )
        descriptions.append(f"是人物身份板/三视图：{character.name}，用于锁定面部、轮廓、服装、发型、体型、手部、姿势语言和情绪范围。")

    scene_path: Path | None = None
    scene = scenes.get(shot.scene_id)
    if scene is not None:
        scene_path = image_paths.get(scene.scene_id)
    if scene is None:
        for candidate in scenes.values():
            if candidate.name == shot.scene_name:
                scene = candidate
                scene_path = image_paths.get(candidate.scene_id)
                break
    if scene is not None and scene_path is not None:
        refs.append(
            AssetRef(
                id=scene.scene_id,
                type="image",
                path=str(scene_path),
                metadata={"asset_type": "scene_three_view", "name": scene.name},
            )
        )
        descriptions.append(f"是无人物场景三视图：{scene.name}，用于锁定空间结构、动线、材质和光源方向。")

    refs.append(
        AssetRef(
            id=f"{shot.shot_id}_storyboard",
            type="image",
            path=str(storyboard_path),
            metadata={"asset_type": "black_white_storyboard", "shot_id": shot.shot_id},
        )
    )
    descriptions.append("是当前15秒黑白线12面板故事板，用于锁定构图、动作节奏、机位变化、灯光方向、情绪推进和关键画面顺序。")
    return refs, descriptions, scene_path


def concat_videos(
    *,
    video_paths: list[Path],
    output_path: Path,
    ffmpeg_path: str,
    final_fps: int,
) -> tuple[Path | None, str | None]:
    if not video_paths:
        return None, "No shot videos to concatenate."
    ffmpeg_executable = shutil.which(ffmpeg_path) or ffmpeg_path
    concat_file = output_path.parent / "concat_list.txt"
    concat_file.parent.mkdir(parents=True, exist_ok=True)
    concat_lines = []
    for path in video_paths:
        escaped = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
        concat_lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    reencode_command = [
        ffmpeg_executable,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        f"fps={final_fps},format=yuv420p",
        "-r",
        str(final_fps),
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        "aresample=async=1:first_pts=0",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        completed = subprocess.run(reencode_command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return None, f"ffmpeg executable not found: {ffmpeg_executable}; {exc}"
    if completed.returncode == 0 and output_path.exists():
        return output_path, None
    return None, "ffmpeg cfr concat failed. stderr: " + completed.stderr[-2000:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-step 2-minute AutoDrama pipeline: GPT-5.5 text, gpt-image-2-high images, "
            "and kling-v3-omni shot videos. All process files are saved under .tmp by default."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--script-file", default=None)
    parser.add_argument("--raw-script", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--target-duration-seconds", type=int, default=DEFAULT_TARGET_SECONDS)
    parser.add_argument("--shot-duration-seconds", type=int, default=DEFAULT_SHOT_SECONDS)
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--text-model", default=TEXT_MODEL)
    parser.add_argument("--image-model", default=IMAGE_MODEL)
    parser.add_argument("--image-resolution", default="4K")
    parser.add_argument("--image-size", default="16:9")
    parser.add_argument("--image-response-format", default="url", choices=["url", "b64_json"])
    parser.add_argument("--video-model", default=VIDEO_MODEL)
    parser.add_argument("--kling-sound", default="on", choices=["on", "off"])
    parser.add_argument("--max-video-reference-images", type=int, default=6)
    parser.add_argument("--final-fps", type=int, default=DEFAULT_FINAL_FPS)
    parser.add_argument("--skip-video", action="store_true", help="Stop after text, visual assets, and storyboards.")
    parser.add_argument("--skip-concat", action="store_true", help="Do not try to concatenate shot videos with ffmpeg.")
    parser.add_argument("--continue-on-video-error", action="store_true")
    return parser


async def run_pipeline(args: argparse.Namespace) -> int:
    if args.target_duration_seconds % args.shot_duration_seconds != 0:
        raise ValueError("--target-duration-seconds must be divisible by --shot-duration-seconds")

    settings = load_settings(args.config)
    raw_script, script_path = load_source_script(args, settings)
    title = args.title or settings.project.title or "AutoDrama Two Minute Short"
    run_id = args.run_id or f"one_step_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = Path(args.output_dir) if args.output_dir else ROOT_DIR / ".tmp" / "one_step_pipeline" / run_id
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()
    prompts_dir = output_dir / "prompts"
    json_dir = output_dir / "json"
    image_dir = output_dir / "assets" / "images"
    video_dir = output_dir / "assets" / "videos"
    payload_dir = output_dir / "payloads"
    output_dir.mkdir(parents=True, exist_ok=True)

    text_provider = build_text_provider(settings, args)
    image_provider = build_image_provider(settings, args)
    video_provider = build_kling_provider(settings, args)

    manifest = PipelineManifest(
        run_id=run_id,
        title=title,
        output_dir=str(output_dir),
        models={"text": args.text_model, "image": args.image_model, "video": args.video_model},
        script_file=str(script_path) if script_path else None,
        target_duration_seconds=args.target_duration_seconds,
        shot_duration_seconds=args.shot_duration_seconds,
        shot_count=args.target_duration_seconds // args.shot_duration_seconds,
        optimized_script_json=str(json_dir / "01_optimized_script.json"),
        visual_asset_plan_json=str(json_dir / "02_visual_asset_plan.json"),
        shot_plan_json=str(json_dir / "03_shot_plan.json"),
        edit_plan_json=str(json_dir / "04_edit_plan.json"),
        operation_guide_md=str(output_dir / "operation_guide.md"),
    )
    write_operation_guide(path=output_dir / "operation_guide.md", manifest=manifest)

    print(f"output_dir={output_dir}")
    print(f"text_model={args.text_model}")
    print(f"image_model={args.image_model}")
    print(f"video_model={args.video_model}")

    prompt = script_optimization_prompt(
        title=title,
        raw_script=raw_script,
        target_duration_seconds=args.target_duration_seconds,
    )
    write_text(prompts_dir / "01_optimize_script_prompt.txt", prompt)
    optimized = await text_provider.generate_json(
        prompt,
        OptimizedScriptOutput,
        temperature=0.45,
        metadata={
            "node_name": "one_step_script_optimization",
            "project_id": run_id,
            "model": args.text_model,
            "target_duration_seconds": args.target_duration_seconds,
        },
    )
    write_json(json_dir / "01_optimized_script.json", optimized)

    prompt = visual_asset_prompt(optimized=optimized, target_duration_seconds=args.target_duration_seconds)
    write_text(prompts_dir / "02_visual_asset_plan_prompt.txt", prompt)
    asset_plan = await text_provider.generate_json(
        prompt,
        VisualAssetPlanOutput,
        temperature=0.35,
        metadata={
            "node_name": "one_step_visual_asset_plan",
            "project_id": run_id,
            "model": args.text_model,
        },
    )
    ensure_unique_ids(asset_plan.characters, "character_id", "char")
    ensure_unique_ids(asset_plan.scenes, "scene_id", "scene")
    write_json(json_dir / "02_visual_asset_plan.json", asset_plan)

    image_paths: dict[str, Path] = {}
    for character in asset_plan.characters:
        asset_id = character.character_id
        image_prompt = character_identity_board_prompt(character, global_visual_style=asset_plan.global_visual_style)
        prompt_path = write_text(prompts_dir / "images" / f"{asset_id}_three_view_prompt.txt", image_prompt)
        result = await image_provider.generate_image(
            image_prompt,
            size=args.image_size,
            metadata={
                "node_name": "one_step_character_identity_board",
                "asset_id": asset_id,
                "asset_type": "character_identity_board",
                "model": args.image_model,
                "size": args.image_size,
                "resolution": args.image_resolution,
                "n": 1,
            },
        )
        result_path = write_json(json_dir / "images" / f"{asset_id}_three_view_result.json", result)
        asset_path, asset_url = await save_image_result(
            result=result,
            output_base=image_dir / "characters" / f"{asset_id}_three_view",
            timeout_seconds=settings.runtime.request_timeout_seconds,
        )
        image_paths[asset_id] = asset_path
        manifest.generated_assets.append(
            GeneratedAsset(
                asset_id=asset_id,
                asset_type="character_identity_board",
                name=character.name,
                prompt_path=str(prompt_path),
                result_json_path=str(result_path),
                asset_path=str(asset_path),
                asset_url=asset_url,
                provider=result.provider,
                model=result.model or args.image_model,
                request_id=result.request_id,
                task_id=result.task_id,
                task_status=result.task_status,
            )
        )
        write_json(output_dir / "manifest.json", manifest)

    for scene in asset_plan.scenes:
        asset_id = scene.scene_id
        image_prompt = scene_three_view_prompt(scene, global_visual_style=asset_plan.global_visual_style)
        prompt_path = write_text(prompts_dir / "images" / f"{asset_id}_three_view_prompt.txt", image_prompt)
        result = await image_provider.generate_image(
            image_prompt,
            size=args.image_size,
            metadata={
                "node_name": "one_step_scene_three_view",
                "asset_id": asset_id,
                "asset_type": "scene_three_view",
                "model": args.image_model,
                "size": args.image_size,
                "resolution": args.image_resolution,
                "n": 1,
            },
        )
        result_path = write_json(json_dir / "images" / f"{asset_id}_three_view_result.json", result)
        asset_path, asset_url = await save_image_result(
            result=result,
            output_base=image_dir / "scenes" / f"{asset_id}_three_view",
            timeout_seconds=settings.runtime.request_timeout_seconds,
        )
        image_paths[asset_id] = asset_path
        manifest.generated_assets.append(
            GeneratedAsset(
                asset_id=asset_id,
                asset_type="scene_three_view",
                name=scene.name,
                prompt_path=str(prompt_path),
                result_json_path=str(result_path),
                asset_path=str(asset_path),
                asset_url=asset_url,
                provider=result.provider,
                model=result.model or args.image_model,
                request_id=result.request_id,
                task_id=result.task_id,
                task_status=result.task_status,
            )
        )
        write_json(output_dir / "manifest.json", manifest)

    prompt = shot_plan_prompt(
        optimized=optimized,
        assets=asset_plan,
        target_duration_seconds=args.target_duration_seconds,
        shot_duration_seconds=args.shot_duration_seconds,
    )
    write_text(prompts_dir / "03_shot_plan_prompt.txt", prompt)
    shot_plan = await text_provider.generate_json(
        prompt,
        ShotPlanOutput,
        temperature=0.4,
        metadata={
            "node_name": "one_step_15s_shot_plan",
            "project_id": run_id,
            "model": args.text_model,
            "target_duration_seconds": args.target_duration_seconds,
            "shot_duration_seconds": args.shot_duration_seconds,
        },
    )
    validate_shot_plan(
        shot_plan,
        target_seconds=args.target_duration_seconds,
        shot_seconds=args.shot_duration_seconds,
    )
    write_json(json_dir / "03_shot_plan.json", shot_plan)
    write_operation_guide(path=output_dir / "operation_guide.md", manifest=manifest, shot_plan=shot_plan)

    prompt = edit_plan_prompt(shot_plan=shot_plan, final_fps=args.final_fps)
    write_text(prompts_dir / "04_edit_plan_prompt.txt", prompt)
    edit_plan = await text_provider.generate_json(
        prompt,
        EditPlanOutput,
        temperature=0.25,
        metadata={
            "node_name": "one_step_edit_plan",
            "project_id": run_id,
            "model": args.text_model,
            "final_fps": args.final_fps,
        },
    )
    write_json(json_dir / "04_edit_plan.json", edit_plan)
    write_json(output_dir / "manifest.json", manifest)

    storyboard_paths: dict[str, Path] = {}
    for shot in shot_plan.shots:
        prompt = storyboard_prompt_for_shot(shot)
        prompt_path = write_text(prompts_dir / "storyboards" / f"{shot.shot_id}_storyboard_prompt.txt", prompt)
        result = await image_provider.generate_image(
            prompt,
            size=args.image_size,
            metadata={
                "node_name": "one_step_15s_black_white_storyboard",
                "asset_id": f"{shot.shot_id}_storyboard",
                "asset_type": "black_white_storyboard",
                "shot_id": shot.shot_id,
                "model": args.image_model,
                "size": args.image_size,
                "resolution": args.image_resolution,
                "n": 1,
            },
        )
        result_path = write_json(json_dir / "storyboards" / f"{shot.shot_id}_storyboard_result.json", result)
        asset_path, asset_url = await save_image_result(
            result=result,
            output_base=image_dir / "storyboards" / f"{shot.shot_id}_storyboard",
            timeout_seconds=settings.runtime.request_timeout_seconds,
        )
        storyboard_paths[shot.shot_id] = asset_path
        manifest.generated_assets.append(
            GeneratedAsset(
                asset_id=f"{shot.shot_id}_storyboard",
                asset_type="black_white_storyboard",
                name=shot.title,
                prompt_path=str(prompt_path),
                result_json_path=str(result_path),
                asset_path=str(asset_path),
                asset_url=asset_url,
                provider=result.provider,
                model=result.model or args.image_model,
                request_id=result.request_id,
                task_id=result.task_id,
                task_status=result.task_status,
            )
        )
        write_json(output_dir / "manifest.json", manifest)

    if args.skip_video:
        write_json(output_dir / "manifest.json", manifest)
        print("skip_video=true")
        print(f"manifest={output_dir / 'manifest.json'}")
        return 0

    character_by_id = {character.character_id: character for character in asset_plan.characters}
    scene_by_id = {scene.scene_id: scene for scene in asset_plan.scenes}
    generated_video_paths: list[Path] = []
    for shot in shot_plan.shots:
        storyboard_path = storyboard_paths[shot.shot_id]
        refs, ref_descriptions, _scene_path = select_refs_for_shot(
            shot=shot,
            characters=character_by_id,
            scenes=scene_by_id,
            image_paths=image_paths,
            storyboard_path=storyboard_path,
        )
        prompt = build_kling_prompt(
            shot=shot,
            ref_descriptions=ref_descriptions,
            shot_duration_seconds=args.shot_duration_seconds,
        )
        prompt_path = write_text(prompts_dir / "videos" / f"{shot.shot_id}_kling_prompt.txt", prompt)
        refs_json_path = write_json(
            json_dir / "videos" / f"{shot.shot_id}_refs.json",
            [
                {
                    "id": ref.id,
                    "type": ref.type,
                    "path": ref.path,
                    "url": ref.url,
                    "metadata": ref.metadata,
                }
                for ref in refs
            ],
        )
        metadata = {
            "node_name": "one_step_kling_video_generation",
            "asset_id": shot.shot_id,
            "shot_id": shot.shot_id,
            "model": args.video_model,
            "aspect_ratio": args.aspect_ratio,
            "duration": args.shot_duration_seconds,
            "sound": args.kling_sound,
            "watermark": False,
        }
        payload_preview_path = write_json(
            payload_dir / "videos" / f"{shot.shot_id}_kling_payload_preview.json",
            video_provider.build_payload(
                prompt,
                refs=refs,
                duration=args.shot_duration_seconds,
                metadata=metadata,
            ),
        )
        try:
            result = await video_provider.generate_video(
                prompt,
                refs=refs,
                duration=args.shot_duration_seconds,
                wait=True,
                metadata=metadata,
            )
            result_json_path = write_json(json_dir / "videos" / f"{shot.shot_id}_video_result.json", result)
            video_path = await save_video_result(
                result=result,
                output_base=video_dir / f"{shot.shot_id}",
                timeout_seconds=settings.runtime.request_timeout_seconds,
            )
            generated_video_paths.append(video_path)
            manifest.generated_videos.append(
                GeneratedVideo(
                    shot_id=shot.shot_id,
                    prompt_path=str(prompt_path),
                    refs_json_path=str(refs_json_path),
                    payload_preview_path=str(payload_preview_path),
                    result_json_path=str(result_json_path),
                    video_path=str(video_path),
                    provider=result.provider,
                    model=result.model or args.video_model,
                    task_id=result.task_id,
                    task_status=result.task_status,
                    request_id=result.request_id,
                )
            )
        except Exception as exc:
            error_path = write_json(
                json_dir / "videos" / f"{shot.shot_id}_video_error.json",
                {"shot_id": shot.shot_id, "error": repr(exc)},
            )
            manifest.warnings.append(f"{shot.shot_id} video failed; details: {error_path}")
            write_json(output_dir / "manifest.json", manifest)
            if not args.continue_on_video_error:
                raise
        write_json(output_dir / "manifest.json", manifest)

    if not args.skip_concat and generated_video_paths:
        final_path, warning = concat_videos(
            video_paths=generated_video_paths,
            output_path=output_dir / "outputs" / "final_2min_video.mp4",
            ffmpeg_path=settings.runtime.ffmpeg_path,
            final_fps=args.final_fps,
        )
        if final_path:
            manifest.final_video_path = str(final_path)
        if warning:
            manifest.warnings.append(warning)

    write_json(output_dir / "manifest.json", manifest)
    print("one_step_pipeline=ok")
    print(f"manifest={output_dir / 'manifest.json'}")
    if manifest.final_video_path:
        print(f"final_video={manifest.final_video_path}")
    print(f"generated_shot_videos={len(manifest.generated_videos)}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    raise SystemExit(main())
