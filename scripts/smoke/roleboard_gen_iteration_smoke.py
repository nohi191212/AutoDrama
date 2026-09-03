from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import io
import json
import mimetypes
import random
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import RoleboardPromptModelOutput  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402

import roleboard_node_evolution_smoke as base  # noqa: E402


PROJECT_ID = "saodi_0803"
KEY_VISION = ROOT / "outputs" / PROJECT_ID / "assets" / "images" / "key_visions" / "key_vision_original.png"
SPATIAL_TEMPLATE = ROOT / ".assets" / "image_templates" / "roleboard_template.png"
SAODI_ROLE_ASSET_DIR = ROOT / "outputs" / PROJECT_ID / "assets" / "json" / "roles"
INIT_LAB = ROOT / ".agents" / "skills" / "iterate-roleboard-nodes" / "scripts" / "init_lab.py"
SAODI_CONFIG = ROOT / "saodi.yaml"
MODEL_CATALOG = ROOT / "model_catalog.yaml.example"
CLIPBOARD_CONFIRMATION = Path(
    r"C:\Users\csh10\AppData\Local\Temp\codex-clipboard-fb58c381-3cf4-4576-9ff1-02ee6ee4ff96.png"
)

IMAGE_BUDGET = 20
GEMINI_BUDGET = 40
AUDIT_BUDGET = 20
IMAGE_SIZE = "3840x2160"
IMAGE_QUALITY = "high"
PROMPT_TEMPERATURE = 0.35
AUDIT_TEMPERATURE = 0.10
MAX_CONCURRENCY = 3
PASS_THRESHOLD = 7.0
MIN_VALID_SAMPLES_PER_CANDIDATE = 2
CONTACT_SHEET_SEED = 20260809

ROLE_SHORT = {"叶凡": "yf", "柳菡烟": "lhy", "李德海": "ldh"}
DESIGN_AXES = (
    "face_and_skull_geometry",
    "body_build_and_proportion",
    "neutral_posture",
    "hairline_mass_and_construction",
    "garment_silhouette_and_layer_construction",
    "material_palette_and_footwear",
    "restrained_signature_details",
)
POLICIES = {
    "template_only": {
        "label": "白模单参考",
        "image_refs": ("spatial_template",),
        "hypothesis": "只给白模时，三视图空间约束更干净，但主视觉画风需要靠提示词自行描述。",
    },
    "template_plus_style": {
        "label": "白模+主视觉",
        "image_refs": ("spatial_template", "key_vision_style"),
        "hypothesis": "同时给白模和主视觉时，白模负责三视图空间，主视觉负责画风，减少职责混淆。",
    },
}
COMPILERS = {
    "minimal": "保持最短可执行提示词：只写稳定事实、可见设计、三视图几何和参考图职责，不写解释或项目背景。",
    "style_explicit": "将主视觉的画风约束写得可观察：只描述渲染媒介、面部渲染处理、材质响应、光线性格、色彩关系和完成度；明确不复制主视觉中的人物、服装、场景或构图。",
    "proportion_first": "把角色比例写成可见约束：头身关系、肩胸腰胯、四肢长度、手脚尺度、年龄与角色身份匹配，并要求三视图保持同一比例；不要把风格审美词当作比例证据。",
    "balanced": "按优先级组织：先锁定三视图与比例，再锁定跨视图同一身份，最后匹配主视觉画风；提示词保持简洁，避免堆叠质量形容词。",
}


class RoleboardAuditOutput(BaseModel):
    style_match_score: float = Field(ge=0, le=10)
    style_match_evidence: str = Field(min_length=1)
    proportion_coordination_score: float = Field(ge=0, le=10)
    proportion_coordination_evidence: str = Field(min_length=1)
    overall_judgment: str = Field(min_length=1)


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_-]+", "-", value).strip("-").lower()
    return text or base.appearance_slug(value)


def role_short(role_name: str) -> str:
    return ROLE_SHORT.get(role_name, slug(role_name)[:12])


def safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return str(value)


def make_runtime_config(path: Path) -> Path:
    raw = yaml.safe_load(SAODI_CONFIG.read_text(encoding="utf-8")) or {}
    raw["apikeys_file"] = str(ROOT / "apikeys.yaml")
    raw["model_catalog_file"] = str(MODEL_CATALOG)
    raw.setdefault("project", {})["id"] = PROJECT_ID
    raw["project"]["script_chapters_dir"] = str(ROOT / "inputs" / "saodi_chapters")
    raw.setdefault("output", {})["root_dir"] = str(ROOT / "outputs")
    node = raw.setdefault("nodes", {}).setdefault("roleboard_image_generation", {})
    node["model"] = "aibox:gpt-image-2-guan"
    node["params"] = {
        **node.get("params", {}),
        "roleboard_prompt_template": "aibox_gpt_image_2_guan",
        "roleboard_image_generation_concurrency": MAX_CONCURRENCY,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
    }
    raw.setdefault("providers", {}).setdefault("aibox", {}).setdefault("models", {})
    raw["providers"]["aibox"]["models"]["image"] = "gpt-image-2-guan"
    raw["providers"]["aibox"]["models"]["roleboard"] = "gpt-image-2-guan"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return path


def load_experiment(lab_dir: Path) -> dict[str, Any]:
    return read_json(lab_dir / "experiment.json")


def save_experiment(lab_dir: Path, experiment: dict[str, Any]) -> None:
    write_json(lab_dir / "experiment.json", experiment)


def role_records(ctx: base.LabContext) -> list[dict[str, Any]]:
    payload = read_json(ctx.lab_dir / "contracts" / "appearances.json")
    rows = [
        item
        for item in payload.get("appearances", [])
        if str(item.get("asset_role") or "base") == "base"
    ]
    if len(rows) < 3:
        raise ValueError(f"roleboard_gen iteration requires at least three base appearances, found {len(rows)}")
    return rows[:3]


def fact_payload(record: dict[str, Any]) -> dict[str, Any]:
    facts = dict(record.get("hard_facts") or {})
    result: dict[str, Any] = {
        "role_type": record.get("role_tier"),
        "identity_invariants": facts.get("identity_invariants") or [],
        "wardrobe": facts.get("wardrobe") or [],
    }
    for key in ("time_period", "age_band"):
        if facts.get(key):
            result[key] = facts[key]
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def character_contract(role_tier: str | None, hard_facts: dict[str, Any]) -> str:
    facts = {
        "role_type": role_tier,
        "identity_invariants": hard_facts.get("identity_invariants") or [],
        "wardrobe": hard_facts.get("wardrobe") or [],
        "time_period": hard_facts.get("time_period"),
        "age_band": hard_facts.get("age_band"),
    }
    facts = {key: value for key, value in facts.items() if value not in (None, "", [])}
    return (
        "稳定事实："
        + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
        + "\n可设计但需保持稳定的轴：脸部与头骨几何、身体比例、发型轮廓、服装层次、材质与一到两个克制的识别细节。"
        + "\n禁止把动作、情绪、受伤、手持物、能量效果或剧情瞬间状态写进身份板。"
    )


def freeze_contracts(ctx: base.LabContext) -> None:
    role_finalize = read_json(ctx.lab_dir / "inputs" / "role_finalize.json")
    frozen: list[dict[str, Any]] = []
    for role in role_finalize.get("final_roles", []):
        role_name = str(role.get("name") or "").strip()
        for asset in role.get("appearance_assets") or []:
            if str(asset.get("asset_role") or "base").strip().lower() != "base":
                continue
            appearance_name = str(asset.get("name") or "base").strip() or "base"
            hard_facts = {
                "identity_invariants": list(asset.get("identity_invariants") or []),
                "wardrobe": list(asset.get("wardrobe") or []),
                "time_period": asset.get("time_period"),
                "age_band": asset.get("age_band"),
                "valid_from_event": asset.get("valid_from_event"),
                "valid_to_event": asset.get("valid_to_event"),
            }
            frozen.append(
                {
                    "role_name": role_name,
                    "role_tier": role.get("role_tier"),
                    "appearance_name": appearance_name,
                    "appearance_key": f"{role_name}/{appearance_name}",
                    "asset_role": "base",
                    "reference_asset_name": None,
                    "hard_facts": hard_facts,
                    "design_decisions": {
                        axis: "开放设计轴：编译器可选择具体、稳定、可见的方案，不得新增剧情事实。"
                        for axis in DESIGN_AXES
                    },
                    "character_contract": character_contract(role.get("role_tier"), hard_facts),
                    "designable_axes_pending_review": [],
                    "forbidden_transient_state": [
                        "pose",
                        "emotion",
                        "injury",
                        "held_props",
                        "action",
                        "energy_state",
                        "event_only_damage",
                    ],
                    "provenance": asset.get("provenance") or {},
                    "review_status": "frozen",
                }
            )
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(frozen):
        for right in frozen[index + 1 :]:
            pairs.append(
                {
                    "left": left["appearance_key"],
                    "right": right["appearance_key"],
                    "same_role_variant_pair": False,
                    "face_geometry_difference_or_lineage": ["每个角色必须有独立的脸部几何，不以发色或衣服区分"],
                    "body_and_posture_difference_or_lineage": ["身体比例和年龄/角色类型应自然区分"],
                    "hair_silhouette_difference_or_lineage": ["可由编译器选择稳定且克制的轮廓差异"],
                    "wardrobe_silhouette_difference_or_lineage": ["不共享白模武者身份，不把所有人做成英雄装束"],
                    "shared_world_invariants": ["共享主视觉的渲染媒介、材质响应、光线性格和完成度"],
                    "review_status": "frozen",
                }
            )
    write_json(ctx.lab_dir / "contracts" / "appearances.json", {"schema_version": 1, "appearances": frozen})
    write_json(ctx.lab_dir / "contracts" / "cast-contrast-matrix.json", {"schema_version": 1, "pairs": pairs})


def validate_prototype_sources(ctx: base.LabContext, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the smoke samples tied to saodi's extracted role assets.

    The image prompt remains free of project metadata, but the experiment artifact
    must prove that every sampled character came from the production role
    extraction/finalization path rather than from an invented cast.
    """
    role_finalize_path = ctx.lab_dir / "inputs" / "role_finalize.json"
    role_finalize = read_json(role_finalize_path)
    final_roles = {
        str(role.get("name") or "").strip(): role
        for role in role_finalize.get("final_roles", [])
        if str(role.get("name") or "").strip()
    }
    role_refs = {
        str(name).strip(): str(path).strip()
        for name, path in (role_finalize.get("role_refs") or {}).items()
        if str(name).strip() and str(path).strip()
    }
    validated: list[dict[str, Any]] = []
    for record in records:
        role_name = str(record.get("role_name") or "").strip()
        if role_name not in final_roles:
            raise ValueError(f"roleboard prototype is not present in saodi role_finalize: {role_name}")
        role_ref = role_refs.get(role_name)
        if not role_ref:
            raise ValueError(f"roleboard prototype has no role_ref in role_finalize: {role_name}")
        source_path = (ROOT / "outputs" / PROJECT_ID / Path(role_ref)).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"saodi extracted role asset is missing: {source_path}")
        source_payload = read_json(source_path)
        source_extract = source_payload.get("extract") or {}
        source_assets = [
            asset
            for asset in source_extract.get("appearance_assets") or []
            if str(asset.get("asset_role") or "base").strip().lower() == "base"
        ]
        appearance_name = str(record.get("appearance_name") or "base").strip() or "base"
        source_asset = next(
            (asset for asset in source_assets if str(asset.get("name") or "base").strip() == appearance_name),
            None,
        )
        if source_asset is None:
            raise ValueError(f"saodi extracted role asset is missing: {role_name}/{appearance_name}")
        source_facts = {
            "identity_invariants": list(source_asset.get("identity_invariants") or []),
            "wardrobe": list(source_asset.get("wardrobe") or []),
            "time_period": source_asset.get("time_period"),
            "age_band": source_asset.get("age_band"),
            "valid_from_event": source_asset.get("valid_from_event"),
            "valid_to_event": source_asset.get("valid_to_event"),
        }
        record_facts = dict(record.get("hard_facts") or {})
        record_facts = {
            "identity_invariants": list(record_facts.get("identity_invariants") or []),
            "wardrobe": list(record_facts.get("wardrobe") or []),
            "time_period": record_facts.get("time_period"),
            "age_band": record_facts.get("age_band"),
            "valid_from_event": record_facts.get("valid_from_event"),
            "valid_to_event": record_facts.get("valid_to_event"),
        }
        if source_facts != record_facts:
            raise ValueError(f"frozen roleboard contract diverges from saodi role asset: {role_name}/{appearance_name}")
        validated.append(
            {
                "role_name": role_name,
                "appearance_key": record.get("appearance_key"),
                "role_tier": record.get("role_tier"),
                "appearance_name": appearance_name,
                "source_role_ref": role_ref.replace("\\", "/"),
                "source_role_file": str(source_path),
                "source_role_file_sha256": sha256(source_path),
                "source_node": str(role_finalize_path),
                "source_node_sha256": sha256(role_finalize_path),
                "identity_invariants": source_facts["identity_invariants"],
                "wardrobe": source_facts["wardrobe"],
                "provenance": record.get("provenance") or {},
            }
        )
    coverage = {
        "schema_version": 1,
        "validated": True,
        "source_project": PROJECT_ID,
        "source_node": str(role_finalize_path),
        "roles": validated,
    }
    write_json(ctx.lab_dir / "contracts" / "prototype-coverage.json", coverage)
    return coverage


def ref_record(ctx: base.LabContext, logical_role: str, index: int) -> dict[str, Any]:
    if logical_role == "spatial_template":
        path, digest = ctx.spatial_template_path, ctx.spatial_template_hash
    elif logical_role == "key_vision_style":
        path, digest = ctx.key_vision_path, ctx.key_vision_hash
    else:
        raise KeyError(logical_role)
    return {
        "index": index,
        "logical_role": logical_role,
        "path": str(path),
        "sha256": digest,
        "identity_transfer_allowed": False,
    }


def asset_ref(record: dict[str, Any]) -> AssetRef:
    return AssetRef(
        id=f"{record['logical_role']}-{record['index']}",
        type="image",
        path=str(record["path"]),
        metadata={
            "logical_role": record["logical_role"],
            "reference_index": record["index"],
            "sha256": record["sha256"],
            "mime_type": mimetypes.guess_type(str(record["path"]))[0] or "image/png",
        },
    )


def compiler_prompt(item: dict[str, Any], record: dict[str, Any]) -> str:
    policy = POLICIES[item["policy_id"]]
    reference_lines = [
        "参考图1：白模空间模板，只转移横向16:9身份板的三视图排布、前/真侧/后顺序、等尺度、共同脚底线、间距和完整全身可见；绝不复制白模人物的脸、身体、发型、冠、铠甲、肩甲、剑、靴子、服装、装饰或白色材质。"
    ]
    if "key_vision_style" in policy["image_refs"]:
        reference_lines.append(
            "参考图2：主视觉画风锚点，只转移角色画风的渲染媒介、面部渲染处理、皮肤/头发/布料材质响应、光线性格、色彩关系、空气感和完成度；绝不复制其中人物的长相、衣着、动作、道具、场景或构图。"
        )
    else:
        reference_lines.append(
            "没有提供主视觉画风参考；不得从白模的白色黏土材质推导项目画风，只保持中性、干净、可读的角色渲染。"
        )
    facts = json.dumps(fact_payload(record), ensure_ascii=False, separators=(",", ":"))
    return (
        "为一个角色生成一条可直接交给图像模型的中文身份板提示词。只使用下面的稳定事实，允许在未指定的稳定视觉轴上做克制、可见、可复用的设计补全；不要新增剧情事实。\n\n"
        + "\n".join(reference_lines)
        + "\n\n稳定事实："
        + facts
        + "\n\n"
        + COMPILERS[item["compiler_id"]]
        + "\n\n输出要求：只输出 JSON，字段只能是 roleboard_prompt、roleboard_negative_prompt、voice_profile_prompt、design_notes。"
        + " roleboard_prompt 只写最终可见画面，不写项目名、项目ID、文件路径、集数、来源资料、审计规则或制作流程。"
        + " 画面必须是一张干净的横向16:9单角色身份板，恰好三个分离的完整全身视图：左正面，中间严格90度真侧面朝画面左侧，右背面；等尺度、共同脚底线、自然中性站姿、无裁切、无重叠、无额外角度。"
        + " 三个视图必须是同一个人，脸部、头骨、发型结构、身体比例、服装层次、鞋和识别细节跨视图一致。"
        + " roleboard_negative_prompt 保持简短，只覆盖额外主体、身份漂移、比例失衡、视图重复或重叠、裁切、动作、道具、文字、字幕、logo、水印和畸形。"
        + " voice_profile_prompt 与 design_notes 没有必要时写空字符串。"
    )


def audit_prompt(item: dict[str, Any]) -> str:
    return (
        "审查参考图1中的角色身份板，只评分下面两项，输出具体可见证据，不要根据提示词声明推断成功。"
        "\n\n参考图2是白模空间模板，仅用于理解角色板的三视图和尺度关系。参考图3是主视觉画风锚点，仅用于判断角色画风；不要比较人物长相、衣着、剧情、场景或构图。"
        "\n\n评分1：style_match_score（0-10）。只看角色画风是否属于主视觉的同一渲染家族：3D/CG媒介、脸部渲染处理、皮肤/头发/布料材质响应、光线软硬与方向感、色彩关系、空气感、细节克制和完成度。忽略角色长相和服装内容。"
        "\n评分2：proportion_coordination_score（0-10）。只看角色身体比例是否协调、年龄和角色类型是否可信、头身/肩胸腰胯/四肢/手脚关系是否自然，以及三个视图之间比例是否一致。忽略画风好坏和服装审美。"
        "\n每项都必须写出主体、区域、关系和结果的像素证据；不要写‘高级’、‘很AI’或‘看起来不错’这类抽象判断。"
        "\n只输出一个 JSON 对象，字段：style_match_score、style_match_evidence、proportion_coordination_score、proportion_coordination_evidence、overall_judgment。"
        "不要输出 Markdown 代码围栏、字段外解释或前后缀文本；首字符必须是 {，末字符必须是 }。"
    )


def make_round1(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        role_name = str(record["role_name"])
        for policy_id in ("template_only", "template_plus_style"):
            items.append(
                {
                    "sample_id": f"r01-{policy_id}-{role_short(role_name)}",
                    "round_id": "r01-reference-policy",
                    "role_key": record["appearance_key"],
                    "role_name": role_name,
                    "appearance_name": record["appearance_name"],
                    "policy_id": policy_id,
                    "compiler_id": "minimal",
                    "candidate_id": f"r01-{policy_id}",
                    "reference_policy": POLICIES[policy_id]["image_refs"],
                    "hypothesis": POLICIES[policy_id]["hypothesis"],
                }
            )
    return items


def make_round_items(
    round_id: str,
    policy_id: str,
    compiler_ids: tuple[str, ...],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        role_name = str(record["role_name"])
        for compiler_id in compiler_ids:
            items.append(
                {
                    "sample_id": f"{round_id}-{policy_id}-{compiler_id}-{role_short(role_name)}",
                    "round_id": round_id,
                    "role_key": record["appearance_key"],
                    "role_name": role_name,
                    "appearance_name": record["appearance_name"],
                    "policy_id": policy_id,
                    "compiler_id": compiler_id,
                    "candidate_id": f"{round_id}-{policy_id}-{compiler_id}",
                    "reference_policy": POLICIES[policy_id]["image_refs"],
                    "hypothesis": POLICIES[policy_id]["hypothesis"],
                }
            )
    return items


def make_round4(records: list[dict[str, Any]], policy_id: str, compiler_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in (records[0], records[-1]):
        role_name = str(record["role_name"])
        items.append(
            {
                "sample_id": f"r04-checkpoint-{role_short(role_name)}",
                "round_id": "r04-final-checkpoint",
                "role_key": record["appearance_key"],
                "role_name": role_name,
                "appearance_name": record["appearance_name"],
                "policy_id": policy_id,
                "compiler_id": compiler_id,
                "candidate_id": "r04-champion-checkpoint",
                "reference_policy": POLICIES[policy_id]["image_refs"],
                "hypothesis": "新鲜 Gemini 提示词和新鲜图片采样仍能保持主视觉画风与角色比例。",
            }
        )
    return items


def write_batch_plan(ctx: base.LabContext, round_id: str, items: list[dict[str, Any]]) -> None:
    path = ctx.lab_dir / "batches" / f"{round_id}.json"
    if path.exists():
        return
    write_json(
        path,
        {
            "schema_version": 1,
            "batch_id": round_id,
            "phase": "roleboard_gen",
            "round": round_id,
            "frozen_controls": {
                "image_model": "gpt-image-2-guan",
                "image_size": IMAGE_SIZE,
                "image_quality": IMAGE_QUALITY,
                "prompt_model": "gemini-3.6-flash",
                "prompt_temperature": PROMPT_TEMPERATURE,
                "audit_model": "gemini-3.6-flash",
                "audit_temperature": AUDIT_TEMPERATURE,
                "audit_dimensions": ["style_match_score", "proportion_coordination_score"],
                "pass_threshold_each": PASS_THRESHOLD,
            },
            "items": items,
        },
    )
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "phase_plan_created",
            "phase": "roleboard_gen",
            "round": round_id,
            "batch_id": round_id,
            "sample_count": len(items),
            "image_sample_count": len(items),
            "at": utc_now(),
        },
    )


def reserved_count(ctx: base.LabContext, kind: str) -> int:
    return sum(
        int(row.get("count") or 1)
        for row in read_jsonl(ctx.lab_dir / "ledger.jsonl")
        if row.get("event") == "reserve" and row.get("kind") == kind
    )


async def reserve_call(ctx: base.LabContext, *, kind: str, stage: str, item: dict[str, Any]) -> str:
    limits = {"gemini": GEMINI_BUDGET, "image": IMAGE_BUDGET, "audit": AUDIT_BUDGET}
    if reserved_count(ctx, kind) >= limits[kind]:
        raise RuntimeError(f"{kind} call ceiling reached before {item['sample_id']}")
    call_id = f"{kind}-{uuid.uuid4().hex}"
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "reserve",
            "call_id": call_id,
            "kind": kind,
            "stage": stage,
            "phase": "roleboard_gen",
            "round": item["round_id"],
            "candidate_id": item["candidate_id"],
            "sample_id": item["sample_id"],
            "count": 1,
            "at": utc_now(),
        },
    )
    return call_id


def complete_call(ctx: base.LabContext, call_id: str, *, status: str, payload: dict[str, Any]) -> None:
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {"event": "complete", "call_id": call_id, "status": status, "at": utc_now(), **safe_json(payload)},
    )


def failure_status(exc: Exception, *, stage: str) -> str:
    if isinstance(exc, ProviderAuthError):
        return "provider_rejection"
    if isinstance(exc, (ProviderBadResponseError, OSError, TimeoutError)):
        message = str(exc).lower()
        if any(token in message for token in ("safety", "policy", "validation", "invalid prompt")):
            return "provider_rejection"
        return "transport_failure"
    if stage in {"roleboard_prompt", "roleboard_image_audit"}:
        return "schema_failure"
    return "provider_rejection"


def sample_rows(ctx: base.LabContext) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ctx.lab_dir / "results.jsonl"):
        if row.get("kind") == "sample_summary" and row.get("sample_id"):
            latest[str(row["sample_id"])] = row
    return latest


def ref_records_for_item(ctx: base.LabContext, item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref_record(ctx, role, index)
        for index, role in enumerate(POLICIES[item["policy_id"]]["image_refs"], start=1)
    ]


async def run_sample(
    ctx: base.LabContext,
    item: dict[str, Any],
    record_by_key: dict[str, dict[str, Any]],
    prompt_provider: Any,
    image_provider: Any,
    audit_provider: Any,
    media_store: MediaStore,
    existing: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sample_id = item["sample_id"]
    if sample_id in existing:
        return existing[sample_id]
    record = record_by_key[item["role_key"]]
    refs_meta = ref_records_for_item(ctx, item)
    prompt_text = compiler_prompt(item, record)
    prompt_path = ctx.lab_dir / "batches" / "prompts" / f"{sample_id}.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8", newline="\n")

    prompt_call = await reserve_call(ctx, kind="gemini", stage="roleboard_prompt", item=item)
    try:
        prompt_output = await prompt_provider.generate_json(
            prompt_text,
            RoleboardPromptModelOutput,
            temperature=PROMPT_TEMPERATURE,
            metadata={
                "node_name": "roleboard_prompt",
                "prompt_asset_type": "roleboard_prompt_iteration",
                "prompt_asset_name": sample_id,
                "sample_id": sample_id,
                "temperature": PROMPT_TEMPERATURE,
                "max_output_tokens": 4096,
            },
            refs=[asset_ref(meta) for meta in refs_meta],
        )
        prompt_payload = prompt_output.model_dump(mode="json")
        response_path = ctx.lab_dir / "responses" / "gemini" / f"{sample_id}.json"
        write_json(
            response_path,
            {
                "stage": "roleboard_prompt",
                "sample_id": sample_id,
                "input_prompt": prompt_text,
                "ordered_references": refs_meta,
                "output": prompt_payload,
            },
        )
        complete_call(
            ctx,
            prompt_call,
            status="success",
            payload={"stage": "roleboard_prompt", "response_path": str(response_path.relative_to(ctx.lab_dir)).replace("\\", "/")},
        )
    except Exception as exc:
        status = failure_status(exc, stage="roleboard_prompt")
        complete_call(
            ctx,
            prompt_call,
            status=status,
            payload={"stage": "roleboard_prompt", "error_type": type(exc).__name__, "error": str(exc)},
        )
        summary = {
            **item,
            "kind": "sample_summary",
            "status": status,
            "failure_stage": "roleboard_prompt",
            "error": str(exc),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        return summary

    core_prompt = str(prompt_payload.get("roleboard_prompt") or "").strip()
    negative = str(prompt_payload.get("roleboard_negative_prompt") or "").strip()
    image_prompt = core_prompt + (f"\n\n避免：{negative}" if negative else "")
    image_prompt_path = ctx.lab_dir / "batches" / "prompts" / f"{sample_id}.image.txt"
    image_prompt_path.write_text(image_prompt, encoding="utf-8", newline="\n")

    image_call = await reserve_call(ctx, kind="image", stage="roleboard_image_generation", item=item)
    image_path = ctx.lab_dir / "images" / "exploration" / f"{sample_id}.png"
    try:
        image_result = await image_provider.generate_image(
            image_prompt,
            refs=[asset_ref(meta) for meta in refs_meta],
            size=IMAGE_SIZE,
            metadata={
                "node_name": "roleboard_image_generation",
                "prompt_asset_type": "roleboard_image_generation_iteration",
                "prompt_asset_name": sample_id,
                "asset_id": sample_id,
                "model": "gpt-image-2-guan",
                "size": IMAGE_SIZE,
                "quality": IMAGE_QUALITY,
                "max_reference_images": len(refs_meta),
            },
        )
        await media_store.write_first_generated_image(ctx.lab_dir, image_path, image_result)
        response_path = ctx.lab_dir / "responses" / "image" / f"{sample_id}.json"
        write_json(
            response_path,
            {
                "stage": "roleboard_image_generation",
                "sample_id": sample_id,
                "provider": getattr(image_result, "provider", None),
                "model": getattr(image_result, "model", None),
                "request_id": getattr(image_result, "request_id", None),
                "task_id": getattr(image_result, "task_id", None),
                "image_urls": safe_json(getattr(image_result, "image_urls", None)),
                "usage": safe_json(getattr(image_result, "usage", None)),
                "raw_response": safe_json(getattr(image_result, "raw_response", None)),
            },
        )
        output_rel = str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/")
        output_hash = sha256(image_path)
        complete_call(
            ctx,
            image_call,
            status="success",
            payload={
                "stage": "roleboard_image_generation",
                "output_path": output_rel,
                "output_sha256": output_hash,
                "request_id": getattr(image_result, "request_id", None),
            },
        )
        append_jsonl(
            ctx.lab_dir / "results.jsonl",
            {
                "kind": "image",
                "stage": "roleboard_image_generation",
                "sample_id": sample_id,
                "status": "success",
                "output_path": output_rel,
                "output_sha256": output_hash,
                "at": utc_now(),
            },
        )
    except Exception as exc:
        status = failure_status(exc, stage="roleboard_image_generation")
        complete_call(
            ctx,
            image_call,
            status=status,
            payload={"stage": "roleboard_image_generation", "error_type": type(exc).__name__, "error": str(exc)},
        )
        summary = {
            **item,
            "kind": "sample_summary",
            "status": status,
            "failure_stage": "roleboard_image_generation",
            "error": str(exc),
            "prompt_output": prompt_payload,
            "image_prompt": image_prompt,
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        return summary

    audit_text = audit_prompt({**item, "prompt_output": prompt_payload, "image_prompt": image_prompt})
    audit_call = await reserve_call(ctx, kind="audit", stage="roleboard_image_audit", item=item)
    audit_refs = [
        {
            "index": 1,
            "logical_role": "generated_roleboard",
            "path": str(image_path),
            "sha256": sha256(image_path),
        },
        ref_record(ctx, "spatial_template", 2),
        ref_record(ctx, "key_vision_style", 3),
    ]
    try:
        audit_output = await audit_provider.generate_json(
            audit_text,
            RoleboardAuditOutput,
            temperature=AUDIT_TEMPERATURE,
            metadata={
                "node_name": "roleboard_image_audit",
                "prompt_asset_type": "roleboard_image_audit_iteration",
                "prompt_asset_name": sample_id,
                "sample_id": sample_id,
                "temperature": AUDIT_TEMPERATURE,
                "max_output_tokens": 2048,
            },
            refs=[asset_ref(meta) for meta in audit_refs],
        )
        audit_payload = audit_output.model_dump(mode="json")
        composite = (
            float(audit_output.style_match_score)
            + float(audit_output.proportion_coordination_score)
        ) / 2
        approved = bool(
            audit_output.style_match_score >= PASS_THRESHOLD
            and audit_output.proportion_coordination_score >= PASS_THRESHOLD
        )
        audit_path = ctx.lab_dir / "responses" / "audit" / f"{sample_id}.json"
        write_json(
            audit_path,
            {
                "stage": "roleboard_image_audit",
                "sample_id": sample_id,
                "input_prompt": audit_text,
                "ordered_references": audit_refs,
                "output": audit_payload,
                "deterministic_composite": composite,
                "deterministic_approved": approved,
            },
        )
        complete_call(
            ctx,
            audit_call,
            status="success",
            payload={
                "stage": "roleboard_image_audit",
                "response_path": str(audit_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "style_match_score": audit_output.style_match_score,
                "proportion_coordination_score": audit_output.proportion_coordination_score,
                "approved": approved,
            },
        )
        summary = {
            **item,
            "kind": "sample_summary",
            "status": "success",
            "prompt_output": prompt_payload,
            "image_prompt": image_prompt,
            "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "image_sha256": sha256(image_path),
            "audit": audit_payload,
            "composite": composite,
            "approved": approved,
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        print(
            f"[roleboard-gen] {item['round_id']} {item['role_name']} "
            f"{item['policy_id']} {item['compiler_id']} "
            f"style={audit_output.style_match_score:.1f} "
            f"proportion={audit_output.proportion_coordination_score:.1f}",
            flush=True,
        )
        return summary
    except Exception as exc:
        status = failure_status(exc, stage="roleboard_image_audit")
        complete_call(
            ctx,
            audit_call,
            status=status,
            payload={"stage": "roleboard_image_audit", "error_type": type(exc).__name__, "error": str(exc)},
        )
        summary = {
            **item,
            "kind": "sample_summary",
            "status": status,
            "failure_stage": "roleboard_image_audit",
            "error": str(exc),
            "prompt_output": prompt_payload,
            "image_prompt": image_prompt,
            "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "image_sha256": sha256(image_path),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        return summary


async def run_round(
    ctx: base.LabContext,
    items: list[dict[str, Any]],
    records: list[dict[str, Any]],
    prompt_provider: Any,
    image_provider: Any,
    audit_provider: Any,
    media_store: MediaStore,
) -> None:
    if not items:
        return
    write_batch_plan(ctx, str(items[0]["round_id"]), items)
    record_by_key = {str(record["appearance_key"]): record for record in records}
    existing = sample_rows(ctx)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await run_sample(
                ctx,
                item,
                record_by_key,
                prompt_provider,
                image_provider,
                audit_provider,
                media_store,
                existing,
            )

    await asyncio.gather(*(one(item) for item in items))


def scored_rows(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in summaries
        if row.get("status") == "success" and isinstance(row.get("audit"), dict)
    ]


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = scored_rows(rows)
    if not scored:
        return {
            "n": 0,
            "mean": None,
            "style_mean": None,
            "proportion_mean": None,
            "pass_rate": None,
            "min": None,
            "variance": None,
        }
    values = [float(row["composite"]) for row in scored]
    style = [float(row["audit"]["style_match_score"]) for row in scored]
    proportion = [float(row["audit"]["proportion_coordination_score"]) for row in scored]
    mean = sum(values) / len(values)
    return {
        "n": len(scored),
        "mean": mean,
        "style_mean": sum(style) / len(style),
        "proportion_mean": sum(proportion) / len(proportion),
        "pass_rate": sum(bool(row.get("approved")) for row in scored) / len(scored),
        "min": min(values),
        "variance": sum((value - mean) ** 2 for value in values) / len(values),
    }


def choose_variant(
    summaries: list[dict[str, Any]],
    field: str,
    candidates: tuple[str, ...],
    *,
    prefer: str | None = None,
    min_valid_samples: int = MIN_VALID_SAMPLES_PER_CANDIDATE,
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    table: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        table[candidate] = stats([row for row in summaries if row.get(field) == candidate])
    eligible = [candidate for candidate in candidates if table[candidate]["n"] >= min_valid_samples]
    if not eligible:
        return None, table
    winner = max(
        eligible,
        key=lambda candidate: (
            float(table[candidate]["pass_rate"] or 0),
            float(table[candidate]["mean"] or -1),
            float(table[candidate]["style_mean"] or -1),
            float(table[candidate]["proportion_mean"] or -1),
            -float(table[candidate]["variance"] or 999),
            1 if candidate == prefer else 0,
        ),
    )
    return winner, table


def append_decision(
    ctx: base.LabContext,
    *,
    round_id: str,
    selected: str | None,
    table: dict[str, dict[str, Any]],
    reason: str,
    decision_status: str = "active",
    supersedes: str | None = None,
) -> None:
    append_jsonl(
        ctx.lab_dir / "results.jsonl",
        {
            "kind": "round_selection",
            "round_id": round_id,
            "selected": selected,
            "candidates": table,
            "reason": reason,
            "decision_status": decision_status,
            "supersedes": supersedes,
            "at": utc_now(),
        },
    )


def make_contact_sheet(ctx: base.LabContext, summaries: list[dict[str, Any]]) -> Path | None:
    rows = [
        row
        for row in summaries
        if row.get("status") == "success" and row.get("image_path")
        and (ctx.lab_dir / str(row["image_path"])).is_file()
    ]
    if not rows:
        return None
    rng = random.Random(CONTACT_SHEET_SEED)
    rows = list(rows)
    rng.shuffle(rows)
    output = ctx.lab_dir / "contact_sheets" / "blind" / "roleboard-all.png"
    answer_key = ctx.lab_dir / "contact_sheets" / "answer_keys" / "roleboard-all.json"
    thumb_w, thumb_h, label_h, columns = 360, 220, 34, 4
    canvas = Image.new(
        "RGB",
        (columns * thumb_w, ((len(rows) + columns - 1) // columns) * (thumb_h + label_h)),
        "#17191d",
    )
    draw = ImageDraw.Draw(canvas)
    mapping: dict[str, Any] = {}
    for index, row in enumerate(rows, start=1):
        blind_id = f"B{index:02d}"
        mapping[blind_id] = {"sample_id": row["sample_id"], "sha256": row.get("image_sha256")}
        path = ctx.lab_dir / str(row["image_path"])
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((thumb_w - 20, thumb_h - 20), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb_w, thumb_h), "#f1f1ef")
            tile.paste(image, ((thumb_w - image.width) // 2, (thumb_h - image.height) // 2))
        x = ((index - 1) % columns) * thumb_w
        y = ((index - 1) // columns) * (thumb_h + label_h)
        canvas.paste(tile, (x, y))
        draw.text((x + 10, y + thumb_h + 8), blind_id, fill="#f4f4f4")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    write_json(answer_key, {"seed": CONTACT_SHEET_SEED, "mapping": mapping})
    return output


def data_uri(path: Path, *, max_side: int = 1100) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=82, method=6)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def format_score(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def reconcile_orphaned_images(ctx: base.LabContext) -> list[str]:
    """Keep generated files visible in the report even if the process died before audit."""
    existing = sample_rows(ctx)
    plan_items: dict[str, dict[str, Any]] = {}
    for plan_path in sorted((ctx.lab_dir / "batches").glob("*.json")):
        try:
            payload = read_json(plan_path)
        except (OSError, json.JSONDecodeError):
            continue
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            if item.get("sample_id"):
                plan_items[str(item["sample_id"])] = dict(item)
    reconciled: list[str] = []
    image_dir = ctx.lab_dir / "images" / "exploration"
    for image_path in sorted(image_dir.glob("*.png")):
        sample_id = image_path.stem
        if sample_id in existing:
            continue
        item = plan_items.get(sample_id, {"sample_id": sample_id})
        prompt_output: dict[str, Any] = {}
        prompt_response_path = ctx.lab_dir / "responses" / "gemini" / f"{sample_id}.json"
        if prompt_response_path.is_file():
            prompt_payload = read_json(prompt_response_path)
            prompt_output = dict(prompt_payload.get("output") or {})
        image_prompt_path = ctx.lab_dir / "batches" / "prompts" / f"{sample_id}.image.txt"
        image_prompt = image_prompt_path.read_text(encoding="utf-8") if image_prompt_path.is_file() else ""
        row = {
            **item,
            "kind": "sample_summary",
            "status": "interrupted",
            "failure_stage": "roleboard_image_audit",
            "error": "image file exists but the audit summary was not written before the process was interrupted",
            "prompt_output": prompt_output,
            "image_prompt": image_prompt,
            "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "image_sha256": sha256(image_path),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", row)
        reconciled.append(sample_id)
    return reconciled


def build_report(ctx: base.LabContext) -> Path:
    reconcile_orphaned_images(ctx)
    experiment = load_experiment(ctx.lab_dir)
    rows = list(sample_rows(ctx).values())
    rows.sort(key=lambda row: (str(row.get("round_id")), str(row.get("sample_id"))))
    contact_sheet = make_contact_sheet(ctx, rows)
    ledger = read_jsonl(ctx.lab_dir / "ledger.jsonl")
    reserved = Counter(str(row.get("kind")) for row in ledger if row.get("event") == "reserve")
    completed = Counter(str(row.get("status")) for row in ledger if row.get("event") == "complete")
    decisions = [row for row in read_jsonl(ctx.lab_dir / "results.jsonl") if row.get("kind") == "round_selection"]
    success_rows = scored_rows(rows)
    prototype_coverage = (
        read_json(ctx.lab_dir / "contracts" / "prototype-coverage.json")
        if (ctx.lab_dir / "contracts" / "prototype-coverage.json").is_file()
        else {}
    )

    def group_table(field: str, candidates: tuple[str, ...]) -> str:
        body: list[str] = []
        for candidate in candidates:
            item_stats = stats([row for row in rows if row.get(field) == candidate])
            pass_text = "—" if item_stats["pass_rate"] is None else f"{item_stats['pass_rate'] * 100:.1f}%"
            body.append(
                "<tr>"
                f"<td>{html.escape(candidate)}</td>"
                f"<td>{item_stats['n']}</td>"
                f"<td>{format_score(item_stats['style_mean'])}</td>"
                f"<td>{format_score(item_stats['proportion_mean'])}</td>"
                f"<td>{format_score(item_stats['mean'])}</td>"
                f"<td>{pass_text}</td>"
                "</tr>"
            )
        return "".join(body)

    cards: list[str] = []
    for row in rows:
        image_html = ""
        if row.get("image_path"):
            path = ctx.lab_dir / str(row["image_path"])
            if path.is_file():
                image_html = (
                    f'<img loading="lazy" src="{data_uri(path)}" '
                    f'alt="{html.escape(str(row["sample_id"]))}">'
                )
        card_image = image_html or '<div class="missing">无图像输出</div>'
        audit = row.get("audit") or {}
        evidence = ""
        if audit:
            evidence = (
                f"<p><b>画风 {format_score(audit.get('style_match_score'))}</b>："
                f"{html.escape(str(audit.get('style_match_evidence') or ''))}</p>"
                f"<p><b>比例 {format_score(audit.get('proportion_coordination_score'))}</b>："
                f"{html.escape(str(audit.get('proportion_coordination_evidence') or ''))}</p>"
                f"<p><b>结论</b>：{html.escape(str(audit.get('overall_judgment') or ''))}</p>"
            )
        if row.get("error"):
            evidence += f"<p><b>未完成原因</b>：{html.escape(str(row.get('error')))}</p>"
        prompt_output = row.get("prompt_output") or {}
        cards.append(
            "<article class='card'>"
            f"<div class='card-image'>{card_image}</div>"
            f"<h3>{html.escape(str(row.get('sample_id')))}</h3>"
            f"<p class='meta'>{html.escape(str(row.get('role_name')))} · "
            f"{html.escape(str(row.get('policy_id')))} · "
            f"{html.escape(str(row.get('compiler_id')))} · "
            f"{html.escape(str(row.get('status')))}</p>"
            f"<p class='score'>综合 {format_score(row.get('composite'))}　"
            f"{('通过' if row.get('approved') else '未通过') if row.get('status') == 'success' else '未完成'}</p>"
            f"{evidence}"
            "<details><summary>查看 Gemini 输出与实际生图提示词</summary>"
            f"<pre>{html.escape(json.dumps(prompt_output, ensure_ascii=False, indent=2))}</pre>"
            f"<pre>{html.escape(str(row.get('image_prompt') or ''))}</pre>"
            "</details></article>"
        )

    contact_html = ""
    if contact_sheet and contact_sheet.is_file():
        contact_html = (
            f"<img class='contact' src='{data_uri(contact_sheet, max_side=1500)}' "
            "alt='blind contact sheet'>"
        )
    decision_html = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('round_id')))}</td>"
        f"<td>{html.escape(str(row.get('selected') or '未选出'))}</td>"
        f"<td>{html.escape(str(row.get('decision_status') or 'active'))}</td>"
        f"<td>{html.escape(str(row.get('supersedes') or ''))}</td>"
        f"<td><pre>{html.escape(json.dumps(row.get('candidates') or {}, ensure_ascii=False, indent=2))}</pre></td>"
        "</tr>"
        for row in decisions
    )
    prototype_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(role.get('role_name') or ''))}</td>"
        f"<td>{html.escape(str(role.get('appearance_name') or ''))}</td>"
        f"<td>{html.escape(str(role.get('source_role_ref') or ''))}</td>"
        f"<td><code>{html.escape(str(role.get('source_role_file_sha256') or ''))}</code></td>"
        "</tr>"
        for role in prototype_coverage.get("roles", [])
    )
    ref_hashes = {"spatial_template": sha256(SPATIAL_TEMPLATE), "key_vision": sha256(KEY_VISION)}
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>roleboard_gen 节点组迭代报告</title>
<style>
body{{margin:0;background:#f4f2ee;color:#202225;font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:1500px;margin:0 auto;padding:28px}}
h1{{margin:0 0 8px;font-size:30px}} h2{{margin-top:34px;border-bottom:1px solid #d4d0c9;padding-bottom:8px}}
.muted{{color:#6c7077}} .summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.metric{{background:white;border:1px solid #dedbd5;border-radius:12px;padding:14px}} .metric strong{{display:block;font-size:25px}}
table{{width:100%;border-collapse:collapse;background:white;margin:10px 0}} th,td{{border:1px solid #dedbd5;padding:8px;text-align:left;vertical-align:top}} th{{background:#eeece7}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:18px}} .card{{background:white;border:1px solid #dedbd5;border-radius:12px;padding:12px;overflow:hidden}}
.card-image{{background:#17191d;border-radius:8px;min-height:210px;display:flex;align-items:center;justify-content:center}} .card img{{display:block;width:100%;height:auto;border-radius:8px}}
.missing{{color:#f4d27c;padding:70px 0}} .meta,.score{{margin:4px 0;color:#62666c}} .score{{font-weight:700;color:#225d4b}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f1f0ed;padding:10px;border-radius:8px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}}
.contact{{max-width:100%;background:#17191d;border-radius:10px}} details{{margin-top:10px}} summary{{cursor:pointer;color:#315e9b}}
</style></head><body><main>
    <h1>roleboard_gen 节点组迭代报告</h1>
    <p class="muted">实验模式：execute · 仅实验目录产物，不自动晋级生产。生成链：Gemini roleboard_prompt → gpt-image-2-guan roleboard_image_generation → Gemini roleboard_image_audit。</p>
    <p class="muted">停止原因：{html.escape(str(experiment.get("stop_reason") or "未记录"))}</p>
<div class="summary">
<div class="metric"><span>图像调用</span><strong>{reserved["image"]} / {IMAGE_BUDGET}</strong><span class="muted">账本预留数</span></div>
<div class="metric"><span>成功并完成审查</span><strong>{len(success_rows)}</strong><span class="muted">样本</span></div>
<div class="metric"><span>Gemini 提示词调用</span><strong>{reserved["gemini"]} / {GEMINI_BUDGET}</strong><span class="muted">roleboard_prompt</span></div>
<div class="metric"><span>Gemini 审查调用</span><strong>{reserved["audit"]} / {AUDIT_BUDGET}</strong><span class="muted">两项 rubric</span></div>
</div>
<h2>实验问题与冻结控制</h2>
    <p>白模仅负责三视图空间模板；主视觉仅负责画风。首轮对比白模单参考和白模+主视觉双参考，后续根据两项 Gemini 评分继续迭代提示词。策略选择要求每个候选至少有 {MIN_VALID_SAMPLES_PER_CANDIDATE} 张有效审计样本，避免把传输失败当成证据。</p>
<table><tr><th>项目</th><th>值</th></tr>
<tr><td>白模</td><td>{html.escape(str(SPATIAL_TEMPLATE))}<br><code>{ref_hashes["spatial_template"]}</code></td></tr>
<tr><td>主视觉（用户确认）</td><td>{html.escape(str(KEY_VISION))}<br><code>{ref_hashes["key_vision"]}</code></td></tr>
<tr><td>图片模型</td><td>gpt-image-2-guan · {IMAGE_SIZE} · {IMAGE_QUALITY} · n=1</td></tr>
<tr><td>提示词模型</td><td>gemini-3.6-flash · temperature={PROMPT_TEMPERATURE}</td></tr>
<tr><td>审核模型</td><td>gemini-3.6-flash · temperature={AUDIT_TEMPERATURE}</td></tr>
    <tr><td>审核通过规则</td><td>style_match_score ≥ {PASS_THRESHOLD} 且 proportion_coordination_score ≥ {PASS_THRESHOLD}；两项等权平均为综合分</td></tr>
    </table>
    <h2>角色原型来源硬门槛</h2>
    <p>所有样本均绑定 saodi 已提取并由 role_finalize 冻结的 base appearance；实验只改变提示词编译和参考图策略，不新增角色原型。验证状态：{html.escape(str(prototype_coverage.get('validated', False)))}。</p>
    <table><tr><th>角色</th><th>appearance</th><th>saodi 角色资产</th><th>资产 SHA-256</th></tr>{prototype_rows}</table>
<h2>参考策略对比</h2>
<table><tr><th>策略</th><th>有效样本</th><th>画风均值</th><th>比例均值</th><th>综合均值</th><th>通过率</th></tr>{group_table("policy_id", ("template_only", "template_plus_style"))}</table>
<h2>提示词编译器对比</h2>
<table><tr><th>编译器</th><th>有效样本</th><th>画风均值</th><th>比例均值</th><th>综合均值</th><th>通过率</th></tr>{group_table("compiler_id", ("minimal", "style_explicit", "proportion_first", "balanced"))}</table>
<h2>轮次选择</h2>
    <table><tr><th>轮次</th><th>选择</th><th>状态</th><th>替代的决策</th><th>候选统计</th></tr>{decision_html}</table>
<h2>盲接触表</h2>{contact_html or "<p>暂无成功图像。</p>"}
<p class="muted">接触表图片只显示盲编号；映射保存在实验目录 contact_sheets/answer_keys/roleboard-all.json。</p>
    <h2>逐样本证据</h2><div class='cards'>{"".join(cards)}</div>
<h2>调用账本</h2>
<table><tr><th>类别</th><th>预留</th><th>完成状态计数</th></tr>
<tr><td>gemini / roleboard_prompt</td><td>{reserved["gemini"]}</td><td>{html.escape(json.dumps(dict(completed), ensure_ascii=False))}</td></tr>
<tr><td>image / roleboard_image_generation</td><td>{reserved["image"]}</td><td>{html.escape(json.dumps(dict(completed), ensure_ascii=False))}</td></tr>
<tr><td>audit / roleboard_image_audit</td><td>{reserved["audit"]}</td><td>{html.escape(json.dumps(dict(completed), ensure_ascii=False))}</td></tr></table>
<p class="muted">实验目录：{html.escape(str(ctx.lab_dir))}</p>
</main></body></html>"""
    report_path = ctx.lab_dir / "reports" / "report-standalone.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html_doc, encoding="utf-8", newline="\n")
    shutil.copy2(report_path, ctx.lab_dir / "report-standalone.html")
    return report_path


def write_final_summary(ctx: base.LabContext, stop_reason: str, winners: dict[str, Any]) -> None:
    rows = list(sample_rows(ctx).values())
    summary = stats(rows)
    text = (
        "# roleboard_gen 节点组迭代摘要\n\n"
        "- 模式：execute；仅实验目录产物，未晋级生产。\n"
        f"- 图像调用上限：{IMAGE_BUDGET}；账本预留：{reserved_count(ctx, 'image')}。\n"
        f"- 成功完成 Gemini 审查的样本：{summary['n']}。\n"
        f"- 画风均值：{format_score(summary['style_mean'])}；比例均值：{format_score(summary['proportion_mean'])}；综合均值：{format_score(summary['mean'])}。\n"
        f"- 轮次选择：{json.dumps(winners, ensure_ascii=False)}\n"
        f"- 停止原因：{stop_reason}\n"
        "- 离线报告：reports/report-standalone.html\n"
    )
    (ctx.lab_dir / "FINAL_SUMMARY.md").write_text(text, encoding="utf-8", newline="\n")
    write_json(
        ctx.lab_dir / "promotion" / "champion.json",
        {
            "production_eligible": False,
            "reason": "Budget-limited smoke iteration with two-dimensional Gemini audit; no full phase-1/2/3 promotion checkpoint.",
            "winners": winners,
            "image_model": "gpt-image-2-guan",
            "key_vision_sha256": sha256(KEY_VISION),
            "spatial_template_sha256": sha256(SPATIAL_TEMPLATE),
            "experiment_dir": str(ctx.lab_dir),
        },
    )


def close_unfinished_calls(ctx: base.LabContext, reason: str) -> list[str]:
    """Close reservations left open when an execute run is deliberately paused."""
    ledger = read_jsonl(ctx.lab_dir / "ledger.jsonl")
    reserved = {
        str(row.get("call_id")): row
        for row in ledger
        if row.get("event") == "reserve" and row.get("call_id")
    }
    completed = {
        str(row.get("call_id"))
        for row in ledger
        if row.get("event") == "complete" and row.get("call_id")
    }
    open_call_ids = [call_id for call_id in reserved if call_id not in completed]
    for call_id in open_call_ids:
        reserve = reserved[call_id]
        complete_call(
            ctx,
            call_id,
            status="interrupted",
            payload={
                "stage": reserve.get("stage"),
                "round": reserve.get("round"),
                "sample_id": reserve.get("sample_id"),
                "interruption_reason": reason,
            },
        )
    return open_call_ids


def make_corrected_checkpoint(
    records: list[dict[str, Any]],
    policy_id: str,
    compiler_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in (records[0], records[-1]):
        role_name = str(record["role_name"])
        items.append(
            {
                "sample_id": f"r04-corrected-checkpoint-{role_short(role_name)}",
                "round_id": "r04-corrected-checkpoint",
                "role_key": record["appearance_key"],
                "role_name": role_name,
                "appearance_name": record["appearance_name"],
                "policy_id": policy_id,
                "compiler_id": compiler_id,
                "candidate_id": "r04-corrected-champion-checkpoint",
                "reference_policy": POLICIES[policy_id]["image_refs"],
                "hypothesis": "在纠正后的参考策略和编译器下，对 saodi 已提取角色原型做新鲜生成与审查 checkpoint。",
            }
        )
    return items


def make_recovery_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        role_name = str(record["role_name"])
        items.append(
            {
                "sample_id": f"r04-recovery-style-explicit-{role_short(role_name)}",
                "round_id": "r04-recovery-style-explicit",
                "role_key": record["appearance_key"],
                "role_name": role_name,
                "appearance_name": record["appearance_name"],
                "policy_id": "template_plus_style",
                "compiler_id": "style_explicit",
                "candidate_id": "r04-recovery-style-explicit",
                "reference_policy": POLICIES["template_plus_style"]["image_refs"],
                "hypothesis": "恢复证据：在审核比较证据不足时，固定同时参考白模和主视觉，显式描述可观察画风，继续覆盖三个 saodi 提取角色。",
            }
        )
    return items


async def run_corrected_resume(ctx: base.LabContext, *, confirmed: bool) -> dict[str, Any]:
    """Resume a paused lab with the audit-corrected plan using only its remaining budget."""
    if not confirmed:
        raise ValueError("--confirm-paid-calls is required")
    if ctx.experiment.get("mode") != "execute":
        raise ValueError("The lab must be initialized with mode=execute")
    if ctx.experiment.get("status") == "completed":
        return {
            "lab": str(ctx.lab_dir),
            "report": str(ctx.lab_dir / "reports" / "report-standalone.html"),
            "image_reserved": reserved_count(ctx, "image"),
            "stop_reason": ctx.experiment.get("stop_reason"),
        }

    records = role_records(ctx)
    prototype_coverage = validate_prototype_sources(ctx, records)
    interrupted = close_unfinished_calls(ctx, "paused by audit intervention before corrected continuation")
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "audit_intervention",
            "action": "pause_and_correct_selection",
            "reason": "A reference policy was selected with insufficient valid audit coverage; the decision is superseded.",
            "interrupted_call_ids": interrupted,
            "minimum_valid_samples_per_candidate": MIN_VALID_SAMPLES_PER_CANDIDATE,
            "prototype_coverage": "contracts/prototype-coverage.json",
            "at": utc_now(),
        },
    )
    remaining_images = IMAGE_BUDGET - reserved_count(ctx, "image")
    if remaining_images < 8:
        raise RuntimeError(
            f"corrected continuation requires 8 remaining image calls, but only {remaining_images} remain"
        )

    round1_rows = [
        row
        for row in sample_rows(ctx).values()
        if row.get("round_id") == "r01-reference-policy"
    ]
    policy_winner, policy_table = choose_variant(
        round1_rows,
        "policy_id",
        ("template_only", "template_plus_style"),
        prefer="template_plus_style",
        min_valid_samples=MIN_VALID_SAMPLES_PER_CANDIDATE,
    )
    append_decision(
        ctx,
        round_id="r01-reference-policy-corrected",
        selected=policy_winner,
        table=policy_table,
        reason=(
            "原始选择因候选审计覆盖不足而废弃；只在首轮每个候选至少有两张有效审计时决策。"
            "本次修正使用白模单参考与白模+主视觉的首轮有效样本，保留 saodi 提取角色原型不变。"
        ),
        decision_status="active_corrected",
        supersedes="r01-reference-policy",
    )
    if policy_winner is None:
        stop_reason = "修正后首轮仍没有达到最低有效审计覆盖，未继续消耗付费图像调用"
        winners = {"reference_policy_corrected": None}
        exp = load_experiment(ctx.lab_dir)
        exp.update(
            {
                "status": "completed",
                "current_phase": "report",
                "stop_reason": stop_reason,
                "winners": winners,
                "intervention": "audit-corrected continuation",
                "completed_at": utc_now(),
            }
        )
        save_experiment(ctx.lab_dir, exp)
        write_final_summary(ctx, stop_reason, winners)
        report = build_report(ctx)
        return {"lab": str(ctx.lab_dir), "report": str(report), "image_reserved": reserved_count(ctx, "image"), "winners": winners}

    write_json(
        ctx.lab_dir / "experiment_plan_correction.json",
        {
            "schema_version": 1,
            "reason": "The original reference-policy decision used incomplete audit evidence and was superseded.",
            "minimum_valid_samples_per_candidate": MIN_VALID_SAMPLES_PER_CANDIDATE,
            "prototype_coverage": prototype_coverage,
            "remaining_image_calls_at_start": remaining_images,
            "rounds": [
                {
                    "round": "r03-corrected-reference-compiler",
                    "image_calls": 6,
                    "description": "在白模+主视觉双参考下比较 minimal 与 style_explicit；三张 saodi 提取角色各一张。",
                },
                {
                    "round": "r04-corrected-checkpoint",
                    "image_calls": 2,
                    "description": "用修正后的编译器对两个已提取角色做新鲜 checkpoint。",
                },
            ],
        },
    )

    settings = load_settings(str(ctx.config_path))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)

    corrected_round = "r03-corrected-reference-compiler"
    corrected_items = make_round_items(
        corrected_round,
        policy_winner,
        ("minimal", "style_explicit"),
        records,
    )
    await run_round(
        ctx,
        corrected_items,
        records,
        prompt_provider,
        image_provider,
        audit_provider,
        media_store,
    )
    corrected_rows = [
        row
        for row in sample_rows(ctx).values()
        if row.get("round_id") == corrected_round
    ]
    compiler_winner, compiler_table = choose_variant(
        corrected_rows,
        "compiler_id",
        ("minimal", "style_explicit"),
        prefer="style_explicit",
        min_valid_samples=MIN_VALID_SAMPLES_PER_CANDIDATE,
    )
    append_decision(
        ctx,
        round_id=corrected_round,
        selected=compiler_winner,
        table=compiler_table,
        reason="修正后的白模+主视觉双参考下，只在每个编译器至少两张有效审计时选择。",
        decision_status="active_corrected",
    )

    winners: dict[str, Any] = {
        "reference_policy_original": "invalid_insufficient_audit_coverage",
        "reference_policy_corrected": policy_winner,
        "compiler_corrected": compiler_winner,
    }
    stop_reason = "corrected continuation completed"
    if compiler_winner is None:
        stop_reason = "修正后的编译器比较没有达到最低有效审计覆盖，未继续做 checkpoint"
    else:
        checkpoint_items = make_corrected_checkpoint(records, policy_winner, compiler_winner)
        if IMAGE_BUDGET - reserved_count(ctx, "image") < len(checkpoint_items):
            stop_reason = "修正后的编译器已选出，但剩余图像预算不足以完成 checkpoint"
        else:
            await run_round(
                ctx,
                checkpoint_items,
                records,
                prompt_provider,
                image_provider,
                audit_provider,
                media_store,
            )
            winners["final_checkpoint"] = "r04-corrected-checkpoint"

    exp = load_experiment(ctx.lab_dir)
    exp.update(
        {
            "status": "completed",
            "current_phase": "report",
            "stop_reason": stop_reason,
            "winners": winners,
            "intervention": "audit-corrected continuation",
            "prototype_contract": {
                "source_project": PROJECT_ID,
                "coverage_path": "contracts/prototype-coverage.json",
                "validated": bool(prototype_coverage.get("validated")),
                "role_count": len(prototype_coverage.get("roles", [])),
            },
            "completed_at": utc_now(),
        }
    )
    save_experiment(ctx.lab_dir, exp)
    write_final_summary(ctx, stop_reason, winners)
    report = build_report(ctx)
    return {
        "lab": str(ctx.lab_dir),
        "report": str(report),
        "image_reserved": reserved_count(ctx, "image"),
        "image_success": sum(1 for row in sample_rows(ctx).values() if row.get("status") == "success"),
        "winners": winners,
        "stop_reason": stop_reason,
    }


async def run_recovery(ctx: base.LabContext, *, confirmed: bool) -> dict[str, Any]:
    """Use the exact remaining image budget for a clearly labeled evidence batch."""
    if not confirmed:
        raise ValueError("--confirm-paid-calls is required")
    if ctx.experiment.get("mode") != "execute":
        raise ValueError("The lab must be initialized with mode=execute")
    if ctx.experiment.get("status") == "completed":
        return {
            "lab": str(ctx.lab_dir),
            "report": str(ctx.lab_dir / "reports" / "report-standalone.html"),
            "image_reserved": reserved_count(ctx, "image"),
            "stop_reason": ctx.experiment.get("stop_reason"),
        }
    records = role_records(ctx)
    prototype_coverage = validate_prototype_sources(ctx, records)
    interrupted = close_unfinished_calls(ctx, "recovery batch started after interrupted corrected continuation")
    remaining_images = IMAGE_BUDGET - reserved_count(ctx, "image")
    recovery_items = make_recovery_items(records)
    if remaining_images < len(recovery_items):
        raise RuntimeError(
            f"recovery batch requires {len(recovery_items)} remaining image calls, but only {remaining_images} remain"
        )
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "audit_intervention",
            "action": "fixed_recovery_evidence_batch",
            "reason": "Compiler comparison did not reach the minimum valid audit coverage after provider transport failures; the final three calls are evidence-only.",
            "strategy": "template_plus_style / style_explicit",
            "interrupted_call_ids": interrupted,
            "at": utc_now(),
        },
    )
    write_json(
        ctx.lab_dir / "experiment_plan_recovery.json",
        {
            "schema_version": 1,
            "purpose": "evidence_only_recovery_batch",
            "strategy": "template_plus_style / style_explicit",
            "not_a_promotion_decision": True,
            "prototype_coverage": prototype_coverage,
            "image_calls": len(recovery_items),
            "reason": "The preceding compiler comparison lacked minimum valid audit coverage because provider responses were not JSON objects.",
            "interrupted_call_ids": interrupted,
        },
    )
    settings = load_settings(str(ctx.config_path))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    await run_round(
        ctx,
        recovery_items,
        records,
        prompt_provider,
        image_provider,
        audit_provider,
        media_store,
    )
    append_decision(
        ctx,
        round_id="r04-recovery-style-explicit",
        selected="style_explicit",
        table={
            "style_explicit": stats(
                [row for row in sample_rows(ctx).values() if row.get("round_id") == "r04-recovery-style-explicit"]
            )
        },
        reason="这是审核证据恢复批次，不是编译器胜者选择；固定白模+主视觉与显式画风提示词以完成剩余预算。",
        decision_status="evidence_only",
    )
    winners = {
        "reference_policy_original": "invalid_insufficient_audit_coverage",
        "reference_policy_corrected": "template_plus_style",
        "compiler_corrected": "inconclusive_insufficient_audit_coverage",
        "recovery_evidence_strategy": "template_plus_style / style_explicit",
    }
    stop_reason = (
        f"{reserved_count(ctx, 'image')}-image reservations recorded; remaining image calls were not started "
        "because the Gemini prompt provider returned a billing_error (insufficient balance); "
        "compiler comparison inconclusive, recovery batch retained as evidence only"
    )
    exp = load_experiment(ctx.lab_dir)
    exp.update(
        {
            "status": "completed",
            "current_phase": "report",
            "stop_reason": stop_reason,
            "winners": winners,
            "intervention": "audit-corrected continuation plus evidence-only recovery",
            "prototype_contract": {
                "source_project": PROJECT_ID,
                "coverage_path": "contracts/prototype-coverage.json",
                "validated": bool(prototype_coverage.get("validated")),
                "role_count": len(prototype_coverage.get("roles", [])),
            },
            "completed_at": utc_now(),
        }
    )
    save_experiment(ctx.lab_dir, exp)
    write_final_summary(ctx, stop_reason, winners)
    report = build_report(ctx)
    return {
        "lab": str(ctx.lab_dir),
        "report": str(report),
        "image_reserved": reserved_count(ctx, "image"),
        "image_success": sum(1 for row in sample_rows(ctx).values() if row.get("status") == "success"),
        "winners": winners,
        "stop_reason": stop_reason,
    }


async def run_experiment(ctx: base.LabContext, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("--confirm-paid-calls is required")
    if ctx.experiment.get("mode") != "execute":
        raise ValueError("The lab must be initialized with mode=execute")
    records = role_records(ctx)
    validate_prototype_sources(ctx, records)
    settings = load_settings(str(ctx.config_path))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)

    winners: dict[str, Any] = {}
    stop_reason = "completed all planned rounds"

    await run_round(ctx, make_round1(records), records, prompt_provider, image_provider, audit_provider, media_store)
    summaries = list(sample_rows(ctx).values())
    policy_winner, policy_table = choose_variant(
        summaries,
        "policy_id",
        ("template_only", "template_plus_style"),
        prefer="template_plus_style",
    )
    append_decision(
        ctx,
        round_id="r01-reference-policy",
        selected=policy_winner,
        table=policy_table,
        reason="以通过率优先、综合均值其次，画风和比例等权；同分优先保留主视觉参考。",
    )
    winners["reference_policy"] = policy_winner

    if policy_winner is None:
        stop_reason = "首轮没有可评分样本，无法安全选择后续参考策略"
    else:
        await run_round(
            ctx,
            make_round_items("r02-compiler", policy_winner, ("minimal", "style_explicit"), records),
            records,
            prompt_provider,
            image_provider,
            audit_provider,
            media_store,
        )
        summaries = list(sample_rows(ctx).values())
        compiler_winner, compiler_table = choose_variant(
            [row for row in summaries if row.get("round_id") == "r02-compiler"],
            "compiler_id",
            ("minimal", "style_explicit"),
            prefer="style_explicit",
        )
        append_decision(
            ctx,
            round_id="r02-compiler",
            selected=compiler_winner,
            table=compiler_table,
            reason="在冻结参考策略下比较 Gemini 的最小提示词与显式画风编译。",
        )
        winners["compiler_round2"] = compiler_winner

        if compiler_winner is None:
            stop_reason = "第二轮没有可评分样本，无法安全选择后续提示词"
        else:
            await run_round(
                ctx,
                make_round_items("r03-prompt-focus", policy_winner, ("proportion_first", "balanced"), records),
                records,
                prompt_provider,
                image_provider,
                audit_provider,
                media_store,
            )
            summaries = list(sample_rows(ctx).values())
            round3_rows = [row for row in summaries if row.get("round_id") == "r03-prompt-focus"]
            refinement_winner, refinement_table = choose_variant(
                round3_rows,
                "compiler_id",
                ("proportion_first", "balanced"),
                prefer="balanced",
            )
            append_decision(
                ctx,
                round_id="r03-prompt-focus",
                selected=refinement_winner,
                table=refinement_table,
                reason="在冻结参考策略下比较比例优先与平衡优先提示词；两项 rubric 仍等权。",
            )
            winners["compiler_round3"] = refinement_winner
            champion_compiler = refinement_winner or compiler_winner
            await run_round(
                ctx,
                make_round4(records, policy_winner, champion_compiler),
                records,
                prompt_provider,
                image_provider,
                audit_provider,
                media_store,
            )
            winners["final_checkpoint_compiler"] = champion_compiler

    exp = load_experiment(ctx.lab_dir)
    exp["status"] = "completed"
    exp["current_phase"] = "report"
    exp["stop_reason"] = stop_reason
    exp["winners"] = winners
    exp["completed_at"] = utc_now()
    save_experiment(ctx.lab_dir, exp)
    write_final_summary(ctx, stop_reason, winners)
    report = build_report(ctx)
    return {
        "lab": str(ctx.lab_dir),
        "report": str(report),
        "image_reserved": reserved_count(ctx, "image"),
        "image_success": sum(1 for row in sample_rows(ctx).values() if row.get("status") == "success"),
        "winners": winners,
        "stop_reason": stop_reason,
    }


def init_lab(lab_dir: Path) -> dict[str, Any]:
    if lab_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing lab: {lab_dir}")
    runtime_config = ROOT / ".tmp" / "roleboard-gen-runtime-gpt-image-2-guan.yaml"
    make_runtime_config(runtime_config)
    command = [
        sys.executable,
        str(INIT_LAB),
        "--repo-root",
        str(ROOT),
        "--config",
        str(runtime_config),
        "--project-id",
        PROJECT_ID,
        "--project-dir",
        str(ROOT / "outputs" / PROJECT_ID),
        "--key-vision",
        str(KEY_VISION),
        "--key-vision-selection-basis",
        "user_confirmed",
        "--lab-dir",
        str(lab_dir),
        "--mode",
        "execute",
        "--budget-image",
        str(IMAGE_BUDGET),
        "--budget-edit",
        "0",
        "--budget-gemini",
        str(GEMINI_BUDGET),
        "--budget-audit",
        str(AUDIT_BUDGET),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    ctx = base.load_lab(lab_dir)
    freeze_contracts(ctx)
    prototype_coverage = validate_prototype_sources(ctx, role_records(ctx))
    exp = load_experiment(lab_dir)
    exp.update(
        {
            "status": "ready_for_execution",
            "current_phase": "roleboard_gen",
            "experiment_question": "在 saodi 已提取角色原型的硬约束下，对比白模单参考与白模+主视觉双参考，并迭代 Gemini 角色提示词，使角色画风匹配主视觉且比例协调。",
            "prototype_contract": {
                "source_project": PROJECT_ID,
                "coverage_path": "contracts/prototype-coverage.json",
                "validated": bool(prototype_coverage.get("validated")),
                "role_count": len(prototype_coverage.get("roles", [])),
            },
            "reference_responsibilities": {
                "spatial_template": "只负责三视图布局、尺度、基线、间距和中性站姿。",
                "key_vision_style": "只负责角色画风、渲染媒介、材质、光线、色彩和完成度。",
            },
            "confirmed_key_vision_attachment": {
                "path": str(CLIPBOARD_CONFIRMATION),
                "sha256": sha256(CLIPBOARD_CONFIRMATION) if CLIPBOARD_CONFIRMATION.is_file() else None,
                "matches_locked_key_vision": CLIPBOARD_CONFIRMATION.is_file()
                and sha256(CLIPBOARD_CONFIRMATION) == sha256(KEY_VISION),
            },
            "budget_policy": {
                "image_calls_hard_ceiling": IMAGE_BUDGET,
                "gemini_prompt_calls_ceiling": GEMINI_BUDGET,
                "gemini_audit_calls_ceiling": AUDIT_BUDGET,
            },
        }
    )
    save_experiment(lab_dir, exp)
    write_json(
        lab_dir / "experiment_plan.json",
        {
            "rounds": [
                {"round": "r01-reference-policy", "image_calls": 6, "description": "三角色各做白模单参考和白模+主视觉双参考"},
                {"round": "r02-compiler", "image_calls": 6, "description": "仅在每个参考候选至少两张有效审计时冻结胜出策略，比较最小编译与显式画风编译"},
                {"round": "r03-prompt-focus", "image_calls": 6, "description": "比较比例优先与平衡优先提示词"},
                {"round": "r04-final-checkpoint", "image_calls": 2, "description": "对两个角色做新鲜 Gemini→图片→审查 checkpoint"},
            ],
            "total_image_calls": IMAGE_BUDGET,
            "audit_dimensions": ["style_match_score", "proportion_coordination_score"],
        },
    )
    return {"lab": str(lab_dir), "mode": "execute", "status": "ready_for_execution"}


def verify_report(ctx: base.LabContext) -> dict[str, Any]:
    report = ctx.lab_dir / "reports" / "report-standalone.html"
    if not report.is_file():
        raise FileNotFoundError(report)
    prototype_path = ctx.lab_dir / "contracts" / "prototype-coverage.json"
    if not prototype_path.is_file() or not read_json(prototype_path).get("validated"):
        raise AssertionError("prototype coverage is missing or not validated")
    document = report.read_text(encoding="utf-8")
    rows = list(sample_rows(ctx).values())
    success = sum(1 for row in rows if row.get("status") == "success" and row.get("image_path"))
    image_rows = sum(
        1
        for row in rows
        if row.get("image_path") and (ctx.lab_dir / str(row["image_path"])).is_file()
    )
    embedded = document.count("data:image/webp;base64,")
    contact_count = 1 if (ctx.lab_dir / "contact_sheets" / "blind" / "roleboard-all.png").is_file() else 0
    if embedded != image_rows + contact_count:
        raise AssertionError(
            f"embedded image count mismatch: embedded={embedded}, sample_images={image_rows}, contact={contact_count}"
        )
    if 'src="http' in document or 'href="http' in document:
        raise AssertionError("report contains external asset links")
    image_reserved = reserved_count(ctx, "image")
    if image_reserved > IMAGE_BUDGET:
        raise AssertionError(f"image budget exceeded: {image_reserved} > {IMAGE_BUDGET}")
    return {
        "report": str(report),
        "bytes": report.stat().st_size,
        "embedded_images": embedded,
        "sample_success": success,
        "sample_images": image_rows,
        "image_reserved": image_reserved,
    }


def inspect_lab(ctx: base.LabContext) -> dict[str, Any]:
    settings = load_settings(str(ctx.config_path))
    router = ProviderRouter(settings)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    image_binding = getattr(image_provider, "model_binding", None)
    return {
        "lab": str(ctx.lab_dir),
        "mode": ctx.experiment.get("mode"),
        "status": ctx.experiment.get("status"),
        "image_node_model_id": getattr(image_binding, "model_id", None),
        "image_provider_model": getattr(image_provider, "model", None),
        "prompt_provider_model": getattr(prompt_provider, "model", None),
        "audit_provider_model": getattr(audit_provider, "model", None),
        "spatial_template_sha256": ctx.spatial_template_hash,
        "key_vision_sha256": ctx.key_vision_hash,
        "contracts_frozen": bool(base.frozen_contracts(ctx)),
        "image_budget": IMAGE_BUDGET,
        "gemini_prompt_budget": GEMINI_BUDGET,
        "gemini_audit_budget": AUDIT_BUDGET,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gemini → gpt-image-2-guan → Gemini roleboard_gen iteration smoke harness"
    )
    parser.add_argument("--lab-dir", required=True, help="Experiment directory under repository .tmp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    run = sub.add_parser("run")
    run.add_argument("--confirm-paid-calls", action="store_true")
    resume = sub.add_parser("resume-corrected")
    resume.add_argument("--confirm-paid-calls", action="store_true")
    recovery = sub.add_parser("recovery")
    recovery.add_argument("--confirm-paid-calls", action="store_true")
    sub.add_parser("report")
    sub.add_parser("verify")
    sub.add_parser("inspect")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lab_dir = Path(args.lab_dir).resolve()
    if args.command == "init":
        result = init_lab(lab_dir)
    else:
        ctx = base.load_lab(lab_dir)
        if args.command == "run":
            result = asyncio.run(run_experiment(ctx, confirmed=bool(args.confirm_paid_calls)))
        elif args.command == "resume-corrected":
            result = asyncio.run(run_corrected_resume(ctx, confirmed=bool(args.confirm_paid_calls)))
        elif args.command == "recovery":
            result = asyncio.run(run_recovery(ctx, confirmed=bool(args.confirm_paid_calls)))
        elif args.command == "report":
            result = {"report": str(build_report(ctx))}
        elif args.command == "verify":
            result = verify_report(ctx)
        elif args.command == "inspect":
            result = inspect_lab(ctx)
        else:
            raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
