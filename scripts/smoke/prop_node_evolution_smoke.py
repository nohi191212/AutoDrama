from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import io
import json
import mimetypes
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402
from autodrama.repositories.project_layout import ProjectLayout  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402


DEFAULT_LAB_DIR = ROOT / ".tmp" / "prop-gen-evolution-20260809-final"
DEFAULT_CONFIG = ROOT / "saodi.yaml"
WHITE_TEMPLATE_SOURCE = ROOT / ".assets" / "image_templates" / "propboard_template.png"
KEY_VISION_SOURCE = Path(
    r"C:\Users\csh10\AppData\Local\Temp\codex-clipboard-3455b727-708e-45cf-be13-b405a412da0d.png"
)
PROJECT_PROP_SOURCE = ROOT / "outputs" / "saodi_0803" / "assets" / "json" / "nodes" / "prop_finalize.json"
IMAGE_MODEL = "gpt-image-2-guan"
TEXT_MODEL = "gemini-3.6-flash"
IMAGE_SIZE = "2048x2048"
IMAGE_QUALITY = "high"
IMAGE_BUDGET = 20
TEXT_BUDGET = 60
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


STYLE_TARGET_COMPACT = (
    "与主视觉一致的电影级东方玄幻三维成片：物理可信的木、金属、玉石和皮革材质，"
    "自然的暖色主光与偏冷天空补光，柔和体积雾和真实接触阴影，低饱和但有层次的青灰、暖金和木色，"
    "细腻克制的磨损与纹理，整体像高预算真人电影的实拍合成或高完成度 CG，不是扁平二维插画。"
)
STYLE_TARGET_EXPANDED = (
    "参考主视觉的整体成片语言，而不是复制其中的人物、建筑、场景或具体道具："
    "写实电影摄影式的东方玄幻三维渲染，真实但克制的材质反射和微表面纹理，"
    "木材有自然纤维，金属有有重量的边缘高光和轻微氧化，玉石有内透与细微纹理，皮革有柔软褶皱；"
    "光线为温暖的侧向日光或夕照，同时保留偏冷的环境填光，阴影自然，空气中有轻薄山雾和空间层次；"
    "色彩以青灰、岩石灰、深木色、暖金为关系，饱和度受控，画面干净、沉稳、带电影级动态范围和细腻后期；"
    "禁止平涂二维国漫、黑白水墨、赛博霓虹、塑料玩具、手机游戏皮肤、廉价金属贴图和过度发光。"
)


class PromptDraft(BaseModel):
    prompt_strategy: str = Field(description="Short description of the prompt-writing strategy")
    final_prompt: str = Field(description="Complete prompt sent verbatim to the image model")
    prompt_checks: list[str] = Field(default_factory=list)


class StyleAudit(BaseModel):
    approved: bool
    style_score: float = Field(ge=0, le=10)
    rendering_medium_score: float = Field(ge=0, le=10)
    lighting_atmosphere_score: float = Field(ge=0, le=10)
    material_response_score: float = Field(ge=0, le=10)
    palette_relationship_score: float = Field(ge=0, le=10)
    production_finish_score: float = Field(ge=0, le=10)
    three_view_score: float = Field(ge=0, le=10)
    prop_identity_score: float = Field(ge=0, le=10)
    issues: list[str] = Field(default_factory=list)
    rationale: str = ""
    recommendation: str = ""


class StyleAuditV2(BaseModel):
    approved: bool
    style_score: float = Field(ge=0, le=10)
    rendering_medium_score: float = Field(ge=0, le=10)
    lighting_atmosphere_score: float = Field(ge=0, le=10)
    material_response_score: float = Field(ge=0, le=10)
    palette_relationship_score: float = Field(ge=0, le=10)
    production_finish_score: float = Field(ge=0, le=10)
    three_view_score: float = Field(ge=0, le=10)
    prop_identity_score: float = Field(ge=0, le=10)
    white_model_semantic_leakage_score: float = Field(
        ge=0,
        le=10,
        description="10 means no semantic structure copied from the white model; 0 means severe copying",
    )
    white_model_semantic_leakage_detected: bool
    white_model_leakage_evidence: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    rationale: str = ""
    recommendation: str = ""


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    title: str
    reference_policy: str
    prompt_strategy: str
    hypothesis: str


@dataclass(frozen=True)
class PropCase:
    case_id: str
    name: str
    description: str
    source: str


CANDIDATES = (
    CandidateSpec(
        candidate_id="white-only-baseline",
        title="白模单参考·基线",
        reference_policy="white_only",
        prompt_strategy="短风格约束 + 白模只负责三视图空间骨架",
        hypothesis="只给白模可以稳定复制三视图结构，风格主要由文字约束决定。",
    ),
    CandidateSpec(
        candidate_id="white-only-isolation-first",
        title="白模单参考·语义隔离优先",
        reference_policy="white_only",
        prompt_strategy="先删除白模物体语义，再用白模保留三视图排列、基线与留白",
        hypothesis="把白模语义隔离放在提示词最前面，能减少把模板直接上材质的失败。",
    ),
    CandidateSpec(
        candidate_id="white-only-shape-first",
        title="白模单参考·道具描述优先",
        reference_policy="white_only",
        prompt_strategy="先锁定项目道具描述和功能结构，再把结果排成白模三视图版式",
        hypothesis="让项目提取描述先决定造型，再用白模排版，能避免模板形状覆盖道具身份。",
    ),
    CandidateSpec(
        candidate_id="white-only-negative-checklist",
        title="白模单参考·逐项反例清单",
        reference_policy="white_only",
        prompt_strategy="白模空间骨架 + 明确列出箱体、提手、旋钮、面板、铰链、支脚等禁止继承项",
        hypothesis="逐项点名模板语义构件，能让图像模型停止复制白模结构。",
    ),
    CandidateSpec(
        candidate_id="white-only-material-after-shape",
        title="白模单参考·材质后置",
        reference_policy="white_only",
        prompt_strategy="先生成项目道具形状与构件关系，最后才添加符合项目描述的材质与风格",
        hypothesis="把材质放在造型之后，能避免直接给白模几何贴材质。",
    ),
    CandidateSpec(
        candidate_id="both-role-separated",
        title="白模 + 主视觉·职责分离",
        reference_policy="white_and_key_vision",
        prompt_strategy="参考图 1 只管三视图布局，参考图 2 只管主视觉媒介、光线、材质和调色",
        hypothesis="显式分离两张参考图的职责，可以同时保住三视图几何和主视觉成片风格。",
    ),
    CandidateSpec(
        candidate_id="both-style-priority",
        title="白模 + 主视觉·风格优先",
        reference_policy="white_and_key_vision",
        prompt_strategy="先锁定主视觉的电影级整体风格，再用白模约束三视图，不复制参考图主体",
        hypothesis="让主视觉作为风格锚点、白模作为结构约束，可能得到更强的风格协调性。",
    ),
    CandidateSpec(
        candidate_id="both-white-primary",
        title="白模 + 主视觉·项目道具优先",
        reference_policy="white_and_key_vision",
        prompt_strategy="项目道具描述决定形状与功能，白模只排版，主视觉只决定成片语言",
        hypothesis="同时给出两张参考图时，明确项目描述优先级能抑制白模和主视觉主体的内容干扰。",
    ),
    CandidateSpec(
        candidate_id="both-style-derived",
        title="白模 + 主视觉·风格转译",
        reference_policy="white_and_key_vision",
        prompt_strategy="从主视觉转译真实光线、材质响应和色彩关系，再把项目道具排进白模三视图版式",
        hypothesis="风格转译比直接复制主视觉内容更适合道具三视图。",
    ),
    CandidateSpec(
        candidate_id="both-hard-isolation",
        title="白模 + 主视觉·双参考硬隔离",
        reference_policy="white_and_key_vision",
        prompt_strategy="对两张参考图都执行内容隔离：白模只排版，主视觉只定风格，项目描述唯一决定道具内容",
        hypothesis="三方职责硬隔离可能同时解决白模语义泄漏和主视觉主体误复制。",
    ),
)


# Populated from prop_finalize.json by init_lab/load_context. No fabricated smoke props.
PROP_CASES: tuple[PropCase, ...] = ()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_project_prop_cases(source: Path = PROJECT_PROP_SOURCE) -> tuple[PropCase, ...]:
    payload = read_json(source)
    props = payload.get("props") if isinstance(payload, dict) else None
    if not isinstance(props, list) or not props:
        raise ValueError(f"Project prop source has no props: {source}")
    cases: list[PropCase] = []
    for index, item in enumerate(props, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        descriptions: list[str] = []
        for value in (item.get("intro"),):
            text = str(value or "").strip()
            if text and text not in descriptions:
                descriptions.append(text)
        assets = item.get("assets") if isinstance(item.get("assets"), list) else []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            text = str(asset.get("desc") or "").strip()
            if text and text not in descriptions:
                descriptions.append(text)
        if not descriptions:
            raise ValueError(f"Project prop has no description: {name}")
        case_id = f"r{index:02d}-{name}"
        cases.append(
            PropCase(
                case_id=case_id,
                name=name,
                description="；".join(descriptions),
                source=str(source),
            )
        )
    if not cases:
        raise ValueError(f"Project prop source has no usable prop records: {source}")
    return tuple(cases)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def ensure_under(path: Path, parent: Path, label: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must remain under {parent}: {path}") from exc


def copy_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != sha256(source):
            raise ValueError(f"Frozen input snapshot differs from source: {destination}")
        return
    shutil.copy2(source, destination)


def source_prop_names() -> set[str]:
    return {case.name for case in load_project_prop_cases(PROJECT_PROP_SOURCE)}


def ref_roles(candidate: CandidateSpec) -> list[str]:
    if candidate.reference_policy == "white_only":
        return ["spatial_white_model"]
    return ["spatial_white_model", "key_vision_anchor"]


def template_text(candidate: CandidateSpec) -> str:
    style = STYLE_TARGET_COMPACT if candidate.candidate_id == "white-only-baseline" else STYLE_TARGET_EXPANDED
    if candidate.reference_policy == "white_only":
        reference_clause = (
            "参考图 1 是白模三视图模板，只复制它的版式几何：三个分离、等比例、完整可见的正面、侧面、背面，"
            "共同脚底基线和中性留白。白模只是空间分布参考，不是道具造型参考。"
        )
        style_clause = (
            "不要读取或臆造任何主视觉图片内容；只依据下面的文字风格目标完成最终渲染。"
            if candidate.candidate_id == "white-only-baseline"
            else "只依据下面更完整的文字风格目标完成最终渲染；不要把文字写成外部项目资料。"
        )
    else:
        reference_clause = (
            "参考图 1 只负责三视图版式几何：三个分离、等比例、完整可见的正面、侧面、背面，共同脚底基线和中性留白；"
            "白模只是空间分布参考，不是道具造型参考；不要复制它的箱体身份、结构、材质或白色黏土效果。参考图 2 只负责主视觉的渲染媒介、材质响应、光线、"
            "空气感、色彩关系和成片质感；不要复制其中的人物、建筑、场景、动作、构图或具体道具。"
        )
        style_clause = (
            "两张参考图发生冲突时，参考图 1 只保留三视图几何，参考图 2 优先决定整体画风；不要让主视觉变成场景图。"
            if candidate.candidate_id == "both-style-priority"
            else "严格分离两张参考图的职责：白模只管几何，主视觉只管风格。"
        )
    return f"""把下面的道具描述编译成一条可以直接用于生图的中文提示词。

输入道具名称：{{prop_name}}
输入道具描述：{{prop_description}}

视觉风格目标：
{style}

参考图职责：
{reference_clause}

本候选策略：
{candidate.prompt_strategy}
{style_clause}

白模语义隔离（硬约束）：
- 白模中的任何物体语义都必须删除，默认禁止继承它的矩形箱体/设备身份、提手、顶部旋钮或盖、正面圆盘或面板、曲柄、铰链、装饰框、竖条、底座和支脚等具体构件。
- 只有输入道具描述明确要求的构件才能出现；即使同名，也必须按输入道具重新设计，不能沿用白模的相对位置、形状、数量或组合。
- 不要把白模的白色黏土材质、轮廓细节、机械结构或“给模型上纹理”的做法带进最终道具。白模只提供三视图排列、共同基线和留白。

最终提示词必须完成以下任务：
- 只生成这一个输入道具，不新增人物、手、场景或第二件道具；保持道具身份、轮廓、尺寸感、构件关系和材质可识别。
- 生成一张干净的道具三视图参考板，恰好三个分离的完整视图：正面、真正的侧面、背面；三者等比例、同一基线、自然站立或平放，主体不能裁切或重叠。
- 允许真实接触面、柔和投影和少量自然反射，但背景必须简单中性；不要标题、标签、说明文字、logo、水印、边框、拼贴、额外小图或可读文字。
- 让材质和光线服从视觉风格目标；不要变成黑白水墨、扁平二维、赛博霓虹、塑料玩具、廉价游戏皮肤或过度发光的法器。
- 最终提示词不要写项目名、episode、文件路径、字段名、API、模型名、尺寸、分辨率或画幅比例；不要解释你在做什么。

只输出 JSON，字段为：prompt_strategy、final_prompt、prompt_checks。final_prompt 必须是一条可直接交给图像模型的完整提示词，不要 Markdown 代码围栏。"""


def render_template(template: str, case: PropCase) -> str:
    replacements = {
        "{{prop_name}}": case.name,
        "{{prop_description}}": case.description,
    }
    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    unresolved = sorted(set(re.findall(r"\{\{[^}]+\}\}", result)))
    if unresolved:
        raise ValueError(f"Unresolved prompt placeholders: {unresolved}")
    return result.strip()


def audit_request(item: dict[str, Any], current_prompt: str) -> str:
    return f"""审查一张道具三视图参考板是否与主视觉风格协调。

参考图 1：待审的道具三视图图像。
参考图 2：唯一的主视觉风格锚点。

{STYLE_TARGET_EXPANDED}

只把主视觉当作整体风格锚点，不要求道具复制主视觉中的人物、建筑、场景、构图、姿态或具体道具。重点判断两张图是否属于同一套成片世界：渲染媒介是否一致，光线和空气感是否协调，材质是否具有相近的真实度与细腻度，色彩和对比关系是否协调，整体是否达到相近的电影级完成度。

同时顺手记录三视图是否完整、道具身份是否可辨，但这两项只是辅助信息；不要因为白背景、三视图版式或道具题材不同而把风格审查判为不通过。

当前生图提示词（只用于理解意图，不作为风格锚点）：
{current_prompt}

返回 JSON：
- approved：只有风格协调且图片没有明显阻止使用的成片问题时为 true；
- style_score：风格协调总分，0 到 10；7 分表示可直接进入下一步，8 分以上表示明显协调；
- rendering_medium_score、lighting_atmosphere_score、material_response_score、palette_relationship_score、production_finish_score：五个风格子项，0 到 10；
- three_view_score、prop_identity_score：辅助子项，0 到 10；
- issues：具体可见问题，若通过则为空数组；
- rationale：一句话说明风格判断依据；
- recommendation：下一轮模板或提示词最值得保留/修正的一点。

    只输出 JSON，不要 Markdown，不要输出项目字段或审查流程。"""


def audit_request_v2(item: dict[str, Any], current_prompt: str) -> str:
    return f"""审查一张道具三视图参考板，判断它是否与主视觉风格协调，并检查它有没有把白模的物体语义直接复制出来。

参考图 1：待审的道具三视图图像。
参考图 2：唯一的主视觉风格锚点。
参考图 3：白模三视图空间模板，只用于对照它原本的版式几何和其中不应被继承的模板语义。

待审道具：{item['prop_name']}
当前生图提示词（只用于理解道具意图，不作为风格锚点）：
{current_prompt}

主视觉风格目标：
{STYLE_TARGET_EXPANDED}

风格审查：只把参考图 2 当作整体风格锚点，不要求道具复制其中的人物、建筑、场景、构图、姿态或具体道具。判断三视图道具是否属于同一套成片世界：渲染媒介是否一致，光线和空气感是否协调，材质是否具有相近的真实度与细腻度，色彩和对比关系是否协调，整体是否达到相近的电影级完成度。白背景、三视图版式和题材差异本身不构成风格问题。

白模语义泄漏审查（硬门槛）：
- 参考图 3 的白模只应该贡献三个视图的分布、共同基线和留白；它不是道具形状、部件、材质或装饰的参考。
- 先依据当前生图提示词判断输入道具本来需要什么结构，再检查参考图 1 是否无理由地复现了参考图 3 的语义结构。
- 重点查找白模的语义组合是否被照搬：矩形箱体/设备身份、提手、顶部旋钮或盖、正面圆盘或面板、曲柄、铰链、装饰框、竖条、底座和支脚，以及这些构件在三个视图中的相对位置、数量和组合。
- 只要输出仍然像“给白模上材质”，或复制了多个与输入道具无关的模板构件，就判定 white_model_semantic_leakage_detected=true，并在 white_model_leakage_evidence 中逐条写出可见证据。
- 输入道具描述明确要求的同名构件不算泄漏，但若仍沿用白模的整套组合、比例或布局，仍算泄漏。
- white_model_semantic_leakage_score 的含义固定为：10 = 看不到白模语义复制，0 = 严重复制；任何明显泄漏都不能给 9 分以上。

返回 JSON：
- approved：只有风格协调、没有明显成片问题、且没有白模语义泄漏时为 true；
- style_score：风格协调总分，0 到 10；7 分表示可进入下一步，8 分以上表示明显协调；
- rendering_medium_score、lighting_atmosphere_score、material_response_score、palette_relationship_score、production_finish_score：五个风格子项，0 到 10；
- three_view_score、prop_identity_score：辅助子项，0 到 10；
- white_model_semantic_leakage_score：白模语义隔离分，0 到 10，10 表示完全没有复制；
- white_model_semantic_leakage_detected：只要存在明显白模语义复制就为 true；
- white_model_leakage_evidence：具体指出复制了哪些白模语义构件或组合；若没有则为空数组；
- issues：其它具体可见问题；若没有则为空数组；
- rationale：一句话同时概括风格判断和白模语义隔离判断；
- recommendation：下一轮模板或提示词最值得保留/修正的一点。

    只输出 JSON，不要 Markdown，不要输出项目字段或审查流程。"""


def v2_audit_gate(decision: StyleAuditV2) -> bool:
    leakage_free = (
        not decision.white_model_semantic_leakage_detected
        and decision.white_model_semantic_leakage_score >= 9.0
    )
    return bool(decision.approved and decision.style_score >= 7.0 and leakage_free)


def candidate_by_id(candidate_id: str) -> CandidateSpec:
    for candidate in CANDIDATES:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise KeyError(candidate_id)


def case_by_id(case_id: str) -> PropCase:
    for case in PROP_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def item_id(case: PropCase, candidate: CandidateSpec) -> str:
    return f"{case.case_id}--{candidate.candidate_id}"


def all_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for round_index, case in enumerate(PROP_CASES, start=1):
        for candidate in CANDIDATES:
            current_id = item_id(case, candidate)
            items.append(
                {
                    "item_id": current_id,
                    "round": round_index,
                    "round_id": f"r{round_index:02d}",
                    "case_id": case.case_id,
                    "prop_name": case.name,
                    "candidate_id": candidate.candidate_id,
                    "candidate_title": candidate.title,
                    "reference_policy": candidate.reference_policy,
                    "reference_roles": ref_roles(candidate),
                    "prompt_path": f"prompts/{current_id}.json",
                    "image_path": f"images/r{round_index:02d}/{current_id}.png",
                    "image_response_path": f"responses/images/{current_id}.json",
                    "audit_path": f"responses/audits/{current_id}.json",
                    "audit_v2_path": f"responses/audits_v2/{current_id}.json",
                }
            )
    return items


def init_lab(lab_dir: Path, config_path: Path) -> dict[str, Any]:
    global PROP_CASES
    lab_dir = lab_dir.resolve()
    ensure_under(lab_dir, ROOT / ".tmp", "lab directory")
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    if config_path != (ROOT / "saodi.yaml").resolve():
        raise ValueError(f"This lab must run against saodi.yaml, got: {config_path}")
    if not WHITE_TEMPLATE_SOURCE.is_file():
        raise FileNotFoundError(WHITE_TEMPLATE_SOURCE)
    if not KEY_VISION_SOURCE.is_file():
        raise FileNotFoundError(KEY_VISION_SOURCE)
    if not PROJECT_PROP_SOURCE.is_file():
        raise FileNotFoundError(PROJECT_PROP_SOURCE)

    PROP_CASES = load_project_prop_cases(PROJECT_PROP_SOURCE)
    project_names = {case.name for case in PROP_CASES}
    expected_image_items = len(PROP_CASES) * len(CANDIDATES)
    if expected_image_items != IMAGE_BUDGET:
        raise ValueError(
            f"Experiment design must consume exactly {IMAGE_BUDGET} image calls; "
            f"project props={len(PROP_CASES)}, candidates={len(CANDIDATES)}, items={expected_image_items}"
        )

    existing_experiment = lab_dir / "experiment.json"
    if existing_experiment.is_file():
        existing = read_json(existing_experiment)
        if existing.get("status") == "invalidated":
            raise ValueError(f"Lab is invalidated; choose a new lab directory: {lab_dir}")
        return {
            "lab": str(lab_dir),
            "status": "already_initialized",
            "image_budget": existing.get("budgets", {}).get("image"),
            "text_budget": existing.get("budgets", {}).get("text"),
        }

    inputs_dir = lab_dir / "inputs"
    snapshots = {
        "spatial_white_model": (WHITE_TEMPLATE_SOURCE, inputs_dir / "propboard_template.png", "image"),
        "key_vision_anchor": (KEY_VISION_SOURCE, inputs_dir / "key_vision_anchor.png", "image"),
        "project_config": (config_path, inputs_dir / "saodi.yaml", "yaml"),
        "project_prop_source": (PROJECT_PROP_SOURCE, inputs_dir / "prop_finalize.json", "json"),
    }
    for source, destination, _kind in snapshots.values():
        copy_snapshot(source, destination)

    manifest_files = []
    for logical_role, (source, destination, kind) in snapshots.items():
        manifest_files.append(
            {
                "logical_role": logical_role,
                "kind": kind,
                "source_path": str(source),
                "snapshot_path": str(destination),
                "sha256": sha256(destination),
                "bytes": destination.stat().st_size,
            }
        )

    experiment = {
        "schema_version": 1,
        "name": "prop-gen-node-evolution",
        "created_at": utc_now(),
        "status": "initialized",
        "mode": "execute",
        "config": {"source_path": str(config_path), "sha256": sha256(config_path)},
        "budgets": {"image": IMAGE_BUDGET, "text": TEXT_BUDGET},
        "controls": {
            "image_model": IMAGE_MODEL,
            "text_model": TEXT_MODEL,
            "image_size": IMAGE_SIZE,
            "image_quality": IMAGE_QUALITY,
            "image_calls": "one call per candidate per prop case; no image retry inside the lab",
            "rounds": len(PROP_CASES),
            "candidates_per_round": len(CANDIDATES),
        },
        "reference_selection": {
            "key_vision_source": str(KEY_VISION_SOURCE),
            "selection_basis": "user_attached_image",
            "note": "The attached clipboard image is the only style anchor for this lab.",
        },
        "prop_case_policy": "Every generated prop case is read from saodi.yaml project's finalized prop extraction; no fabricated smoke-only props are allowed.",
    }
    write_json(lab_dir / "experiment.json", experiment)
    write_json(lab_dir / "inputs" / "manifest.json", {"schema_version": 1, "files": manifest_files})
    write_json(
        lab_dir / "prop_cases.json",
        {
            "schema_version": 1,
            "source_project_props": sorted(project_names),
            "cases": [case.__dict__ for case in PROP_CASES],
        },
    )
    write_json(
        lab_dir / "candidates.json",
        {
            "schema_version": 1,
            "candidates": [candidate.__dict__ for candidate in CANDIDATES],
            "style_target_compact": STYLE_TARGET_COMPACT,
            "style_target_expanded": STYLE_TARGET_EXPANDED,
        },
    )
    for candidate in CANDIDATES:
        write_json(
            lab_dir / "templates" / f"{candidate.candidate_id}.json",
            {
                "candidate": candidate.__dict__,
                "prompt_writer_template": template_text(candidate),
            },
        )
    for round_index, case in enumerate(PROP_CASES, start=1):
        batch_items = [item for item in all_items() if item["round"] == round_index]
        write_json(
            lab_dir / "batches" / f"r{round_index:02d}.json",
            {
                "round_id": f"r{round_index:02d}",
                "prop_case": case.__dict__,
                "items": batch_items,
                "image_calls": len(batch_items),
                "reference_options": {
                    "white_only": ["spatial_white_model"],
                    "white_and_key_vision": ["spatial_white_model", "key_vision_anchor"],
                },
            },
        )
    protocol = f"""# Prop-gen node evolution smoke lab

## Fixed budget

    - Image generation ceiling: {IMAGE_BUDGET} calls.
    - Gemini text ceiling: {TEXT_BUDGET} calls, covering {len(PROP_CASES) * len(CANDIDATES)} `prop_prompt` calls, the same number of first-pass audits, and the same number of v2 re-audits.
    - Each round is one actual prop extracted from the project. The project currently contains {len(PROP_CASES)} props; ten controlled prompt/reference candidates per prop use the full 20-image ceiling.
- Image retries are disabled at the lab layer and the provider is forced to one attempt. A failed image call remains a consumed budget slot.

## References

- White-model spatial template: `inputs/propboard_template.png`.
- Style anchor: `inputs/key_vision_anchor.png`, copied from the user-attached clipboard image.
    - For generation, Image 1 is spatial geometry only; Image 2, when present, is style only.
    - For v2 audit, the generated board is Image 1, the key vision is Image 2, and the white model is Image 3.

## Audit focus

    Gemini audits whether the generated prop board belongs to the same cinematic rendering world as the style anchor and whether it has copied white-model semantics. Style coordination is scored, but any detected white-model semantic leakage is a hard failure regardless of style score.
"""
    (lab_dir / "experiment_protocol.md").parent.mkdir(parents=True, exist_ok=True)
    (lab_dir / "experiment_protocol.md").write_text(protocol, encoding="utf-8", newline="\n")
    (lab_dir / "ledger.jsonl").touch()
    return {
        "lab": str(lab_dir),
        "status": "initialized",
        "image_budget": IMAGE_BUDGET,
        "text_budget": TEXT_BUDGET,
        "rounds": len(PROP_CASES),
        "image_items": len(all_items()),
        "key_vision_snapshot": str(inputs_dir / "key_vision_anchor.png"),
        "white_template_snapshot": str(inputs_dir / "propboard_template.png"),
    }


def verify_snapshot(record: dict[str, Any]) -> Path:
    path = Path(str(record.get("snapshot_path") or "")).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = str(record.get("sha256") or "")
    actual = sha256(path)
    if not expected or expected != actual:
        raise ValueError(f"Frozen input changed: {path}")
    return path


def load_context(lab_dir: Path) -> dict[str, Any]:
    global PROP_CASES
    lab_dir = lab_dir.resolve()
    ensure_under(lab_dir, ROOT / ".tmp", "lab directory")
    experiment = read_json(lab_dir / "experiment.json")
    manifest = read_json(lab_dir / "inputs" / "manifest.json")
    snapshots = {
        str(item["logical_role"]): verify_snapshot(item)
        for item in manifest.get("files", [])
    }
    if "key_vision_anchor" not in snapshots or "spatial_white_model" not in snapshots:
        raise ValueError("Lab manifest is missing the two required reference images")
    if "project_prop_source" not in snapshots:
        raise ValueError("Lab manifest is missing the frozen project prop extraction")
    config_info = experiment.get("config") or {}
    config_path = Path(str(config_info.get("source_path") or "")).resolve()
    if not config_path.is_file() or sha256(config_path) != config_info.get("sha256"):
        raise ValueError(f"Configured runtime source changed or is missing: {config_path}")
    PROP_CASES = load_project_prop_cases(snapshots["project_prop_source"])
    cases = {case.case_id: case for case in PROP_CASES}
    candidates = {candidate.candidate_id: candidate for candidate in CANDIDATES}
    return {
        "lab_dir": lab_dir,
        "experiment": experiment,
        "manifest": manifest,
        "snapshots": snapshots,
        "config_path": config_path,
        "cases": cases,
        "candidates": candidates,
        "items": all_items(),
    }


def stage_path(ctx: dict[str, Any], item: dict[str, Any], stage: str) -> Path:
    lab_dir: Path = ctx["lab_dir"]
    if stage == "prompt":
        return lab_dir / str(item["prompt_path"])
    if stage == "image":
        return lab_dir / str(item["image_response_path"])
    if stage == "audit":
        return lab_dir / str(item["audit_path"])
    if stage == "audit_v2":
        return lab_dir / str(item["audit_v2_path"])
    raise KeyError(stage)


def successful_record(ctx: dict[str, Any], item: dict[str, Any], stage: str) -> dict[str, Any] | None:
    path = stage_path(ctx, item, stage)
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("success") is True else None


def any_record(ctx: dict[str, Any], item: dict[str, Any], stage: str) -> dict[str, Any] | None:
    path = stage_path(ctx, item, stage)
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def reserved_count(ctx: dict[str, Any], kind: str) -> int:
    return sum(int(row.get("count") or 1) for row in read_jsonl(ctx["lab_dir"] / "ledger.jsonl") if row.get("event") == "reserve" and row.get("kind") == kind)


def reserve_call(ctx: dict[str, Any], *, kind: str, stage: str, item_id_value: str, limit: int) -> str:
    used = reserved_count(ctx, kind)
    if used + 1 > limit:
        raise RuntimeError(f"{kind} budget exceeded: used={used}, limit={limit}")
    call_id = f"{kind}-{stage}-{item_id_value}-{used + 1:03d}"
    append_jsonl(
        ctx["lab_dir"] / "ledger.jsonl",
        {
            "event": "reserve",
            "call_id": call_id,
            "kind": kind,
            "stage": stage,
            "item_id": item_id_value,
            "count": 1,
            "at": utc_now(),
        },
    )
    return call_id


def record_completed(ctx: dict[str, Any], *, call_id: str, kind: str, stage: str, item_id_value: str, status: str, **extra: Any) -> None:
    append_jsonl(
        ctx["lab_dir"] / "ledger.jsonl",
        {
            "event": "complete",
            "call_id": call_id,
            "kind": kind,
            "stage": stage,
            "item_id": item_id_value,
            "status": status,
            "at": utc_now(),
            **extra,
        },
    )


def load_reference_url_cache(ctx: dict[str, Any]) -> dict[str, Any]:
    path = ctx["lab_dir"] / "responses" / "reference_urls.json"
    if not path.is_file():
        return {}
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_reference_url_cache(ctx: dict[str, Any], value: dict[str, Any]) -> None:
    write_json(ctx["lab_dir"] / "responses" / "reference_urls.json", value)


async def public_reference(
    ctx: dict[str, Any],
    uploader: ToAPIImageProvider,
    *,
    label: str,
    path: Path,
) -> AssetRef:
    cache = load_reference_url_cache(ctx)
    digest = sha256(path)
    entry = cache.get(label) if isinstance(cache.get(label), dict) else {}
    url = str(entry.get("url") or "") if entry.get("sha256") == digest else ""
    ref = AssetRef(
        id=label,
        type="image",
        path=str(path),
        url=url or None,
        metadata={"logical_role": label, "sha256": digest},
    )
    await uploader.ensure_reference_image_urls([ref])
    if not ref.url:
        raise ProviderBadResponseError(f"Could not obtain public reference URL for {path}")
    cache[label] = {"sha256": digest, "url": ref.url, "updated_at": utc_now()}
    save_reference_url_cache(ctx, cache)
    return AssetRef(
        id=label,
        type="image",
        url=ref.url,
        metadata={"logical_role": label, "sha256": digest},
    )


def reference_by_role(public_refs: dict[str, AssetRef], roles: list[str]) -> list[AssetRef]:
    return [public_refs[role].model_copy(deep=True) for role in roles]


def provider_settings_for_lab(config_path: Path):
    settings = load_settings(str(config_path))
    aibox = settings.providers.get("aibox")
    if aibox is not None:
        aibox.models["image"] = IMAGE_MODEL
        aibox.models["prop"] = IMAGE_MODEL
        aibox.options["aibox_max_attempts"] = 1
        aibox.options["max_attempts"] = 1
        aibox.options["aibox_request_timeout_seconds"] = max(
            int(aibox.options.get("aibox_request_timeout_seconds") or 240),
            240,
        )
    node = settings.nodes.get("prop_image_generation")
    if node is not None:
        node.model = f"aibox:{IMAGE_MODEL}"
        node.params["size"] = IMAGE_SIZE
        node.params["quality"] = IMAGE_QUALITY
        node.params["max_attempts"] = 1
    return settings


async def prompt_one(
    ctx: dict[str, Any],
    item: dict[str, Any],
    provider: Any,
    public_refs: dict[str, AssetRef],
    semaphore: asyncio.Semaphore,
    call_id: str,
) -> dict[str, Any]:
    case = ctx["cases"][item["case_id"]]
    candidate = ctx["candidates"][item["candidate_id"]]
    request = render_template(template_text(candidate), case)
    record: dict[str, Any] = {
        "success": False,
        "stage": "prop_prompt",
        "item_id": item["item_id"],
        "round": item["round"],
        "prop_name": case.name,
        "candidate_id": candidate.candidate_id,
        "reference_policy": candidate.reference_policy,
        "reference_roles": item["reference_roles"],
        "request": request,
        "started_at": utc_now(),
    }
    try:
        async with semaphore:
            draft = await provider.generate_json(
                request,
                PromptDraft,
                temperature=0.4,
                refs=reference_by_role(public_refs, item["reference_roles"]),
                metadata={
                    "node_name": "prop_prompt",
                    "asset_id": item["item_id"],
                    "prompt_asset_type": "prop_prompt_evolution",
                    "prompt_asset_name": item["item_id"],
                    "reasoning_effort": "high",
                },
            )
        final_prompt = str(draft.final_prompt or "").strip()
        if not final_prompt:
            raise ValueError("Gemini returned an empty final_prompt")
        record.update(
            {
                "success": True,
                "prompt_strategy": draft.prompt_strategy,
                "final_prompt": final_prompt,
                "prompt_checks": draft.prompt_checks,
                "final_prompt_sha256": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
            }
        )
    except Exception as exc:
        record.update({"error_type": type(exc).__name__, "error": str(exc)})
    record["completed_at"] = utc_now()
    record_completed(
        ctx,
        call_id=call_id,
        kind="text",
        stage="prop_prompt",
        item_id_value=item["item_id"],
        status="success" if record["success"] else "failure",
        error=record.get("error"),
    )
    write_json(stage_path(ctx, item, "prompt"), record)
    return record


async def image_one(
    ctx: dict[str, Any],
    item: dict[str, Any],
    provider: Any,
    public_refs: dict[str, AssetRef],
    media_store: MediaStore,
    semaphore: asyncio.Semaphore,
    call_id: str,
    prompt_record: dict[str, Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "success": False,
        "stage": "prop_image_generation",
        "item_id": item["item_id"],
        "round": item["round"],
        "prop_name": item["prop_name"],
        "candidate_id": item["candidate_id"],
        "reference_policy": item["reference_policy"],
        "reference_roles": item["reference_roles"],
        "prompt_sha256": prompt_record.get("final_prompt_sha256"),
        "started_at": utc_now(),
    }
    try:
        prompt = str(prompt_record.get("final_prompt") or "").strip()
        if not prompt:
            raise ValueError("prop_image_generation has no final_prompt")
        refs = reference_by_role(public_refs, item["reference_roles"])
        async with semaphore:
            result = await provider.generate_image(
                prompt,
                refs=refs,
                size=IMAGE_SIZE,
                metadata={
                    "node_name": "prop_image_generation",
                    "asset_id": item["item_id"],
                    "model": IMAGE_MODEL,
                    "size": IMAGE_SIZE,
                    "quality": IMAGE_QUALITY,
                    "max_reference_images": len(refs),
                },
            )
            output_path = ctx["lab_dir"] / item["image_path"]
            await media_store.write_first_generated_image(ctx["lab_dir"], output_path, result)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise ValueError(f"Generated image was not saved: {output_path}")
        record.update(
            {
                "success": True,
                "output_path": item["image_path"],
                "output_sha256": sha256(output_path),
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "task_id": result.task_id,
                "task_status": result.task_status,
                "usage": result.usage,
                "image_urls": result.image_urls,
                "raw_response": result.raw_response,
            }
        )
    except Exception as exc:
        record.update({"error_type": type(exc).__name__, "error": str(exc)})
    record["completed_at"] = utc_now()
    record_completed(
        ctx,
        call_id=call_id,
        kind="image",
        stage="prop_image_generation",
        item_id_value=item["item_id"],
        status="success" if record["success"] else "failure",
        error=record.get("error"),
    )
    write_json(stage_path(ctx, item, "image"), record)
    return record


async def audit_one(
    ctx: dict[str, Any],
    item: dict[str, Any],
    provider: Any,
    uploader: ToAPIImageProvider,
    public_refs: dict[str, AssetRef],
    semaphore: asyncio.Semaphore,
    call_id: str,
    prompt_record: dict[str, Any],
    image_record: dict[str, Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "success": False,
        "stage": "prop_image_audit",
        "item_id": item["item_id"],
        "round": item["round"],
        "prop_name": item["prop_name"],
        "candidate_id": item["candidate_id"],
        "reference_policy": item["reference_policy"],
        "started_at": utc_now(),
    }
    try:
        image_path = (ctx["lab_dir"] / str(image_record["output_path"])).resolve()
        generated_ref = await public_reference(
            ctx,
            uploader,
            label=f"generated--{item['item_id']}",
            path=image_path,
        )
        refs = [generated_ref, public_refs["key_vision_anchor"].model_copy(deep=True)]
        async with semaphore:
            decision = await provider.generate_json(
                audit_request(item, str(prompt_record.get("final_prompt") or "")),
                StyleAudit,
                temperature=0.1,
                refs=refs,
                metadata={
                    "node_name": "prop_image_audit",
                    "asset_id": item["item_id"],
                    "prompt_asset_type": "prop_style_audit",
                    "prompt_asset_name": item["item_id"],
                    "reasoning_effort": "high",
                },
            )
        audit_payload = decision.model_dump(mode="json")
        style_score = float(decision.style_score)
        audit_gate = bool(decision.approved and style_score >= 7.0)
        record.update(
            {
                "success": True,
                "audit": audit_payload,
                "style_score": style_score,
                "audit_gate": audit_gate,
                "audited_reference_roles": ["generated_prop_board", "key_vision_anchor"],
            }
        )
    except Exception as exc:
        record.update({"error_type": type(exc).__name__, "error": str(exc)})
    record["completed_at"] = utc_now()
    record_completed(
        ctx,
        call_id=call_id,
        kind="text",
        stage="prop_image_audit",
        item_id_value=item["item_id"],
        status="success" if record["success"] else "failure",
        error=record.get("error"),
    )
    write_json(stage_path(ctx, item, "audit"), record)
    return record


async def audit_v2_one(
    ctx: dict[str, Any],
    item: dict[str, Any],
    provider: Any,
    uploader: ToAPIImageProvider,
    public_refs: dict[str, AssetRef],
    semaphore: asyncio.Semaphore,
    call_id: str,
    prompt_record: dict[str, Any],
    image_record: dict[str, Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "success": False,
        "stage": "prop_image_audit_v2",
        "audit_version": "v2",
        "item_id": item["item_id"],
        "round": item["round"],
        "prop_name": item["prop_name"],
        "candidate_id": item["candidate_id"],
        "reference_policy": item["reference_policy"],
        "started_at": utc_now(),
    }
    try:
        image_path = (ctx["lab_dir"] / str(image_record["output_path"])).resolve()
        generated_ref = await public_reference(
            ctx,
            uploader,
            label=f"generated--{item['item_id']}",
            path=image_path,
        )
        refs = [
            generated_ref,
            public_refs["key_vision_anchor"].model_copy(deep=True),
            public_refs["spatial_white_model"].model_copy(deep=True),
        ]
        async with semaphore:
            decision = await provider.generate_json(
                audit_request_v2(item, str(prompt_record.get("final_prompt") or "")),
                StyleAuditV2,
                temperature=0.1,
                refs=refs,
                metadata={
                    "node_name": "prop_image_audit",
                    "audit_version": "v2",
                    "asset_id": item["item_id"],
                    "prompt_asset_type": "prop_style_audit_v2",
                    "prompt_asset_name": item["item_id"],
                    "reasoning_effort": "high",
                },
            )
        audit_payload = decision.model_dump(mode="json")
        audit_gate = v2_audit_gate(decision)
        record.update(
            {
                "success": True,
                "audit": audit_payload,
                "style_score": float(decision.style_score),
                "white_model_semantic_leakage_score": float(decision.white_model_semantic_leakage_score),
                "white_model_semantic_leakage_detected": bool(decision.white_model_semantic_leakage_detected),
                "semantic_leakage_gate": not decision.white_model_semantic_leakage_detected
                and decision.white_model_semantic_leakage_score >= 9.0,
                "audit_gate": audit_gate,
                "audited_reference_roles": [
                    "generated_prop_board",
                    "key_vision_anchor",
                    "spatial_white_model",
                ],
            }
        )
    except Exception as exc:
        record.update({"error_type": type(exc).__name__, "error": str(exc)})
    record["completed_at"] = utc_now()
    record_completed(
        ctx,
        call_id=call_id,
        kind="text",
        stage="prop_image_audit_v2",
        item_id_value=item["item_id"],
        status="success" if record["success"] else "failure",
        error=record.get("error"),
    )
    write_json(stage_path(ctx, item, "audit_v2"), record)
    return record


async def run_lab(ctx: dict[str, Any], *, prompt_concurrency: int, image_concurrency: int, audit_concurrency: int) -> dict[str, Any]:
    settings = provider_settings_for_lab(ctx["config_path"])
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx["lab_dir"])
    prompt_provider = router.text("prop", node_name="prop_prompt")
    audit_provider = router.text("prop", node_name="prop_image_audit")
    image_provider = router.image("prop", node_name="prop_image_generation")
    if str(getattr(image_provider, "model", "")) != IMAGE_MODEL:
        raise ValueError(
            f"prop_image_generation must use {IMAGE_MODEL}, got {getattr(image_provider, 'model', '-')!r}"
        )
    for label, provider in (("prop_prompt", prompt_provider), ("prop_image_audit", audit_provider)):
        if str(getattr(provider, "model", "")) != TEXT_MODEL:
            raise ValueError(f"{label} must use {TEXT_MODEL}, got {getattr(provider, 'model', '-')!r}")

    uploader_settings = settings.providers.get("toapi")
    if uploader_settings is None:
        raise ValueError("The configured runtime has no toapi provider for local reference uploads")
    uploader = ToAPIImageProvider(uploader_settings, settings.runtime)
    public_refs = {
        "spatial_white_model": await public_reference(
            ctx,
            uploader,
            label="spatial_white_model",
            path=ctx["snapshots"]["spatial_white_model"],
        ),
        "key_vision_anchor": await public_reference(
            ctx,
            uploader,
            label="key_vision_anchor",
            path=ctx["snapshots"]["key_vision_anchor"],
        ),
    }
    media_store = MediaStore(ProjectLayout(settings), timeout_seconds=settings.runtime.request_timeout_seconds)
    text_budget = int(ctx["experiment"].get("budgets", {}).get("text") or TEXT_BUDGET)
    image_budget = int(ctx["experiment"].get("budgets", {}).get("image") or IMAGE_BUDGET)
    prompt_sem = asyncio.Semaphore(max(1, prompt_concurrency))
    image_sem = asyncio.Semaphore(max(1, image_concurrency))
    audit_sem = asyncio.Semaphore(max(1, audit_concurrency))

    for round_index in range(1, len(PROP_CASES) + 1):
        round_items = [item for item in ctx["items"] if item["round"] == round_index]
        prompt_pending = [item for item in round_items if any_record(ctx, item, "prompt") is None]
        prompt_calls: list[tuple[dict[str, Any], str]] = []
        for item in prompt_pending:
            prompt_calls.append(
                (item, reserve_call(ctx, kind="text", stage="prop_prompt", item_id_value=item["item_id"], limit=text_budget))
            )
        if prompt_calls:
            await asyncio.gather(
                *[
                    prompt_one(ctx, item, prompt_provider, public_refs, prompt_sem, call_id)
                    for item, call_id in prompt_calls
                ]
            )

        image_pending = [
            item
            for item in round_items
            if successful_record(ctx, item, "prompt") is not None and any_record(ctx, item, "image") is None
        ]
        image_calls: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for item in image_pending:
            prompt_record = successful_record(ctx, item, "prompt")
            if prompt_record is None:
                continue
            image_calls.append(
                (
                    item,
                    reserve_call(ctx, kind="image", stage="prop_image_generation", item_id_value=item["item_id"], limit=image_budget),
                    prompt_record,
                )
            )
        if image_calls:
            await asyncio.gather(
                *[
                    image_one(ctx, item, image_provider, public_refs, media_store, image_sem, call_id, prompt_record)
                    for item, call_id, prompt_record in image_calls
                ]
            )

        audit_pending = [
            item
            for item in round_items
            if successful_record(ctx, item, "prompt") is not None
            and successful_record(ctx, item, "image") is not None
            and any_record(ctx, item, "audit") is None
        ]
        audit_calls: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
        for item in audit_pending:
            prompt_record = successful_record(ctx, item, "prompt")
            image_record = successful_record(ctx, item, "image")
            if prompt_record is None or image_record is None:
                continue
            audit_calls.append(
                (
                    item,
                    reserve_call(ctx, kind="text", stage="prop_image_audit", item_id_value=item["item_id"], limit=text_budget),
                    prompt_record,
                    image_record,
                )
            )
        if audit_calls:
            await asyncio.gather(
                *[
                    audit_one(ctx, item, audit_provider, uploader, public_refs, audit_sem, call_id, prompt_record, image_record)
                    for item, call_id, prompt_record, image_record in audit_calls
                ]
            )
        print(
            f"[prop-smoke] round {round_index}/{len(PROP_CASES)} complete: "
            f"{round_items[0]['prop_name']} | reserved images={reserved_count(ctx, 'image')}/{image_budget}",
            flush=True,
        )

    return inspect_lab(ctx)


async def reaudit_v2(ctx: dict[str, Any], *, audit_concurrency: int) -> dict[str, Any]:
    """Re-audit existing generated boards with the white model as an explicit third reference."""
    settings = provider_settings_for_lab(ctx["config_path"])
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx["lab_dir"])
    audit_provider = router.text("prop", node_name="prop_image_audit")
    if str(getattr(audit_provider, "model", "")) != TEXT_MODEL:
        raise ValueError(f"prop_image_audit must use {TEXT_MODEL}, got {getattr(audit_provider, 'model', '-')!r}")

    uploader_settings = settings.providers.get("toapi")
    if uploader_settings is None:
        raise ValueError("The configured runtime has no toapi provider for local reference uploads")
    uploader = ToAPIImageProvider(uploader_settings, settings.runtime)
    public_refs = {
        "spatial_white_model": await public_reference(
            ctx,
            uploader,
            label="spatial_white_model",
            path=ctx["snapshots"]["spatial_white_model"],
        ),
        "key_vision_anchor": await public_reference(
            ctx,
            uploader,
            label="key_vision_anchor",
            path=ctx["snapshots"]["key_vision_anchor"],
        ),
    }
    text_budget = int(ctx["experiment"].get("budgets", {}).get("text") or TEXT_BUDGET)
    audit_sem = asyncio.Semaphore(max(1, audit_concurrency))
    pending: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
    for item in ctx["items"]:
        prompt_record = successful_record(ctx, item, "prompt")
        image_record = successful_record(ctx, item, "image")
        if prompt_record is None or image_record is None or any_record(ctx, item, "audit_v2") is not None:
            continue
        pending.append(
            (
                item,
                reserve_call(
                    ctx,
                    kind="text",
                    stage="prop_image_audit_v2",
                    item_id_value=item["item_id"],
                    limit=text_budget,
                ),
                prompt_record,
                image_record,
            )
        )
    if pending:
        await asyncio.gather(
            *[
                audit_v2_one(
                    ctx,
                    item,
                    audit_provider,
                    uploader,
                    public_refs,
                    audit_sem,
                    call_id,
                    prompt_record,
                    image_record,
                )
                for item, call_id, prompt_record, image_record in pending
            ]
        )
    print(f"[prop-smoke] v2 audit complete: audited={len(pending)} | reserved text={reserved_count(ctx, 'text')}/{text_budget}", flush=True)
    return inspect_lab(ctx)


def inspect_lab(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt_success = sum(1 for item in ctx["items"] if successful_record(ctx, item, "prompt") is not None)
    image_success = sum(1 for item in ctx["items"] if successful_record(ctx, item, "image") is not None)
    audit_v2_success = sum(1 for item in ctx["items"] if successful_record(ctx, item, "audit_v2") is not None)
    audit_v1_success = sum(1 for item in ctx["items"] if successful_record(ctx, item, "audit") is not None)
    audit_success = sum(
        1
        for item in ctx["items"]
        if successful_record(ctx, item, "audit_v2") is not None
        or successful_record(ctx, item, "audit") is not None
    )
    audit_pass = sum(
        1
        for item in ctx["items"]
        if (
            (record := successful_record(ctx, item, "audit_v2")) is not None
            or (record := successful_record(ctx, item, "audit")) is not None
        )
        and bool(record.get("audit_gate"))
    )
    semantic_leakage_gate_pass = sum(
        1
        for item in ctx["items"]
        if (record := successful_record(ctx, item, "audit_v2")) is not None
        and bool(record.get("semantic_leakage_gate"))
    )
    return {
        "lab": str(ctx["lab_dir"]),
        "status": ctx["experiment"].get("status"),
        "image_budget": ctx["experiment"].get("budgets", {}).get("image"),
        "image_calls_reserved": reserved_count(ctx, "image"),
        "text_calls_reserved": reserved_count(ctx, "text"),
        "items": len(ctx["items"]),
        "prompt_success": prompt_success,
        "image_success": image_success,
        "audit_success": audit_success,
        "audit_v1_success": audit_v1_success,
        "audit_v2_success": audit_v2_success,
        "audit_gate_pass": audit_pass,
        "semantic_leakage_gate_pass": semantic_leakage_gate_pass,
        "key_vision_snapshot": str(ctx["snapshots"]["key_vision_anchor"]),
        "white_template_snapshot": str(ctx["snapshots"]["spatial_white_model"]),
    }


def image_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
            image.thumbnail((1400, 1400))
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=86, method=6)
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/webp;base64,{payload}"
    except Exception:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def load_successful_records(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in ctx["items"]:
        prompt = successful_record(ctx, item, "prompt")
        image = successful_record(ctx, item, "image")
        audit_v2 = successful_record(ctx, item, "audit_v2")
        audit_v1 = successful_record(ctx, item, "audit")
        audit = audit_v2 or audit_v1
        records.append(
            {
                "item": item,
                "prompt": prompt,
                "image": image,
                "audit": audit,
                "audit_v1": audit_v1,
                "audit_v2": audit_v2,
            }
        )
    return records


def candidate_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        subset = [row for row in records if row["item"]["candidate_id"] == candidate.candidate_id]
        scored = [float(row["audit"]["style_score"]) for row in subset if row["audit"] and row["audit"].get("style_score") is not None]
        gates = [bool(row["audit"].get("audit_gate")) for row in subset if row["audit"]]
        leakage_scores = [
            float(row["audit"]["white_model_semantic_leakage_score"])
            for row in subset
            if row["audit"] and row["audit"].get("white_model_semantic_leakage_score") is not None
        ]
        leakage_gates = [
            bool(row["audit"].get("semantic_leakage_gate"))
            for row in subset
            if row["audit"] and row["audit"].get("white_model_semantic_leakage_score") is not None
        ]
        rows.append(
            {
                "candidate": candidate,
                "n": len(subset),
                "scored_n": len(scored),
                "mean_style": sum(scored) / len(scored) if scored else None,
                "pass_rate": sum(gates) / len(gates) if gates else None,
                "mean_leakage": sum(leakage_scores) / len(leakage_scores) if leakage_scores else None,
                "leakage_pass_rate": sum(leakage_gates) / len(leakage_gates) if leakage_gates else None,
                "scores": scored,
            }
        )
    return rows


def build_report(ctx: dict[str, Any], output: Path | None = None) -> dict[str, Any]:
    output = (output or (ctx["lab_dir"] / "report.html")).resolve()
    records = load_successful_records(ctx)
    summaries = candidate_summary(records)
    scored_summaries = [row for row in summaries if row["mean_style"] is not None]
    gated_summaries = [row for row in scored_summaries if (row["pass_rate"] or 0) > 0]
    winner = max(
        gated_summaries or scored_summaries,
        key=lambda row: (
            row["pass_rate"] or 0,
            row["mean_style"] or 0,
            row["mean_leakage"] or 0,
        ),
    ) if scored_summaries else None
    image_success = sum(1 for row in records if row["image"])
    audit_success = sum(1 for row in records if row["audit"])
    audit_v2_success = sum(1 for row in records if row["audit_v2"])
    image_failures = [row for row in records if any_record(ctx, row["item"], "image") and not row["image"]]
    prompt_failures = [row for row in records if any_record(ctx, row["item"], "prompt") and not row["prompt"]]
    audit_failures = [
        row
        for row in records
        if (any_record(ctx, row["item"], "audit_v2") and not row["audit_v2"])
        or (not any_record(ctx, row["item"], "audit_v2") and any_record(ctx, row["item"], "audit") and not row["audit"])
    ]

    white_path = ctx["snapshots"]["spatial_white_model"]
    key_path = ctx["snapshots"]["key_vision_anchor"]
    white_src = image_data_uri(white_path)
    key_src = image_data_uri(key_path)

    summary_rows = []
    for row in summaries:
        mean = "—" if row["mean_style"] is None else f"{row['mean_style']:.2f}"
        pass_rate = "—" if row["pass_rate"] is None else f"{row['pass_rate'] * 100:.0f}%"
        leakage = "—" if row["mean_leakage"] is None else f"{row['mean_leakage']:.2f}"
        leakage_pass_rate = "—" if row["leakage_pass_rate"] is None else f"{row['leakage_pass_rate'] * 100:.0f}%"
        summary_rows.append(
            f"<tr><td>{esc(row['candidate'].title)}</td><td>{esc(row['candidate'].reference_policy)}</td>"
            f"<td>{row['scored_n']}/{row['n']}</td><td>{mean}</td><td>{leakage}</td><td>{leakage_pass_rate}</td><td>{pass_rate}</td>"
            f"<td>{esc(row['candidate'].hypothesis)}</td></tr>"
        )

    cards: list[str] = []
    for row in records:
        item = row["item"]
        image_record = row["image"]
        audit_record = row["audit"]
        prompt_record = row["prompt"]
        if image_record:
            image_path = (ctx["lab_dir"] / str(image_record["output_path"])).resolve()
            image_html = f"<img loading=\"lazy\" src=\"{image_data_uri(image_path)}\" alt=\"{esc(item['item_id'])}\">"
        else:
            image_html = "<div class=\"missing\">图片生成失败或尚未完成</div>"
        score = "—"
        leakage_score = "—"
        leakage_state = "未审查"
        gate = "未审查"
        audit_detail = ""
        if audit_record:
            score = f"{float(audit_record.get('style_score', 0)):.2f}/10"
            if audit_record.get("white_model_semantic_leakage_score") is not None:
                leakage_score = f"{float(audit_record.get('white_model_semantic_leakage_score', 0)):.2f}/10"
                leakage_state = "通过" if audit_record.get("semantic_leakage_gate") else "检测到/未通过"
            else:
                leakage_state = "v1 未含此项"
            gate = "通过" if audit_record.get("audit_gate") else "未通过"
            audit = audit_record.get("audit") or {}
            evidence = "".join(f"<li>白模语义证据：{esc(value)}</li>" for value in audit.get("white_model_leakage_evidence") or [])
            issues = evidence + "".join(f"<li>{esc(value)}</li>" for value in audit.get("issues") or [])
            audit_detail = (
                f"<p><strong>审查版本：</strong>{esc(audit_record.get('audit_version') or 'v1')}；<strong>审查依据：</strong>{esc(audit.get('rationale'))}</p>"
                f"<p><strong>建议：</strong>{esc(audit.get('recommendation'))}</p>"
                f"<ul>{issues or '<li>无明确问题</li>'}</ul>"
                f"<details><summary>审查分项</summary><pre>{esc(json.dumps(audit, ensure_ascii=False, indent=2))}</pre></details>"
            )
        prompt_detail = ""
        if prompt_record:
            prompt_detail = (
                f"<details><summary>Gemini prop_prompt 输出</summary>"
                f"<p>{esc(prompt_record.get('prompt_strategy'))}</p>"
                f"<pre>{esc(prompt_record.get('final_prompt'))}</pre></details>"
            )
        cards.append(
            f"""<article class=\"card\"><div class=\"image\">{image_html}</div>
            <div class=\"card-body\"><div class=\"eyebrow\">Round {item['round']} · {esc(item['prop_name'])}</div>
            <h3>{esc(item['candidate_title'])}</h3><p class=\"meta\">{esc(item['reference_policy'])} · {esc(', '.join(item['reference_roles']))}</p>
            <p><span class=\"score\">风格 {score}</span> <span class=\"score\">白模语义隔离 {leakage_score}</span> <span class=\"badge\">{esc(gate)}</span></p>
            <p class=\"meta\">白模硬门槛：{esc(leakage_state)}</p>
            {audit_detail}{prompt_detail}</div></article>"""
        )

    template_sections = []
    for candidate in CANDIDATES:
        template_sections.append(
            f"<details><summary>{esc(candidate.title)}</summary><pre>{esc(template_text(candidate))}</pre></details>"
        )

    winner_text = "尚无可比较的审查结果"
    if winner:
        if gated_summaries:
            winner_text = (
                f"{winner['candidate'].title}，硬门槛通过率 {winner['pass_rate'] * 100:.0f}%、"
                f"平均风格分 {winner['mean_style']:.2f}/10、平均白模语义隔离分 {winner['mean_leakage']:.2f}/10"
            )
        else:
            winner_text = (
                f"没有候选同时通过风格与白模语义硬门槛；最高风格分候选为 {winner['candidate'].title}"
                f"（{winner['mean_style']:.2f}/10，但不可直接采用）"
            )
    failure_text = (
        f"prop_prompt 失败 {len(prompt_failures)} 条；图像生成失败 {len(image_failures)} 条；"
        f"最新审查失败 {len(audit_failures)} 条；v2 白模语义审查完成 {audit_v2_success} 条。失败调用仍按预算计入。"
    )
    document = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>prop_gen 节点组迭代报告</title>
<style>
:root{{--bg:#0b0f14;--panel:#151c25;--panel2:#1d2733;--line:#334151;--text:#edf3f7;--muted:#9aa9b8;--cyan:#86d9dc;--gold:#f0c875;--green:#82d39e;--red:#f29a9a;--shadow:0 18px 50px rgba(0,0,0,.28)}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% -10%,#30495a 0,transparent 34%),var(--bg);color:var(--text);font:15px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}nav{{position:sticky;top:0;z-index:5;display:flex;gap:18px;align-items:center;padding:12px max(20px,calc((100vw - 1400px)/2));background:rgba(11,15,20,.9);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}}nav strong{{margin-right:auto}}nav a{{color:var(--muted);text-decoration:none;font-size:13px}}main{{max-width:1400px;margin:auto;padding:35px 20px 80px}}header{{padding:35px 0}}h1{{font-size:clamp(34px,6vw,68px);line-height:1.06;max-width:970px;margin:12px 0 18px}}h2{{font-size:29px;margin:58px 0 16px}}h3{{margin:5px 0 6px;font-size:20px}}.kicker,.eyebrow{{color:var(--cyan);font-size:12px;letter-spacing:.12em;text-transform:uppercase}}.lead{{max-width:950px;color:#cad5de;font-size:18px}}.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:25px 0}}.metric{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:15px;padding:18px;box-shadow:var(--shadow)}}.metric b{{display:block;color:var(--gold);font-size:28px}}.metric span{{color:var(--muted)}}.callout{{border:1px solid #62512b;background:#1e1a11;padding:18px 22px;border-radius:15px;margin:18px 0}}.callout strong{{color:var(--gold)}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:11px 13px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--cyan);font-weight:600;background:#17202b}}td{{color:#d8e0e7}}.reference-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.reference-grid figure{{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:15px;overflow:hidden}}figure img{{display:block;width:100%;background:#fff}}figcaption{{padding:12px 15px;color:var(--muted)}}.gallery{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:17px;overflow:hidden;box-shadow:var(--shadow)}}.image{{background:#fff;min-height:220px;display:flex;align-items:center;justify-content:center}}.image img{{display:block;width:100%;height:auto}}.missing{{padding:60px 20px;color:#8b9aaa}}.card-body{{padding:17px 18px}}.meta{{color:var(--muted);font-size:13px}}.score{{color:var(--gold);font-weight:700}}.badge{{display:inline-block;padding:2px 9px;border:1px solid #546576;border-radius:999px;color:#cbd8e2;font-size:12px}}pre{{white-space:pre-wrap;overflow:auto;background:#0e141b;border:1px solid var(--line);padding:14px;border-radius:10px;color:#d4e1e9;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}}details{{margin:12px 0}}summary{{cursor:pointer;color:var(--cyan);padding:6px 0}}.fail{{color:var(--red)}}footer{{border-top:1px solid var(--line);padding-top:20px;color:var(--muted);margin-top:60px}}@media(max-width:900px){{.metrics,.gallery,.reference-grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:620px){{.metrics,.gallery,.reference-grid{{grid-template-columns:1fr}}nav{{gap:9px;overflow:auto}}}}
</style></head><body>
<nav><strong>prop_gen evolution lab</strong><a href=\"#summary\">结论</a><a href=\"#references\">输入</a><a href=\"#comparison\">比较</a><a href=\"#gallery\">图片</a><a href=\"#templates\">模板</a></nav>
<main><header id=\"summary\"><div class=\"kicker\">Standalone report · 2026-08-09</div><h1>Gemini prop_prompt → GPT-Image-2-Guan prop_gen</h1>
<p class=\"lead\">项目当前从 saodi.yaml 流程提取出 {len(PROP_CASES)} 个真实 prop；每个 prop 进行 {len(CANDIDATES)} 种提示词/参考图策略试验，共授权 {ctx['experiment'].get('budgets', {}).get('image', IMAGE_BUDGET)} 张图片。主视觉锚点使用用户最新附带图片；白模只负责三视图排布、共同基线和留白。v2 Gemini 审计额外把白模作为第三张对照图，白模语义泄漏是硬门槛。</p>
<div class=\"metrics\"><div class=\"metric\"><b>{reserved_count(ctx, 'image')} / {ctx['experiment'].get('budgets', {}).get('image', IMAGE_BUDGET)}</b><span>图片调用 / 授权</span></div><div class=\"metric\"><b>{image_success}</b><span>成功保存图片</span></div><div class=\"metric\"><b>{audit_v2_success} / {audit_success}</b><span>v2 / 最新审查</span></div><div class=\"metric\"><b>{sum(1 for row in records if row['audit'] and row['audit'].get('audit_gate'))}</b><span>风格+白模硬门槛通过</span></div><div class=\"metric\"><b>{esc(winner['candidate'].title if winner else '—')}</b><span>当前比较策略</span></div></div>
<div class=\"callout\"><strong>实验结论：</strong>{esc(winner_text)}。{esc(failure_text)} 评分只用于比较模板，不会把烟测样例写入生产资产。</div></header>
<section id=\"references\"><h2>冻结输入</h2><div class=\"reference-grid\"><figure><img src=\"{white_src}\"><figcaption>空间参考：propboard_template.png；只用于三视图排布、共同基线和留白，禁止把其箱体/提手/旋钮等语义带入道具。</figcaption></figure><figure><img src=\"{key_src}\"><figcaption>风格参考：用户最新附带图片；只用于整体渲染媒介、光线、材质、色彩与成片质感。</figcaption></figure></div><details><summary>输入哈希与来源</summary><pre>{esc(json.dumps(ctx['manifest'], ensure_ascii=False, indent=2))}</pre></details></section>
<section id=\"comparison\"><h2>候选策略比较</h2><p>项目实际只有 {len(PROP_CASES)} 个提取出的 prop，因此每个候选在每个真实 prop 上各生成一次。平均风格分越高，说明与主视觉锚点越协调；平均白模语义隔离分越高，说明越没有把白模当成造型参考；通过率同时受两者硬门槛约束。</p><div class=\"table-wrap\"><table><thead><tr><th>候选</th><th>参考策略</th><th>有效评分</th><th>平均风格分</th><th>平均白模隔离分</th><th>白模硬门槛</th><th>总通过率</th><th>假设</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table></div></section>
<section id=\"gallery\"><h2>{reserved_count(ctx, 'image')} 张实验图片与审查</h2><p>按真实项目 prop 轮次展示；每张卡片包含 Gemini 生成的最终提示词、风格分、白模语义隔离分和问题证据。v2 审查按“生成图 + 主视觉 + 白模”三张参考图执行。</p><div class=\"gallery\">{''.join(cards)}</div></section>
<section id=\"templates\"><h2>裸模提示词模板</h2><p>以下模板发给 Gemini，用于生成最终直接发送给图像 API 的提示词；模板中没有项目路径、episode、模型名等与内容无关的外部字段。</p>{''.join(template_sections)}</section>
<section><h2>项目真实 prop 输入</h2><p>以下内容直接来自冻结的 prop_finalize 提取结果；没有为了填满烟测轮次而新增道具。</p><pre>{esc(json.dumps([case.__dict__ for case in PROP_CASES], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>运行与失败记录</h2><p class=\"fail\">{esc(failure_text)}</p><pre>{esc(json.dumps(inspect_lab(ctx), ensure_ascii=False, indent=2))}</pre></section>
<footer>本 HTML 为单文件报告，图片已内嵌，无外部字体、脚本或网络依赖。生成时间：{esc(datetime.now().astimezone().isoformat(timespec='seconds'))}。</footer>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8", newline="\n")
    return {"report": str(output), "image_success": image_success, "audit_success": audit_success, "winner": winner_text}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled prop_gen node evolution smoke lab")
    parser.add_argument("--lab-dir", default=str(DEFAULT_LAB_DIR))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Freeze project prop extraction and the two reference images")
    run_parser = subparsers.add_parser("run", help="Run Gemini prompt compilation, 20 image calls, and Gemini audits")
    run_parser.add_argument("--confirm-paid-calls", action="store_true", help="Required before dispatching image calls")
    run_parser.add_argument("--prompt-concurrency", type=int, default=3)
    run_parser.add_argument("--image-concurrency", type=int, default=2)
    run_parser.add_argument("--audit-concurrency", type=int, default=3)
    reaudit_parser = subparsers.add_parser("reaudit-v2", help="Re-audit existing images with the white model as audit reference 3")
    reaudit_parser.add_argument("--audit-concurrency", type=int, default=3)
    subparsers.add_parser("inspect", help="Inspect frozen inputs, ledger, and stage completion")
    report_parser = subparsers.add_parser("report", help="Build the standalone HTML report")
    report_parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lab_dir = Path(args.lab_dir).resolve()
    if args.command == "init":
        result = init_lab(lab_dir, Path(args.config))
    else:
        ctx = load_context(lab_dir)
        if args.command == "inspect":
            result = inspect_lab(ctx)
        elif args.command == "report":
            result = build_report(ctx, Path(args.output).resolve() if args.output else None)
        elif args.command == "run":
            if not args.confirm_paid_calls:
                raise ValueError("run requires --confirm-paid-calls because it dispatches real image calls")
            result = asyncio.run(
                run_lab(
                    ctx,
                    prompt_concurrency=args.prompt_concurrency,
                    image_concurrency=args.image_concurrency,
                    audit_concurrency=args.audit_concurrency,
                )
            )
            result["v2_audit"] = asyncio.run(reaudit_v2(ctx, audit_concurrency=args.audit_concurrency))
            report = build_report(ctx)
            result["report"] = report
        elif args.command == "reaudit-v2":
            result = asyncio.run(reaudit_v2(ctx, audit_concurrency=args.audit_concurrency))
            report = build_report(ctx)
            result["report"] = report
        else:
            raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
