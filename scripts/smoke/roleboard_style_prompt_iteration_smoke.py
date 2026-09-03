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
import statistics
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
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
from autodrama.core.schemas import (  # noqa: E402
    Role,
    RoleAppearance,
    RoleExtractItem,
    RoleboardPromptModelOutput,
)
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402
from autodrama.services.role_service import RoleService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import RoleAppearanceGenerationBase  # noqa: E402

import roleboard_gen_iteration_smoke as legacy  # noqa: E402
import roleboard_node_evolution_smoke as lab_base  # noqa: E402


PROJECT_ID = "saodi_0803"
ACTIVE_CONFIG = ROOT / "saodi.yaml"
MODEL_CATALOG = ROOT / "model_catalog.yaml.example"
PROJECT_DIR = ROOT / "outputs" / PROJECT_ID
KEY_VISION = PROJECT_DIR / "assets" / "images" / "key_visions" / "key_vision_original.png"
SPATIAL_TEMPLATE = ROOT / ".assets" / "image_templates" / "roleboard_template.png"
ROLE_FINALIZE = PROJECT_DIR / "assets" / "json" / "nodes" / "role_finalize.json"
ROLEBOARD_PROMPT_BASELINE = PROJECT_DIR / "assets" / "json" / "nodes" / "roleboard_prompt.json"
ROLEBOARD_GENERATION_BASELINE = PROJECT_DIR / "assets" / "json" / "nodes" / "roleboard_image_generation.json"
RUBRIC_SOURCE = (
    ROOT
    / ".agents"
    / "skills"
    / "iterate-roleboard-nodes"
    / "assets"
    / "roleboard-keyvision-person-design-rubric-v1.json"
)
INIT_LAB = (
    ROOT
    / ".agents"
    / "skills"
    / "iterate-roleboard-nodes"
    / "scripts"
    / "init_lab.py"
)

ROUND_COUNT = 10
CANDIDATES_PER_ROUND = 3
BASE_ROLE_COUNT = 3
PROMPT_BUDGET = ROUND_COUNT * CANDIDATES_PER_ROUND * BASE_ROLE_COUNT
IMAGE_BUDGET = PROMPT_BUDGET
AUDIT_BUDGET = PROMPT_BUDGET + BASE_ROLE_COUNT
PROMPT_TEMPERATURE = 0.45
AUDIT_TEMPERATURE = 0.10
MAX_CONCURRENCY = 3
CONTACT_SEED = 20260822

ROLE_SHORT = {"叶凡": "yf", "柳菡烟": "lhy", "李德海": "ldh"}
FORBIDDEN_TEMPLATE_TOKENS = {
    "identity_invariants",
    "wardrobe",
    "roleboard_prompt",
    "design_notes",
    "episode_keys",
    "asset_role",
    "reference_asset_name",
    "project_id",
    "role_叶凡",
    "role_柳菡烟",
    "role_李德海",
}
FORBIDDEN_ROLE_NAMES = {"叶凡", "柳菡烟", "李德海"}


class SimilarityAssessment(BaseModel):
    dimension_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=10)
    evidence: str = Field(min_length=1)


class SimilarityGate(BaseModel):
    gate_id: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)


class SimilarityAuditOutput(BaseModel):
    assessments: list[SimilarityAssessment]
    gates: list[SimilarityGate]
    overall_summary: str = Field(min_length=1)


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


def write_json_immutable(path: Path, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"Refusing to mutate immutable artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return str(value)


def role_short(role_name: str) -> str:
    return ROLE_SHORT.get(role_name, hashlib.sha256(role_name.encode("utf-8")).hexdigest()[:8])


def round_id(round_number: int) -> str:
    if not 1 <= round_number <= ROUND_COUNT:
        raise ValueError(f"round must be in 1..{ROUND_COUNT}")
    return f"r{round_number:02d}"


def load_experiment(lab_dir: Path) -> dict[str, Any]:
    return read_json(lab_dir / "experiment.json")


def save_experiment(lab_dir: Path, experiment: dict[str, Any]) -> None:
    write_json(lab_dir / "experiment.json", experiment)


def make_runtime_config(path: Path) -> Path:
    raw = yaml.safe_load(ACTIVE_CONFIG.read_text(encoding="utf-8")) or {}
    raw["apikeys_file"] = str(ROOT / "apikeys.yaml")
    raw["model_catalog_file"] = str(MODEL_CATALOG)
    raw.setdefault("project", {})["id"] = PROJECT_ID
    raw["project"]["script_chapters_dir"] = str(ROOT / "inputs" / "saodi_chapters")
    raw.setdefault("output", {})["root_dir"] = str(ROOT / "outputs")
    raw.setdefault("budget", {})["max_image_count"] = max(
        int((raw.get("budget") or {}).get("max_image_count") or 0), IMAGE_BUDGET
    )
    raw["budget"]["max_text_calls"] = max(
        int((raw.get("budget") or {}).get("max_text_calls") or 0), PROMPT_BUDGET + AUDIT_BUDGET
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return path


def active_style_prompt() -> str:
    raw = yaml.safe_load(ACTIVE_CONFIG.read_text(encoding="utf-8")) or {}
    return str(((raw.get("generation") or {}).get("roleboard_style_prompt") or "")).strip()


def source_production_hashes() -> dict[str, str]:
    paths = {
        "active_config": ACTIVE_CONFIG,
        "role_finalize": ROLE_FINALIZE,
        "roleboard_prompt": ROLEBOARD_PROMPT_BASELINE,
        "roleboard_image_generation": ROLEBOARD_GENERATION_BASELINE,
        "key_vision": KEY_VISION,
        "spatial_template": SPATIAL_TEMPLATE,
        "roleboard_叶凡": PROJECT_DIR / "assets" / "images" / "roles" / "role_叶凡_appearance_base_roleboard.png",
        "roleboard_柳菡烟": PROJECT_DIR / "assets" / "images" / "roles" / "role_柳菡烟_appearance_base_roleboard.png",
        "roleboard_李德海": PROJECT_DIR / "assets" / "images" / "roles" / "role_李德海_appearance_base_roleboard.png",
        "role_service": ROOT / "autodrama" / "src" / "autodrama" / "services" / "role_service.py",
        "static_asset_nodes": ROOT / "autodrama" / "src" / "autodrama" / "workflows" / "nodes" / "static_asset_nodes.py",
        "similarity_audit_template": ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "roleboard_style_similarity_audit" / "default.md",
    }
    for template in sorted(
        (ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "roleboard_prompt").glob("*.md")
    ):
        paths[f"roleboard_prompt_template:{template.name}"] = template
    return {name: sha256(path) for name, path in paths.items()}


def base_role_inputs(ctx: lab_base.LabContext) -> list[dict[str, Any]]:
    payload = read_json(ctx.lab_dir / "inputs" / "role_finalize.json")
    rows: list[dict[str, Any]] = []
    for role_payload in payload.get("final_roles", []):
        role = RoleExtractItem.model_validate(role_payload)
        for appearance in role.appearance_assets:
            if str(appearance.asset_role) != "base":
                continue
            appearance_payload = appearance.model_dump(mode="json")
            appearance_payload["appearance_name"] = appearance.name
            rows.append(
                {
                    "role": role,
                    "appearance": appearance_payload,
                    "role_name": role.name,
                    "appearance_name": appearance.name,
                    "role_id": f"role_{role.name}",
                    "appearance_id": f"role_{role.name}_appearance_{appearance.name}",
                }
            )
    if len(rows) != BASE_ROLE_COUNT:
        raise ValueError(f"Expected exactly {BASE_ROLE_COUNT} base roles, found {len(rows)}")
    return rows


def prompt_variant(config_path: Path) -> str:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    params = (((raw.get("nodes") or {}).get("roleboard_image_generation") or {}).get("params") or {})
    return str(params.get("roleboard_prompt_template") or "default").strip() or "default"


def image_controls(config_path: Path) -> dict[str, str]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    node = ((raw.get("nodes") or {}).get("roleboard_image_generation") or {})
    route = str(node.get("model") or "")
    if ":" not in route:
        raise ValueError("roleboard_image_generation model route must use provider:model")
    provider_name, model_name = route.split(":", 1)
    params = node.get("params") or {}
    provider = ((raw.get("providers") or {}).get(provider_name) or {})
    options = provider.get("options") or {}
    return {
        "route": route,
        "provider": provider_name,
        "model": model_name,
        "size": str(params.get("size") or options.get("roleboard_size") or options.get("size") or "auto"),
        "quality": str(params.get("quality") or options.get("roleboard_quality") or options.get("quality") or "auto"),
        "prompt_template": prompt_variant(config_path),
    }


def copy_production_baseline(ctx: lab_base.LabContext) -> dict[str, Any]:
    generation = read_json(ROLEBOARD_GENERATION_BASELINE)
    baseline_dir = ctx.lab_dir / "inputs" / "production-baseline"
    image_dir = baseline_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for item in generation.get("generated_assets", []):
        source = PROJECT_DIR / str(item.get("asset_path") or "")
        if not source.is_file():
            raise FileNotFoundError(source)
        target = image_dir / source.name
        shutil.copy2(source, target)
        rows.append(
            {
                "role_name": str(item.get("name") or "").split("/", 1)[0],
                "asset_id": item.get("asset_id"),
                "source_path": str(source),
                "snapshot_path": str(target),
                "sha256": sha256(target),
                "prompt": item.get("prompt") or "",
                "provider": item.get("provider"),
                "model": item.get("model"),
            }
        )
    if len(rows) != BASE_ROLE_COUNT:
        raise ValueError(f"Expected {BASE_ROLE_COUNT} baseline roleboards, found {len(rows)}")
    manifest = {
        "schema_version": 1,
        "style_prompt": active_style_prompt(),
        "style_prompt_sha256": hashlib.sha256(active_style_prompt().encode("utf-8")).hexdigest(),
        "images": rows,
        "production_hashes": source_production_hashes(),
    }
    write_json_immutable(baseline_dir / "manifest.json", manifest)
    return manifest


def init_lab(lab_dir: Path) -> dict[str, Any]:
    if lab_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing lab: {lab_dir}")
    for required in (
        ACTIVE_CONFIG,
        MODEL_CATALOG,
        KEY_VISION,
        SPATIAL_TEMPLATE,
        ROLE_FINALIZE,
        ROLEBOARD_PROMPT_BASELINE,
        ROLEBOARD_GENERATION_BASELINE,
        RUBRIC_SOURCE,
        INIT_LAB,
    ):
        if not required.is_file():
            raise FileNotFoundError(required)
    runtime_config = ROOT / ".tmp" / "roleboard-style-prompt-runtime.yaml"
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
        str(PROJECT_DIR),
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
        str(PROMPT_BUDGET),
        "--budget-audit",
        str(AUDIT_BUDGET),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    ctx = lab_base.load_lab(lab_dir)
    legacy.freeze_contracts(ctx)
    legacy.validate_prototype_sources(ctx, legacy.role_records(ctx))
    baseline = copy_production_baseline(ctx)
    rubric_target = ctx.lab_dir / "rubrics" / RUBRIC_SOURCE.name
    rubric_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUBRIC_SOURCE, rubric_target)
    baseline_candidate = {
        "candidate_id": "baseline-production-style-prompt",
        "round": "baseline",
        "parent_id": None,
        "changed_family": "production-incumbent",
        "hypothesis": "现有生产 roleboard_style_prompt 作为零调用文本基线。",
        "roleboard_style_prompt": active_style_prompt(),
        "sha256": hashlib.sha256(active_style_prompt().encode("utf-8")).hexdigest(),
        "created_at": utc_now(),
    }
    write_json_immutable(
        ctx.lab_dir / "candidates" / "style_prompt" / "baseline-production-style-prompt.json",
        baseline_candidate,
    )
    controls = image_controls(ctx.config_path)
    experiment = load_experiment(ctx.lab_dir)
    experiment.update(
        {
            "status": "ready_for_execution",
            "current_phase": "style_prompt_iteration",
            "experiment_question": (
                "只改变 roleboard_style_prompt，最大化角色板与已确认主视觉人物的共享设计和渲染语言相似度，"
                "同时禁止复制主视觉中的具体人物身份。"
            ),
            "authorized_variable": "generation.roleboard_style_prompt",
            "round_count": ROUND_COUNT,
            "candidates_per_round": CANDIDATES_PER_ROUND,
            "roles_per_candidate": BASE_ROLE_COUNT,
            "current_rubric": {
                "path": str(rubric_target.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "sha256": sha256(rubric_target),
                "revision": read_json(rubric_target).get("revision"),
            },
            "frozen_controls": {
                "production_hashes": baseline["production_hashes"],
                "key_vision_sha256": ctx.key_vision_hash,
                "spatial_template_sha256": ctx.spatial_template_hash,
                "reference_order": ["spatial_template", "key_vision_style"],
                "prompt_model_route": "aibox:gemini-3.6-flash",
                "prompt_temperature_actual": PROMPT_TEMPERATURE,
                "image": controls,
                "audit_model_route": "aibox:gemini-3.6-flash",
                "audit_temperature": AUDIT_TEMPERATURE,
                "roleboard_prompt_template_hashes": {
                    path.name: sha256(path)
                    for path in sorted(
                        (ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "roleboard_prompt").glob("*.md")
                    )
                },
                "image_wrapper_sha256": sha256(
                    ROOT / "autodrama" / "src" / "autodrama" / "workflows" / "nodes" / "static_asset_nodes.py"
                ),
            },
            "budget_policy": {
                "gemini_prompt_calls": PROMPT_BUDGET,
                "image_calls": IMAGE_BUDGET,
                "audit_calls": AUDIT_BUDGET,
                "transport_failures_consume_budget": True,
            },
            "completed_rounds": [],
            "created_for_user_request_at": utc_now(),
        }
    )
    save_experiment(ctx.lab_dir, experiment)
    write_json_immutable(
        ctx.lab_dir / "PROTOCOL-STYLE-PROMPT.md.json",
        {
            "mode": "execute",
            "single_variable": "roleboard_style_prompt",
            "rounds": ROUND_COUNT,
            "candidates_each_round": CANDIDATES_PER_ROUND,
            "roles_each_candidate": BASE_ROLE_COUNT,
            "image_calls": IMAGE_BUDGET,
            "selection_authority": "Codex supervised visual review; model audit is advisory evidence",
            "no_intermediate_image_editing": True,
        },
    )
    return inspect_lab(ctx)


def current_rubric(ctx: lab_base.LabContext) -> tuple[Path, dict[str, Any]]:
    experiment = load_experiment(ctx.lab_dir)
    info = experiment.get("current_rubric") or {}
    path = ctx.lab_dir / str(info.get("path") or "")
    if not path.is_file():
        raise FileNotFoundError(path)
    if sha256(path) != str(info.get("sha256") or ""):
        raise ValueError("Current rubric hash drifted")
    return path, read_json(path)


def rubric_ids(rubric: dict[str, Any]) -> tuple[list[str], list[str]]:
    dimensions = [str(item["id"]) for item in rubric.get("dimensions", [])]
    gates = [str(item["id"]) for item in rubric.get("gates", [])]
    if not dimensions or len(dimensions) != len(set(dimensions)):
        raise ValueError("Rubric dimensions must be non-empty and unique")
    if not gates or len(gates) != len(set(gates)):
        raise ValueError("Rubric gates must be non-empty and unique")
    if sum(float(item.get("weight") or 0) for item in rubric["dimensions"]) <= 0:
        raise ValueError("Rubric weights must sum above zero")
    return dimensions, gates


def render_rubric_text(rubric: dict[str, Any]) -> tuple[str, str]:
    dimension_sentences = []
    for item in rubric.get("dimensions", []):
        dimension_sentences.append(
            f"{item['id']}，权重 {item['weight']}：{str(item['question']).strip()}"
        )
    gate_sentences = []
    for item in rubric.get("gates", []):
        gate_sentences.append(f"{item['id']}：{str(item['description']).strip()}")
    return "\n\n".join(dimension_sentences), "\n\n".join(gate_sentences)


def validate_style_prompt(prompt: str) -> None:
    text = str(prompt or "").strip()
    if len(text) < 120:
        raise ValueError("roleboard_style_prompt must contain at least 120 characters")
    if any(name in text for name in FORBIDDEN_ROLE_NAMES):
        raise ValueError("roleboard_style_prompt must remain role-agnostic")
    if any(token in text for token in FORBIDDEN_TEMPLATE_TOKENS):
        raise ValueError("roleboard_style_prompt contains a project-internal field or role identifier")
    if any(line.lstrip().startswith(("- ", "* ", "1. ", "2. ", "3. ")) for line in text.splitlines()):
        raise ValueError("roleboard_style_prompt must use direct prompt prose, not skill-style lists")


def load_candidate_file(path: Path, round_number: int) -> list[dict[str, Any]]:
    payload = read_json(path)
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or len(candidates) != CANDIDATES_PER_ROUND:
        raise ValueError(f"Candidate file must contain exactly {CANDIDATES_PER_ROUND} candidates")
    expected_round = round_id(round_number)
    prompts: set[str] = set()
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        candidate_id = str(item.get("candidate_id") or "").strip()
        prompt = str(item.get("roleboard_style_prompt") or "").strip()
        if not candidate_id.startswith(expected_round + "-"):
            raise ValueError(f"candidate_id must start with {expected_round}-: {candidate_id}")
        if candidate_id in ids or prompt in prompts:
            raise ValueError("Candidate IDs and prompt texts must be unique within the round")
        if not str(item.get("changed_family") or "").strip():
            raise ValueError(f"Candidate {candidate_id} lacks changed_family")
        if not str(item.get("hypothesis") or "").strip():
            raise ValueError(f"Candidate {candidate_id} lacks hypothesis")
        validate_style_prompt(prompt)
        ids.add(candidate_id)
        prompts.add(prompt)
        item.update(
            {
                "round": expected_round,
                "roleboard_style_prompt": prompt,
                "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "created_at": str(item.get("created_at") or utc_now()),
            }
        )
        normalized.append(item)
    return normalized


def freeze_candidates(ctx: lab_base.LabContext, round_number: int, candidates: list[dict[str, Any]]) -> None:
    directory = ctx.lab_dir / "candidates" / "style_prompt" / round_id(round_number)
    for candidate in candidates:
        write_json_immutable(directory / f"{candidate['candidate_id']}.json", candidate)


def candidate_for_id(ctx: lab_base.LabContext, candidate_id: str) -> dict[str, Any]:
    matches = list((ctx.lab_dir / "candidates" / "style_prompt").glob(f"**/{candidate_id}.json"))
    if len(matches) != 1:
        raise ValueError(f"Expected one candidate manifest for {candidate_id}, found {len(matches)}")
    return read_json(matches[0])


def batch_items(
    ctx: lab_base.LabContext,
    round_number: int,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    roles = base_role_inputs(ctx)
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        for role in roles:
            sample_id = f"{candidate['candidate_id']}-{role_short(role['role_name'])}"
            items.append(
                {
                    "sample_id": sample_id,
                    "round": round_id(round_number),
                    "candidate_id": candidate["candidate_id"],
                    "candidate_sha256": candidate["sha256"],
                    "role_name": role["role_name"],
                    "appearance_name": role["appearance_name"],
                    "role_id": role["role_id"],
                    "appearance_id": role["appearance_id"],
                    "split": "exploration",
                    "replicate": 1,
                    "expected_output": f"images/exploration/{round_id(round_number)}/{sample_id}.png",
                }
            )
    if len(items) != CANDIDATES_PER_ROUND * BASE_ROLE_COUNT:
        raise AssertionError("Unexpected round sample count")
    return items


def write_batch_plan(
    ctx: lab_base.LabContext,
    round_number: int,
    candidates: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> None:
    controls = image_controls(ctx.config_path)
    rubric_path, rubric = current_rubric(ctx)
    write_json_immutable(
        ctx.lab_dir / "batches" / f"{round_id(round_number)}.json",
        {
            "schema_version": 1,
            "round": round_id(round_number),
            "authorized_variable": "roleboard_style_prompt",
            "candidate_ids": [candidate["candidate_id"] for candidate in candidates],
            "candidate_hashes": {candidate["candidate_id"]: candidate["sha256"] for candidate in candidates},
            "frozen_controls": {
                "role_count": BASE_ROLE_COUNT,
                "prompt_temperature": PROMPT_TEMPERATURE,
                "image": controls,
                "reference_order": ["spatial_template", "key_vision_style"],
                "key_vision_sha256": ctx.key_vision_hash,
                "spatial_template_sha256": ctx.spatial_template_hash,
                "audit_temperature": AUDIT_TEMPERATURE,
                "rubric_path": str(rubric_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "rubric_revision": rubric.get("revision"),
                "rubric_sha256": sha256(rubric_path),
            },
            "items": items,
            "created_at": utc_now(),
        },
    )


def reserved_count(ctx: lab_base.LabContext, kind: str) -> int:
    return sum(
        int(row.get("count") or 1)
        for row in read_jsonl(ctx.lab_dir / "ledger.jsonl")
        if row.get("event") == "reserve" and row.get("kind") == kind
    )


def reserve_call(
    ctx: lab_base.LabContext,
    *,
    kind: str,
    stage: str,
    candidate_id: str,
    sample_id: str,
    round_name: str,
) -> str:
    limits = {"gemini": PROMPT_BUDGET, "image": IMAGE_BUDGET, "audit": AUDIT_BUDGET}
    if reserved_count(ctx, kind) >= limits[kind]:
        raise RuntimeError(f"{kind} call ceiling reached before {sample_id}")
    call_id = f"{kind}-{uuid.uuid4().hex}"
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "reserve",
            "call_id": call_id,
            "kind": kind,
            "stage": stage,
            "phase": "style_prompt_iteration",
            "round": round_name,
            "candidate_id": candidate_id,
            "sample_id": sample_id,
            "count": 1,
            "at": utc_now(),
        },
    )
    return call_id


def complete_call(
    ctx: lab_base.LabContext,
    call_id: str,
    *,
    status: str,
    payload: dict[str, Any],
) -> None:
    append_jsonl(
        ctx.lab_dir / "ledger.jsonl",
        {
            "event": "complete",
            "call_id": call_id,
            "status": status,
            "at": utc_now(),
            **safe_json(payload),
        },
    )


def failure_status(exc: Exception, stage: str) -> str:
    if isinstance(exc, ProviderAuthError):
        return "provider_rejection"
    if isinstance(exc, (ProviderBadResponseError, OSError, TimeoutError)):
        message = str(exc).lower()
        if any(token in message for token in ("safety", "policy", "validation", "invalid prompt", "billing")):
            return "provider_rejection"
        return "transport_failure"
    if stage in {"roleboard_prompt", "roleboard_style_similarity_audit"}:
        return "schema_failure"
    return "provider_rejection"


def latest_samples(ctx: lab_base.LabContext) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ctx.lab_dir / "results.jsonl"):
        if row.get("kind") == "sample_summary" and row.get("sample_id"):
            latest[str(row["sample_id"])] = row
    return latest


def reference_records(ctx: lab_base.LabContext) -> list[dict[str, Any]]:
    return [
        {
            "index": 1,
            "logical_role": "spatial_template",
            "path": str(ctx.spatial_template_path),
            "sha256": ctx.spatial_template_hash,
            "reference_role": "spatial_template",
            "identity_transfer_allowed": False,
        },
        {
            "index": 2,
            "logical_role": "key_vision_style",
            "path": str(ctx.key_vision_path),
            "sha256": ctx.key_vision_hash,
            "reference_role": "key_vision_style",
            "identity_transfer_allowed": False,
        },
    ]


def asset_ref(record: dict[str, Any]) -> AssetRef:
    return AssetRef(
        id=f"{record['logical_role']}-{record.get('index', 0)}",
        type="image",
        path=str(record["path"]),
        metadata={
            "reference_role": record.get("reference_role") or record.get("logical_role"),
            "reference_index": record.get("index"),
            "sha256": record.get("sha256"),
            "mime_type": mimetypes.guess_type(str(record["path"]))[0] or "image/png",
            "identity_transfer_allowed": bool(record.get("identity_transfer_allowed")),
        },
    )


def role_contract_text(role_input: dict[str, Any]) -> str:
    return RoleService.roleboard_character_description(
        role_input["role"],
        role_input["appearance"],
    )


def render_roleboard_prompt_request(
    prompts: PromptStore,
    config_path: Path,
    role_input: dict[str, Any],
    style_prompt: str,
) -> str:
    return prompts.render(
        "roleboard_prompt",
        variant=prompt_variant(config_path),
        roleboard_character_description=role_contract_text(role_input),
        roleboard_style_prompt=style_prompt,
        roleboard_view_requirement=RoleService.roleboard_view_requirement(),
    )


def render_image_prompt(
    role_input: dict[str, Any],
    prompt_output: RoleboardPromptModelOutput,
    refs: list[AssetRef],
) -> str:
    appearance_payload = role_input["appearance"]
    appearance = RoleAppearance(
        id=role_input["appearance_id"],
        role_id=role_input["role_id"],
        name=role_input["appearance_name"],
        asset_role="base",
        identity_invariants=list(appearance_payload.get("identity_invariants") or []),
        wardrobe=list(appearance_payload.get("wardrobe") or []),
        time_period=appearance_payload.get("time_period"),
        age_band=appearance_payload.get("age_band"),
        roleboard_prompt=prompt_output.roleboard_prompt,
    )
    role = Role(
        id=role_input["role_id"],
        name=role_input["role_name"],
        intro=str(role_input["role"].brief or role_input["role_name"]),
        role_tier=role_input["role"].role_tier,
        has_dialogue=role_input["role"].has_dialogue,
        visual_reuse_required=role_input["role"].visual_reuse_required,
        appearances={appearance.name: appearance},
    )
    node = object.__new__(RoleAppearanceGenerationBase)
    return node.roleboard_prompt_for_generation(role=role, appearance=appearance, refs=refs)


def render_similarity_audit_request(
    prompts: PromptStore,
    role_input: dict[str, Any],
    rubric: dict[str, Any],
) -> str:
    rubric_text, gate_text = render_rubric_text(rubric)
    return prompts.render(
        "roleboard_style_similarity_audit",
        role_contract=role_contract_text(role_input),
        rubric_text=rubric_text,
        gate_text=gate_text,
    )


def normalize_audit(
    output: SimilarityAuditOutput,
    rubric: dict[str, Any],
) -> dict[str, Any]:
    dimension_ids, gate_ids = rubric_ids(rubric)
    assessments = {item.dimension_id: item for item in output.assessments}
    gates = {item.gate_id: item for item in output.gates}
    if len(assessments) != len(output.assessments) or set(assessments) != set(dimension_ids):
        raise ValueError(
            f"Audit dimension coverage mismatch: expected={dimension_ids}, actual={list(assessments)}"
        )
    if len(gates) != len(output.gates) or set(gates) != set(gate_ids):
        raise ValueError(f"Audit gate coverage mismatch: expected={gate_ids}, actual={list(gates)}")
    weighted = sum(
        float(assessments[str(item["id"])].score) * float(item["weight"])
        for item in rubric["dimensions"]
    )
    weight_sum = sum(float(item["weight"]) for item in rubric["dimensions"])
    raw_score = weighted / weight_sum
    minimum_score = min(float(item.score) for item in assessments.values())
    gate_pass = all(item.passed for item in gates.values())
    policy = rubric.get("policy") or {}
    approved = bool(
        gate_pass
        and raw_score >= float(policy.get("approval_threshold") or 8.0)
        and minimum_score >= float(policy.get("minimum_dimension_score") or 7.0)
    )
    return {
        "raw_score": raw_score,
        "minimum_dimension_score": minimum_score,
        "gate_pass": gate_pass,
        "approved": approved,
        "scores": {key: float(assessments[key].score) for key in dimension_ids},
        "evidence": {key: assessments[key].evidence for key in dimension_ids},
        "gates": {key: bool(gates[key].passed) for key in gate_ids},
        "gate_evidence": {key: gates[key].evidence for key in gate_ids},
        "overall_summary": output.overall_summary,
    }


async def run_candidate_sample(
    ctx: lab_base.LabContext,
    item: dict[str, Any],
    role_input: dict[str, Any],
    candidate: dict[str, Any],
    prompt_provider: Any,
    image_provider: Any,
    audit_provider: Any,
    media_store: MediaStore,
    prompts: PromptStore,
    controls: dict[str, str],
    rubric_path: Path,
    rubric: dict[str, Any],
    existing: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sample_id = str(item["sample_id"])
    if sample_id in existing:
        return existing[sample_id]
    refs_meta = reference_records(ctx)
    refs = [asset_ref(record) for record in refs_meta]
    prompt_request = render_roleboard_prompt_request(
        prompts,
        ctx.config_path,
        role_input,
        str(candidate["roleboard_style_prompt"]),
    )
    prompt_request_path = ctx.lab_dir / "responses" / "gemini" / f"{sample_id}.request.txt"
    prompt_request_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_request_path.write_text(prompt_request, encoding="utf-8", newline="\n")

    prompt_call = reserve_call(
        ctx,
        kind="gemini",
        stage="roleboard_prompt",
        candidate_id=item["candidate_id"],
        sample_id=sample_id,
        round_name=item["round"],
    )
    try:
        prompt_output = await prompt_provider.generate_json(
            prompt_request,
            RoleboardPromptModelOutput,
            temperature=PROMPT_TEMPERATURE,
            metadata={
                "node_name": "roleboard_prompt",
                "prompt_asset_type": "roleboard_style_prompt_iteration",
                "prompt_asset_name": sample_id,
                "sample_id": sample_id,
                "candidate_id": item["candidate_id"],
                "style_prompt_sha256": candidate["sha256"],
                "temperature": PROMPT_TEMPERATURE,
                "max_output_tokens": 4096,
            },
        )
        prompt_payload = prompt_output.model_dump(mode="json")
        prompt_response_path = ctx.lab_dir / "responses" / "gemini" / f"{sample_id}.json"
        write_json(
            prompt_response_path,
            {
                "stage": "roleboard_prompt",
                "sample_id": sample_id,
                "candidate_id": item["candidate_id"],
                "style_prompt": candidate["roleboard_style_prompt"],
                "style_prompt_sha256": candidate["sha256"],
                "input_prompt": prompt_request,
                "output": prompt_payload,
            },
        )
        complete_call(
            ctx,
            prompt_call,
            status="success",
            payload={
                "stage": "roleboard_prompt",
                "response_path": str(prompt_response_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            },
        )
    except Exception as exc:
        status = failure_status(exc, "roleboard_prompt")
        complete_call(
            ctx,
            prompt_call,
            status=status,
            payload={"stage": "roleboard_prompt", "error_type": type(exc).__name__, "error": str(exc)},
        )
        summary = {**item, "kind": "sample_summary", "status": status, "failure_stage": "roleboard_prompt", "error": str(exc), "at": utc_now()}
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        return summary

    image_prompt = render_image_prompt(role_input, prompt_output, refs)
    image_prompt_path = ctx.lab_dir / "responses" / "image" / f"{sample_id}.prompt.txt"
    image_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    image_prompt_path.write_text(image_prompt, encoding="utf-8", newline="\n")
    image_path = ctx.lab_dir / str(item["expected_output"])
    image_call = reserve_call(
        ctx,
        kind="image",
        stage="roleboard_image_generation",
        candidate_id=item["candidate_id"],
        sample_id=sample_id,
        round_name=item["round"],
    )
    try:
        image_result = await image_provider.generate_image(
            image_prompt,
            refs=refs,
            size=controls["size"],
            metadata={
                "node_name": "roleboard_image_generation",
                "prompt_asset_type": "roleboard_style_prompt_iteration",
                "prompt_asset_name": sample_id,
                "asset_id": sample_id,
                "candidate_id": item["candidate_id"],
                "style_prompt_sha256": candidate["sha256"],
                "size": controls["size"],
                "quality": controls["quality"],
                "max_reference_images": len(refs),
                "reference_roles": [record["reference_role"] for record in refs_meta],
            },
        )
        await media_store.write_first_generated_image(ctx.lab_dir, image_path, image_result)
        image_response_path = ctx.lab_dir / "responses" / "image" / f"{sample_id}.json"
        write_json(
            image_response_path,
            {
                "stage": "roleboard_image_generation",
                "sample_id": sample_id,
                "candidate_id": item["candidate_id"],
                "prompt": image_prompt,
                "ordered_references": refs_meta,
                "provider": getattr(image_result, "provider", None),
                "model": getattr(image_result, "model", None),
                "request_id": getattr(image_result, "request_id", None),
                "task_id": getattr(image_result, "task_id", None),
                "usage": safe_json(getattr(image_result, "usage", None)),
                "raw_response": safe_json(getattr(image_result, "raw_response", None)),
            },
        )
        complete_call(
            ctx,
            image_call,
            status="success",
            payload={
                "stage": "roleboard_image_generation",
                "output_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "output_sha256": sha256(image_path),
                "response_path": str(image_response_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "request_id": getattr(image_result, "request_id", None),
            },
        )
    except Exception as exc:
        status = failure_status(exc, "roleboard_image_generation")
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

    audit_request = render_similarity_audit_request(prompts, role_input, rubric)
    audit_request_path = ctx.lab_dir / "responses" / "audit" / f"{sample_id}.request.txt"
    audit_request_path.parent.mkdir(parents=True, exist_ok=True)
    audit_request_path.write_text(audit_request, encoding="utf-8", newline="\n")
    audit_refs_meta = [
        {
            "index": 1,
            "logical_role": "generated_roleboard",
            "path": str(image_path),
            "sha256": sha256(image_path),
            "reference_role": "audit_target",
        },
        {
            "index": 2,
            "logical_role": "key_vision_style",
            "path": str(ctx.key_vision_path),
            "sha256": ctx.key_vision_hash,
            "reference_role": "key_vision_style",
        },
        {
            "index": 3,
            "logical_role": "spatial_template",
            "path": str(ctx.spatial_template_path),
            "sha256": ctx.spatial_template_hash,
            "reference_role": "spatial_template",
        },
    ]
    audit_call = reserve_call(
        ctx,
        kind="audit",
        stage="roleboard_style_similarity_audit",
        candidate_id=item["candidate_id"],
        sample_id=sample_id,
        round_name=item["round"],
    )
    try:
        audit_output = await audit_provider.generate_json(
            audit_request,
            SimilarityAuditOutput,
            temperature=AUDIT_TEMPERATURE,
            metadata={
                "node_name": "roleboard_image_audit",
                "prompt_asset_type": "roleboard_style_similarity_iteration",
                "prompt_asset_name": sample_id,
                "sample_id": sample_id,
                "candidate_id": item["candidate_id"],
                "rubric_revision": rubric.get("revision"),
                "rubric_sha256": sha256(rubric_path),
                "temperature": AUDIT_TEMPERATURE,
                "max_output_tokens": 8192,
            },
            refs=[asset_ref(record) for record in audit_refs_meta],
        )
        normalized = normalize_audit(audit_output, rubric)
        audit_response_path = ctx.lab_dir / "responses" / "audit" / f"{sample_id}.json"
        write_json(
            audit_response_path,
            {
                "stage": "roleboard_style_similarity_audit",
                "sample_id": sample_id,
                "candidate_id": item["candidate_id"],
                "input_prompt": audit_request,
                "ordered_references": audit_refs_meta,
                "rubric_path": str(rubric_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "rubric_sha256": sha256(rubric_path),
                "rubric_revision": rubric.get("revision"),
                "output": audit_output.model_dump(mode="json"),
                "normalized": normalized,
            },
        )
        complete_call(
            ctx,
            audit_call,
            status="success",
            payload={
                "stage": "roleboard_style_similarity_audit",
                "response_path": str(audit_response_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "raw_score": normalized["raw_score"],
                "gate_pass": normalized["gate_pass"],
                "approved": normalized["approved"],
            },
        )
        evaluation = {
            "candidate_id": item["candidate_id"],
            "sample_id": sample_id,
            "scope": "asset",
            "role_id": item["role_id"],
            "appearance_id": item["appearance_id"],
            "status": "scored",
            "scores": normalized["scores"],
            "gates": normalized["gates"],
            "evidence": normalized["evidence"],
            "gate_evidence": normalized["gate_evidence"],
            "raw_score": normalized["raw_score"],
            "minimum_dimension_score": normalized["minimum_dimension_score"],
            "approved": normalized["approved"],
            "judge_id": "gemini-3.6-flash-roleboard-style-similarity",
            "rubric_sha256": sha256(rubric_path),
            "rubric_revision": rubric.get("revision"),
            "image_sha256": sha256(image_path),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "evaluations" / "image.jsonl", evaluation)
        summary = {
            **item,
            "kind": "sample_summary",
            "status": "success",
            "style_prompt": candidate["roleboard_style_prompt"],
            "style_prompt_sha256": candidate["sha256"],
            "prompt_output": prompt_payload,
            "image_prompt": image_prompt,
            "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "image_sha256": sha256(image_path),
            "audit": normalized,
            "rubric_revision": rubric.get("revision"),
            "rubric_sha256": sha256(rubric_path),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        print(
            f"[style-prompt] {item['round']} {item['candidate_id']} {item['role_name']} "
            f"score={normalized['raw_score']:.3f} gates={'pass' if normalized['gate_pass'] else 'fail'}",
            flush=True,
        )
        return summary
    except Exception as exc:
        status = failure_status(exc, "roleboard_style_similarity_audit")
        complete_call(
            ctx,
            audit_call,
            status=status,
            payload={"stage": "roleboard_style_similarity_audit", "error_type": type(exc).__name__, "error": str(exc)},
        )
        summary = {
            **item,
            "kind": "sample_summary",
            "status": status,
            "failure_stage": "roleboard_style_similarity_audit",
            "error": str(exc),
            "style_prompt": candidate["roleboard_style_prompt"],
            "style_prompt_sha256": candidate["sha256"],
            "prompt_output": prompt_payload,
            "image_prompt": image_prompt,
            "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "image_sha256": sha256(image_path),
            "at": utc_now(),
        }
        append_jsonl(ctx.lab_dir / "results.jsonl", summary)
        return summary


def baseline_manifest(ctx: lab_base.LabContext) -> dict[str, Any]:
    return read_json(ctx.lab_dir / "inputs" / "production-baseline" / "manifest.json")


async def audit_baseline(ctx: lab_base.LabContext, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("--confirm-paid-calls is required")
    verify_production_unchanged(ctx)
    existing = latest_samples(ctx)
    settings = load_settings(str(ctx.config_path))
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    prompts = PromptStore()
    rubric_path, rubric = current_rubric(ctx)
    roles = {item["role_name"]: item for item in base_role_inputs(ctx)}
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        sample_id = f"baseline-{role_short(row['role_name'])}"
        if sample_id in existing:
            return existing[sample_id]
        role_input = roles[row["role_name"]]
        image_path = Path(str(row["snapshot_path"]))
        request = render_similarity_audit_request(prompts, role_input, rubric)
        refs = [
            {"index": 1, "logical_role": "generated_roleboard", "path": str(image_path), "sha256": sha256(image_path), "reference_role": "audit_target"},
            {"index": 2, "logical_role": "key_vision_style", "path": str(ctx.key_vision_path), "sha256": ctx.key_vision_hash, "reference_role": "key_vision_style"},
            {"index": 3, "logical_role": "spatial_template", "path": str(ctx.spatial_template_path), "sha256": ctx.spatial_template_hash, "reference_role": "spatial_template"},
        ]
        call_id = reserve_call(
            ctx,
            kind="audit",
            stage="roleboard_style_similarity_audit",
            candidate_id="baseline-production-style-prompt",
            sample_id=sample_id,
            round_name="baseline",
        )
        try:
            output = await audit_provider.generate_json(
                request,
                SimilarityAuditOutput,
                temperature=AUDIT_TEMPERATURE,
                metadata={
                    "node_name": "roleboard_image_audit",
                    "prompt_asset_type": "roleboard_style_similarity_baseline",
                    "prompt_asset_name": sample_id,
                    "sample_id": sample_id,
                    "candidate_id": "baseline-production-style-prompt",
                    "rubric_revision": rubric.get("revision"),
                    "rubric_sha256": sha256(rubric_path),
                    "temperature": AUDIT_TEMPERATURE,
                    "max_output_tokens": 8192,
                },
                refs=[asset_ref(item) for item in refs],
            )
            normalized = normalize_audit(output, rubric)
            response_path = ctx.lab_dir / "responses" / "audit" / f"{sample_id}.json"
            write_json(
                response_path,
                {
                    "stage": "roleboard_style_similarity_audit",
                    "sample_id": sample_id,
                    "candidate_id": "baseline-production-style-prompt",
                    "input_prompt": request,
                    "ordered_references": refs,
                    "rubric_path": str(rubric_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                    "rubric_sha256": sha256(rubric_path),
                    "rubric_revision": rubric.get("revision"),
                    "output": output.model_dump(mode="json"),
                    "normalized": normalized,
                },
            )
            complete_call(
                ctx,
                call_id,
                status="success",
                payload={"stage": "roleboard_style_similarity_audit", "response_path": str(response_path.relative_to(ctx.lab_dir)).replace("\\", "/"), "raw_score": normalized["raw_score"]},
            )
            evaluation = {
                "candidate_id": "baseline-production-style-prompt",
                "sample_id": sample_id,
                "scope": "asset",
                "role_id": role_input["role_id"],
                "appearance_id": role_input["appearance_id"],
                "status": "scored",
                "scores": normalized["scores"],
                "gates": normalized["gates"],
                "evidence": normalized["evidence"],
                "gate_evidence": normalized["gate_evidence"],
                "raw_score": normalized["raw_score"],
                "minimum_dimension_score": normalized["minimum_dimension_score"],
                "approved": normalized["approved"],
                "judge_id": "gemini-3.6-flash-roleboard-style-similarity",
                "rubric_sha256": sha256(rubric_path),
                "rubric_revision": rubric.get("revision"),
                "image_sha256": sha256(image_path),
                "at": utc_now(),
            }
            append_jsonl(ctx.lab_dir / "evaluations" / "image.jsonl", evaluation)
            summary = {
                "kind": "sample_summary",
                "sample_id": sample_id,
                "round": "baseline",
                "candidate_id": "baseline-production-style-prompt",
                "role_name": row["role_name"],
                "appearance_name": "base",
                "role_id": role_input["role_id"],
                "appearance_id": role_input["appearance_id"],
                "status": "success",
                "style_prompt": baseline_manifest(ctx)["style_prompt"],
                "style_prompt_sha256": baseline_manifest(ctx)["style_prompt_sha256"],
                "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "image_sha256": sha256(image_path),
                "audit": normalized,
                "rubric_revision": rubric.get("revision"),
                "rubric_sha256": sha256(rubric_path),
                "at": utc_now(),
            }
            append_jsonl(ctx.lab_dir / "results.jsonl", summary)
            print(f"[baseline] {row['role_name']} score={normalized['raw_score']:.3f}", flush=True)
            return summary
        except Exception as exc:
            status = failure_status(exc, "roleboard_style_similarity_audit")
            complete_call(ctx, call_id, status=status, payload={"stage": "roleboard_style_similarity_audit", "error_type": type(exc).__name__, "error": str(exc)})
            summary = {"kind": "sample_summary", "sample_id": sample_id, "round": "baseline", "candidate_id": "baseline-production-style-prompt", "role_name": row["role_name"], "status": status, "failure_stage": "roleboard_style_similarity_audit", "error": str(exc), "image_path": str(image_path.relative_to(ctx.lab_dir)).replace("\\", "/"), "image_sha256": sha256(image_path), "at": utc_now()}
            append_jsonl(ctx.lab_dir / "results.jsonl", summary)
            return summary

    async def guarded(row: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await one(row)

    rows = await asyncio.gather(*(guarded(row) for row in baseline_manifest(ctx)["images"]))
    make_contact_sheets(ctx, "baseline", rows)
    write_ranking(ctx, "baseline")
    return {"lab": str(ctx.lab_dir), "baseline_samples": len(rows), "audit_reserved": reserved_count(ctx, "audit")}


async def run_round(
    ctx: lab_base.LabContext,
    *,
    round_number: int,
    candidate_file: Path,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("--confirm-paid-calls is required")
    experiment = load_experiment(ctx.lab_dir)
    if experiment.get("status") == "completed":
        raise ValueError("Experiment is already completed")
    verify_production_unchanged(ctx)
    candidates = load_candidate_file(candidate_file, round_number)
    existing_ids = {
        path.stem
        for path in (ctx.lab_dir / "candidates" / "style_prompt").glob("**/*.json")
    }
    for candidate in candidates:
        if candidate["candidate_id"] in existing_ids:
            existing = candidate_for_id(ctx, candidate["candidate_id"])
            if existing != candidate:
                raise FileExistsError(f"Candidate ID already exists with different content: {candidate['candidate_id']}")
    freeze_candidates(ctx, round_number, candidates)
    items = batch_items(ctx, round_number, candidates)
    write_batch_plan(ctx, round_number, candidates, items)
    role_by_name = {item["role_name"]: item for item in base_role_inputs(ctx)}
    candidate_by_id = {item["candidate_id"]: item for item in candidates}
    settings = load_settings(str(ctx.config_path))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    prompts = PromptStore()
    controls = image_controls(ctx.config_path)
    rubric_path, rubric = current_rubric(ctx)
    existing = latest_samples(ctx)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await run_candidate_sample(
                ctx,
                item,
                role_by_name[item["role_name"]],
                candidate_by_id[item["candidate_id"]],
                prompt_provider,
                image_provider,
                audit_provider,
                media_store,
                prompts,
                controls,
                rubric_path,
                rubric,
                existing,
            )

    rows = await asyncio.gather(*(one(item) for item in items))
    make_contact_sheets(ctx, round_id(round_number), rows)
    ranking = write_ranking(ctx, round_id(round_number))
    experiment = load_experiment(ctx.lab_dir)
    completed = list(experiment.get("completed_rounds") or [])
    if round_id(round_number) not in completed:
        completed.append(round_id(round_number))
    experiment["completed_rounds"] = sorted(completed)
    experiment["current_round"] = round_id(round_number)
    experiment["updated_at"] = utc_now()
    save_experiment(ctx.lab_dir, experiment)
    return {
        "lab": str(ctx.lab_dir),
        "round": round_id(round_number),
        "samples": len(rows),
        "successful": sum(row.get("status") == "success" for row in rows),
        "ranking": ranking,
        "prompt_reserved": reserved_count(ctx, "gemini"),
        "image_reserved": reserved_count(ctx, "image"),
        "audit_reserved": reserved_count(ctx, "audit"),
    }


def contact_rows(ctx: lab_base.LabContext, round_name: str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    candidates = rows or list(latest_samples(ctx).values())
    return [
        row
        for row in candidates
        if row.get("round") == round_name
        and row.get("image_path")
        and (ctx.lab_dir / str(row["image_path"])).is_file()
    ]


def make_contact_sheets(
    ctx: lab_base.LabContext,
    round_name: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, str] | None:
    source_rows = contact_rows(ctx, round_name, rows)
    if not source_rows:
        return None
    seed = CONTACT_SEED + sum(ord(char) for char in round_name)
    rng = random.Random(seed)
    shuffled = list(source_rows)
    rng.shuffle(shuffled)
    mapping: dict[str, Any] = {}
    thumb_w, thumb_h, label_h, columns = 600, 360, 38, 3
    row_count = (len(shuffled) + columns - 1) // columns
    full = Image.new("RGB", (columns * thumb_w, row_count * (thumb_h + label_h)), "#12171a")
    draw = ImageDraw.Draw(full)
    detail_w, detail_h = 520, 250
    detail = Image.new("RGB", (columns * detail_w, row_count * (detail_h + label_h)), "#12171a")
    detail_draw = ImageDraw.Draw(detail)
    for index, row in enumerate(shuffled, start=1):
        blind_id = f"B{index:02d}"
        path = ctx.lab_dir / str(row["image_path"])
        mapping[blind_id] = {
            "sample_id": row["sample_id"],
            "candidate_id": row["candidate_id"],
            "role_name": row.get("role_name"),
            "image_path": row["image_path"],
            "sha256": sha256(path),
        }
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            fitted = ImageOps.contain(image, (thumb_w - 20, thumb_h - 20), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb_w, thumb_h), "#ececea")
            tile.paste(fitted, ((thumb_w - fitted.width) // 2, (thumb_h - fitted.height) // 2))
            panel_width = image.width / 3
            crops: list[Image.Image] = []
            for panel_index in (0, 1):
                center_x = (panel_index + 0.5) * panel_width
                crop = image.crop(
                    (
                        max(0, int(center_x - panel_width * 0.24)),
                        max(0, int(image.height * 0.06)),
                        min(image.width, int(center_x + panel_width * 0.24)),
                        min(image.height, int(image.height * 0.39)),
                    )
                )
                crops.append(ImageOps.fit(crop, (detail_w // 2, detail_h), Image.Resampling.LANCZOS))
            detail_tile = Image.new("RGB", (detail_w, detail_h), "#ececea")
            detail_tile.paste(crops[0], (0, 0))
            detail_tile.paste(crops[1], (detail_w // 2, 0))
        column = (index - 1) % columns
        grid_row = (index - 1) // columns
        x, y = column * thumb_w, grid_row * (thumb_h + label_h)
        full.paste(tile, (x, y))
        draw.text((x + 12, y + thumb_h + 9), blind_id, fill="#f6f3ea")
        dx, dy = column * detail_w, grid_row * (detail_h + label_h)
        detail.paste(detail_tile, (dx, dy))
        detail_draw.text((dx + 12, dy + detail_h + 9), blind_id, fill="#f6f3ea")
    blind_dir = ctx.lab_dir / "contact_sheets" / "blind"
    answer_dir = ctx.lab_dir / "contact_sheets" / "answer_keys"
    manifest_dir = ctx.lab_dir / "contact_sheets" / "manifests"
    blind_dir.mkdir(parents=True, exist_ok=True)
    answer_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    full_path = blind_dir / f"{round_name}-full.png"
    detail_path = blind_dir / f"{round_name}-faces.png"
    full.save(full_path, format="PNG", optimize=True)
    detail.save(detail_path, format="PNG", optimize=True)
    write_json(answer_dir / f"{round_name}.json", {"seed": seed, "mapping": mapping})
    write_json(
        manifest_dir / f"{round_name}.json",
        {
            "seed": seed,
            "crop_policy": "full board uses fit-within; detail sheet shows fixed front/profile head regions without modifying source",
            "columns": columns,
            "full_thumbnail": [thumb_w, thumb_h],
            "detail_thumbnail": [detail_w, detail_h],
            "source_count": len(shuffled),
            "full_path": str(full_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "detail_path": str(detail_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
        },
    )
    return {"full": str(full_path), "faces": str(detail_path)}


def rows_for_candidate(ctx: lab_base.LabContext, candidate_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in latest_samples(ctx).values()
        if row.get("candidate_id") == candidate_id and row.get("status") == "success" and isinstance(row.get("audit"), dict)
    ]


def candidate_stats(ctx: lab_base.LabContext, candidate_id: str, rubric: dict[str, Any]) -> dict[str, Any]:
    rows = rows_for_candidate(ctx, candidate_id)
    if not rows:
        return {"candidate_id": candidate_id, "n": 0, "mean": None, "minimum": None, "variance": None, "gate_pass_rate": None, "approval_rate": None, "dimension_means": {}, "roles": {}}
    values: list[float] = []
    dimension_values: dict[str, list[float]] = defaultdict(list)
    role_scores: dict[str, float] = {}
    gate_passes = 0
    approvals = 0
    for row in rows:
        normalized = recompute_normalized(row["audit"], rubric)
        values.append(normalized["raw_score"])
        gate_passes += int(normalized["gate_pass"])
        approvals += int(normalized["approved"])
        role_scores[str(row.get("role_name"))] = normalized["raw_score"]
        for key, value in normalized["scores"].items():
            dimension_values[key].append(float(value))
    return {
        "candidate_id": candidate_id,
        "n": len(values),
        "mean": statistics.fmean(values),
        "minimum": min(values),
        "variance": statistics.pvariance(values) if len(values) > 1 else 0.0,
        "gate_pass_rate": gate_passes / len(values),
        "approval_rate": approvals / len(values),
        "dimension_means": {key: statistics.fmean(items) for key, items in dimension_values.items()},
        "roles": role_scores,
    }


def recompute_normalized(audit: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    scores = {str(key): float(value) for key, value in (audit.get("scores") or {}).items()}
    gates = {str(key): bool(value) for key, value in (audit.get("gates") or {}).items()}
    dimension_ids, gate_ids = rubric_ids(rubric)
    if set(scores) != set(dimension_ids) or set(gates) != set(gate_ids):
        raise ValueError("Stored audit cannot be recomputed under a rubric with different dimension or gate IDs")
    weight_sum = sum(float(item["weight"]) for item in rubric["dimensions"])
    raw_score = sum(scores[str(item["id"])] * float(item["weight"]) for item in rubric["dimensions"]) / weight_sum
    minimum_score = min(scores.values())
    gate_pass = all(gates.values())
    policy = rubric.get("policy") or {}
    approved = bool(
        gate_pass
        and raw_score >= float(policy.get("approval_threshold") or 8.0)
        and minimum_score >= float(policy.get("minimum_dimension_score") or 7.0)
    )
    return {**audit, "raw_score": raw_score, "minimum_dimension_score": minimum_score, "gate_pass": gate_pass, "approved": approved, "scores": scores, "gates": gates}


def write_ranking(ctx: lab_base.LabContext, round_name: str) -> dict[str, Any]:
    _rubric_path, rubric = current_rubric(ctx)
    ids = sorted(
        {
            str(row.get("candidate_id"))
            for row in latest_samples(ctx).values()
            if row.get("round") == round_name and row.get("candidate_id")
        }
    )
    stats_rows = [candidate_stats(ctx, candidate_id, rubric) for candidate_id in ids]
    stats_rows.sort(
        key=lambda row: (
            float(row.get("gate_pass_rate") or 0),
            int(row.get("n") or 0) >= BASE_ROLE_COUNT,
            float(row.get("mean") or -1),
            float(row.get("minimum") or -1),
            -float(row.get("variance") or 999),
        ),
        reverse=True,
    )
    payload = {
        "round": round_name,
        "rubric_revision": rubric.get("revision"),
        "rubric_sha256": current_rubric(ctx)[0] and sha256(current_rubric(ctx)[0]),
        "ranking": stats_rows,
        "advisory_winner": stats_rows[0]["candidate_id"] if stats_rows else None,
        "selection_authority": "Codex supervised visual review",
        "created_at": utc_now(),
    }
    write_json(ctx.lab_dir / "evaluations" / "rankings" / f"{round_name}.json", payload)
    return payload


def record_analysis(ctx: lab_base.LabContext, round_number: int, source: Path) -> dict[str, Any]:
    payload = read_json(source)
    rid = round_id(round_number)
    if str(payload.get("round") or "") != rid:
        raise ValueError(f"Analysis round must be {rid}")
    candidate_ids = {
        path.stem for path in (ctx.lab_dir / "candidates" / "style_prompt" / rid).glob("*.json")
    }
    selected = str(payload.get("selected_candidate") or "")
    if selected not in candidate_ids and selected != "none":
        raise ValueError(f"selected_candidate must be one of {sorted(candidate_ids)} or none")
    if not isinstance(payload.get("visual_findings"), list) or not payload["visual_findings"]:
        raise ValueError("visual_findings must be a non-empty list")
    if round_number < ROUND_COUNT and (
        not isinstance(payload.get("next_hypotheses"), list) or len(payload["next_hypotheses"]) != CANDIDATES_PER_ROUND
    ):
        raise ValueError(f"next_hypotheses must contain exactly {CANDIDATES_PER_ROUND} items")
    target = ctx.lab_dir / "evaluations" / "rounds" / f"{rid}-analysis.json"
    write_json_immutable(target, payload)
    append_jsonl(
        ctx.lab_dir / "results.jsonl",
        {
            "kind": "round_human_selection",
            "round": rid,
            "selected_candidate": selected,
            "analysis_path": str(target.relative_to(ctx.lab_dir)).replace("\\", "/"),
            "at": utc_now(),
        },
    )
    return payload


def register_rubric(ctx: lab_base.LabContext, source: Path) -> dict[str, Any]:
    current_path, current = current_rubric(ctx)
    challenger = read_json(source)
    current_dimension_ids, current_gate_ids = rubric_ids(current)
    challenger_dimension_ids, challenger_gate_ids = rubric_ids(challenger)
    if challenger_dimension_ids != current_dimension_ids or challenger_gate_ids != current_gate_ids:
        raise ValueError("Weight-only rubric updates must preserve dimension and gate IDs")
    current_questions = {item["id"]: item.get("question") for item in current["dimensions"]}
    challenger_questions = {item["id"]: item.get("question") for item in challenger["dimensions"]}
    current_descriptions = {item["id"]: item.get("description") for item in current["gates"]}
    challenger_descriptions = {item["id"]: item.get("description") for item in challenger["gates"]}
    if challenger_questions != current_questions or challenger_descriptions != current_descriptions:
        raise ValueError("Changing rubric wording requires re-auditing all images; this command accepts weight/threshold changes only")
    revision = str(challenger.get("revision") or "").strip()
    if not revision or revision == str(current.get("revision") or ""):
        raise ValueError("New rubric must have a distinct non-empty revision")
    target = ctx.lab_dir / "rubrics" / f"roleboard-keyvision-person-design-rubric-v{revision}.json"
    write_json_immutable(target, challenger)
    experiment = load_experiment(ctx.lab_dir)
    history = list(experiment.get("rubric_history") or [])
    history.append(
        {
            "from_revision": current.get("revision"),
            "from_sha256": sha256(current_path),
            "to_revision": revision,
            "to_sha256": sha256(target),
            "change_type": "weights_and_thresholds_only",
            "registered_at": utc_now(),
        }
    )
    experiment["rubric_history"] = history
    experiment["current_rubric"] = {
        "path": str(target.relative_to(ctx.lab_dir)).replace("\\", "/"),
        "sha256": sha256(target),
        "revision": revision,
    }
    save_experiment(ctx.lab_dir, experiment)
    for rid in ["baseline", *[round_id(index) for index in range(1, ROUND_COUNT + 1)]]:
        if any(row.get("round") == rid for row in latest_samples(ctx).values()):
            write_ranking(ctx, rid)
    return experiment["current_rubric"]


def record_final(ctx: lab_base.LabContext, source: Path) -> dict[str, Any]:
    payload = read_json(source)
    champion = str(payload.get("champion_candidate_id") or "").strip()
    if not champion:
        raise ValueError("champion_candidate_id is required")
    candidate_for_id(ctx, champion)
    if not isinstance(payload.get("lessons"), list) or not payload["lessons"]:
        raise ValueError("lessons must be a non-empty list")
    target = ctx.lab_dir / "evaluations" / "final-analysis.json"
    write_json_immutable(target, payload)
    experiment = load_experiment(ctx.lab_dir)
    experiment["status"] = "completed"
    experiment["current_phase"] = "report"
    experiment["champion_candidate_id"] = champion
    experiment["stop_reason"] = str(payload.get("stop_reason") or "completed 10 authorized rounds")
    experiment["completed_at"] = utc_now()
    save_experiment(ctx.lab_dir, experiment)
    return payload


def image_data_uri(path: Path, *, max_width: int = 1000, quality: int = 74) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if image.width > max_width:
            height = round(image.height * max_width / image.width)
            image = image.resize((max_width, height), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def format_score(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def report_candidate_ids(ctx: lab_base.LabContext) -> list[str]:
    return sorted(
        path.stem
        for path in (ctx.lab_dir / "candidates" / "style_prompt").glob("**/*.json")
        if path.stem != "baseline-production-style-prompt"
    )


def build_report(ctx: lab_base.LabContext) -> Path:
    experiment = load_experiment(ctx.lab_dir)
    rubric_path, rubric = current_rubric(ctx)
    rows = list(latest_samples(ctx).values())
    candidate_ids = report_candidate_ids(ctx)
    overall = [candidate_stats(ctx, candidate_id, rubric) for candidate_id in candidate_ids]
    overall.sort(
        key=lambda row: (
            float(row.get("gate_pass_rate") or 0),
            int(row.get("n") or 0) >= BASE_ROLE_COUNT,
            float(row.get("mean") or -1),
            float(row.get("minimum") or -1),
            -float(row.get("variance") or 999),
        ),
        reverse=True,
    )
    baseline = candidate_stats(ctx, "baseline-production-style-prompt", rubric)
    final_path = ctx.lab_dir / "evaluations" / "final-analysis.json"
    final = read_json(final_path) if final_path.is_file() else {}
    champion_id = str(final.get("champion_candidate_id") or experiment.get("champion_candidate_id") or "")
    champion = candidate_stats(ctx, champion_id, rubric) if champion_id else None
    ledger = read_jsonl(ctx.lab_dir / "ledger.jsonl")
    reserved = Counter(str(row.get("kind")) for row in ledger if row.get("event") == "reserve")
    completions = Counter(str(row.get("status")) for row in ledger if row.get("event") == "complete")
    analyses = []
    for path in sorted((ctx.lab_dir / "evaluations" / "rounds").glob("*-analysis.json")):
        analyses.append(read_json(path))

    ranking_rows = "".join(
        "<tr>"
        f"<td>{index}</td><td>{html.escape(str(item['candidate_id']))}</td>"
        f"<td>{item['n']}</td><td>{format_score(item['mean'])}</td><td>{format_score(item['minimum'])}</td>"
        f"<td>{format_score(item['variance'])}</td><td>{format_score((item['gate_pass_rate'] or 0) * 100)}%</td>"
        f"<td>{html.escape(json.dumps(item['roles'], ensure_ascii=False))}</td></tr>"
        for index, item in enumerate(overall, start=1)
    )
    dimension_rows = "".join(
        f"<tr><td>{html.escape(str(item['id']))}</td><td>{item['weight']}</td><td>{html.escape(str(item['question']))}</td></tr>"
        for item in rubric["dimensions"]
    )
    candidate_prompt_cards = []
    for candidate_id in candidate_ids:
        candidate = candidate_for_id(ctx, candidate_id)
        candidate_prompt_cards.append(
            "<details class='candidate'><summary>"
            f"{html.escape(candidate_id)} · {html.escape(str(candidate.get('changed_family') or ''))}"
            "</summary>"
            f"<p>{html.escape(str(candidate.get('hypothesis') or ''))}</p>"
            f"<pre>{html.escape(str(candidate.get('roleboard_style_prompt') or ''))}</pre>"
            f"<code>{html.escape(str(candidate.get('sha256') or ''))}</code></details>"
        )
    analysis_cards = []
    for analysis in analyses:
        findings = "".join(f"<li>{html.escape(str(value))}</li>" for value in analysis.get("visual_findings", []))
        next_items = "".join(f"<li>{html.escape(str(value))}</li>" for value in analysis.get("next_hypotheses", []))
        analysis_cards.append(
            "<article class='analysis'>"
            f"<h3>{html.escape(str(analysis.get('round')))} · 选择 {html.escape(str(analysis.get('selected_candidate')))}</h3>"
            f"<p>{html.escape(str(analysis.get('selection_reason') or ''))}</p><ul>{findings}</ul>"
            f"<h4>下一轮假设</h4><ol>{next_items}</ol>"
            f"<p><b>Rubric 决策：</b>{html.escape(str(analysis.get('rubric_decision') or ''))}</p></article>"
        )
    round_sections = []
    for number in range(1, ROUND_COUNT + 1):
        rid = round_id(number)
        round_rows = sorted(
            [row for row in rows if row.get("round") == rid],
            key=lambda row: (str(row.get("candidate_id")), str(row.get("role_name"))),
        )
        if not round_rows:
            continue
        ranking_path = ctx.lab_dir / "evaluations" / "rankings" / f"{rid}.json"
        ranking = read_json(ranking_path) if ranking_path.is_file() else {}
        cards = []
        for row in round_rows:
            path = ctx.lab_dir / str(row.get("image_path") or "")
            image_html = (
                f"<img loading='lazy' src='{image_data_uri(path)}' alt='{html.escape(str(row.get('sample_id')))}'>"
                if path.is_file()
                else "<div class='missing'>无图片</div>"
            )
            audit = row.get("audit") or {}
            lowest = sorted((audit.get("scores") or {}).items(), key=lambda item: float(item[1]))[:3]
            evidence = audit.get("evidence") or {}
            low_html = "".join(
                f"<li><b>{html.escape(str(key))} {float(value):.1f}</b>：{html.escape(str(evidence.get(key) or ''))}</li>"
                for key, value in lowest
            )
            cards.append(
                "<article class='sample'>"
                f"{image_html}<h4>{html.escape(str(row.get('candidate_id')))} · {html.escape(str(row.get('role_name')))}</h4>"
                f"<p class='score'>相似度 {format_score(audit.get('raw_score'))} · 门槛 {'通过' if audit.get('gate_pass') else '失败'}</p>"
                f"<ul>{low_html}</ul>"
                f"<details><summary>Gemini 输出与最终生图提示词</summary><pre>{html.escape(json.dumps(row.get('prompt_output') or {}, ensure_ascii=False, indent=2))}</pre><pre>{html.escape(str(row.get('image_prompt') or ''))}</pre></details>"
                "</article>"
            )
        contact_html = ""
        for suffix, label in (("full", "盲评全图"), ("faces", "盲评五官细节")):
            path = ctx.lab_dir / "contact_sheets" / "blind" / f"{rid}-{suffix}.png"
            if path.is_file():
                contact_html += f"<figure><figcaption>{label}</figcaption><img class='contact' src='{image_data_uri(path, max_width=1500, quality=78)}'></figure>"
        round_sections.append(
            f"<section><h2>{rid}</h2><p>模型评分建议胜者：{html.escape(str(ranking.get('advisory_winner') or '无'))}</p>"
            f"<div class='contacts'>{contact_html}</div><div class='samples'>{''.join(cards)}</div></section>"
        )
    lessons_html = "".join(f"<li>{html.escape(str(value))}</li>" for value in final.get("lessons", []))
    limitations_html = "".join(f"<li>{html.escape(str(value))}</li>" for value in final.get("limitations", []))
    key_vision_uri = image_data_uri(ctx.key_vision_path, max_width=1500, quality=84)
    report = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Roleboard Style Prompt 10×3 迭代报告</title>
<style>
:root{{--bg:#07100e;--panel:#101d19;--panel2:#14251f;--line:#29443a;--text:#eef6f1;--muted:#9eb4aa;--mint:#73e3b1;--gold:#e6bc68;--bad:#ef806f}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:1540px;margin:auto;padding:28px}} h1{{font-size:clamp(34px,6vw,72px);line-height:1.02;margin:0 0 16px;max-width:1000px}}
h2{{font-size:29px;margin:50px 0 16px}} h3{{margin:10px 0}} .hero{{display:grid;grid-template-columns:1.1fr .9fr;gap:24px;align-items:center;padding:28px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(135deg,#10251d,#08110f)}}
.hero img,.contact,.sample img{{width:100%;display:block;border-radius:14px}} .eyebrow{{color:var(--mint);letter-spacing:.16em;text-transform:uppercase;font-weight:700}}
.muted{{color:var(--muted)}} .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:22px 0}}
.metric,.analysis,.candidate,.sample,table{{background:var(--panel);border:1px solid var(--line);border-radius:14px}} .metric{{padding:16px}} .metric strong{{display:block;font-size:28px;color:var(--mint)}}
table{{width:100%;border-collapse:separate;border-spacing:0;overflow:hidden}} th,td{{padding:10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}} th{{color:var(--mint);background:#0c1714}}
.samples{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}} .sample{{padding:12px}} .score{{color:var(--gold);font-weight:700}}
.analysis{{padding:18px;margin:14px 0}} .candidate{{padding:12px 16px;margin:9px 0}} summary{{cursor:pointer;color:var(--mint);font-weight:700}}
pre{{white-space:pre-wrap;word-break:break-word;background:#07100e;border:1px solid var(--line);border-radius:10px;padding:12px;color:#d5e7df;font:12px/1.5 ui-monospace,Consolas,monospace}}
.contacts{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} figure{{margin:0}} figcaption{{color:var(--muted);margin-bottom:6px}} .missing{{padding:120px 20px;text-align:center;color:var(--bad)}}
code{{word-break:break-all;color:var(--gold)}} @media(max-width:900px){{.hero,.contacts{{grid-template-columns:1fr}} main{{padding:16px}}}}
</style></head><body><main>
<section class='hero'><div><p class='eyebrow'>Controlled Roleboard Experiment · 2026-08</p><h1>主视觉人物设计语言<br>10×3 Prompt 迭代</h1>
<p>唯一变量是 <code>generation.roleboard_style_prompt</code>。角色事实、Gemini 模板、生图包装、白模、主视觉、参考顺序、模型、尺寸、温度和评分维度全部冻结。</p>
<p class='muted'>相似度指同一项目的人物设计与渲染语言，不指复制主视觉中的具体人物。</p></div><img src='{key_vision_uri}' alt='用户确认主视觉'></section>
<div class='metrics'>
<div class='metric'><span>候选</span><strong>{len(candidate_ids)} / 30</strong></div>
<div class='metric'><span>图片调用</span><strong>{reserved['image']} / {IMAGE_BUDGET}</strong></div>
<div class='metric'><span>Gemini Prompt</span><strong>{reserved['gemini']} / {PROMPT_BUDGET}</strong></div>
<div class='metric'><span>Gemini Audit</span><strong>{reserved['audit']} / {AUDIT_BUDGET}</strong></div>
<div class='metric'><span>生产基线</span><strong>{format_score(baseline.get('mean'))}</strong></div>
<div class='metric'><span>最终选择</span><strong>{html.escape(champion_id or '未选择')}</strong></div></div>
<h2>实验结论</h2><p>{html.escape(str(final.get('rationale') or '尚未完成最终人工判断。'))}</p>
<p><b>基线：</b>{format_score(baseline.get('mean'))}　<b>最终候选：</b>{format_score((champion or {}).get('mean'))}　<b>停止原因：</b>{html.escape(str(experiment.get('stop_reason') or '进行中'))}</p>
<h3>经验总结</h3><ul>{lessons_html or '<li>实验尚未完成。</li>'}</ul><h3>限制</h3><ul>{limitations_html or '<li>实验尚未完成。</li>'}</ul>
<h2>最终 Rubric</h2><p>revision {html.escape(str(rubric.get('revision')))} · <code>{sha256(rubric_path)}</code></p>
<table><tr><th>维度</th><th>权重</th><th>可见判断</th></tr>{dimension_rows}</table>
<h2>30 个候选总排名</h2><table><tr><th>#</th><th>候选</th><th>N</th><th>均值</th><th>最低</th><th>方差</th><th>门槛通过率</th><th>角色分</th></tr>{ranking_rows}</table>
<h2>逐轮人工判断</h2>{''.join(analysis_cards) or '<p>暂无人工分析。</p>'}
<h2>完整 Style Prompt</h2>{''.join(candidate_prompt_cards)}
{''.join(round_sections)}
<h2>调用与失败分类</h2><p>预留：{html.escape(json.dumps(dict(reserved), ensure_ascii=False))}</p><p>完成状态：{html.escape(json.dumps(dict(completions), ensure_ascii=False))}</p>
<p class='muted'>实验目录：{html.escape(str(ctx.lab_dir))}</p>
</main></body></html>"""
    report_path = ctx.lab_dir / "reports" / "report-standalone.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8", newline="\n")
    return report_path


def verify_production_unchanged(ctx: lab_base.LabContext) -> None:
    expected = baseline_manifest(ctx)["production_hashes"]
    actual = source_production_hashes()
    if expected != actual:
        raise AssertionError(f"Production assets changed during experiment: expected={expected}, actual={actual}")


def preflight(ctx: lab_base.LabContext) -> dict[str, Any]:
    verify_production_unchanged(ctx)
    settings = load_settings(str(ctx.config_path))
    router = ProviderRouter(settings)
    prompt_provider = router.text("role", node_name="roleboard_prompt")
    image_provider = router.image("role", node_name="roleboard_image_generation")
    audit_provider = router.text("role", node_name="roleboard_image_audit")
    controls = image_controls(ctx.config_path)
    rubric_path, rubric = current_rubric(ctx)
    rubric_ids(rubric)
    roles = base_role_inputs(ctx)
    refs = reference_records(ctx)
    if [item["reference_role"] for item in refs] != ["spatial_template", "key_vision_style"]:
        raise AssertionError("Reference order drifted")
    return {
        "lab": str(ctx.lab_dir),
        "status": load_experiment(ctx.lab_dir).get("status"),
        "role_names": [item["role_name"] for item in roles],
        "prompt_provider_model": getattr(prompt_provider, "model", None),
        "image_provider_model": getattr(image_provider, "model", None),
        "audit_provider_model": getattr(audit_provider, "model", None),
        "image_controls": controls,
        "reference_order": [item["reference_role"] for item in refs],
        "key_vision_sha256": ctx.key_vision_hash,
        "spatial_template_sha256": ctx.spatial_template_hash,
        "rubric_revision": rubric.get("revision"),
        "rubric_sha256": sha256(rubric_path),
        "production_unchanged": True,
        "budgets": {"gemini": PROMPT_BUDGET, "image": IMAGE_BUDGET, "audit": AUDIT_BUDGET},
    }


def verify_complete(ctx: lab_base.LabContext) -> dict[str, Any]:
    verify_production_unchanged(ctx)
    experiment = load_experiment(ctx.lab_dir)
    expected_rounds = [round_id(index) for index in range(1, ROUND_COUNT + 1)]
    if sorted(experiment.get("completed_rounds") or []) != expected_rounds:
        raise AssertionError(f"Incomplete rounds: {experiment.get('completed_rounds')}")
    candidates = report_candidate_ids(ctx)
    if len(candidates) != ROUND_COUNT * CANDIDATES_PER_ROUND:
        raise AssertionError(f"Expected 30 candidates, found {len(candidates)}")
    prompts = [candidate_for_id(ctx, candidate_id)["roleboard_style_prompt"] for candidate_id in candidates]
    if len(prompts) != len(set(prompts)):
        raise AssertionError("All 30 roleboard_style_prompt candidates must be distinct")
    for index in range(1, ROUND_COUNT + 1):
        rid = round_id(index)
        manifests = list((ctx.lab_dir / "candidates" / "style_prompt" / rid).glob("*.json"))
        if len(manifests) != CANDIDATES_PER_ROUND:
            raise AssertionError(f"{rid} does not contain exactly three candidate manifests")
        if not (ctx.lab_dir / "evaluations" / "rounds" / f"{rid}-analysis.json").is_file():
            raise AssertionError(f"{rid} lacks supervised visual analysis")
        if not (ctx.lab_dir / "contact_sheets" / "blind" / f"{rid}-full.png").is_file():
            raise AssertionError(f"{rid} lacks blind contact sheet")
    if reserved_count(ctx, "image") > IMAGE_BUDGET or reserved_count(ctx, "gemini") > PROMPT_BUDGET or reserved_count(ctx, "audit") > AUDIT_BUDGET:
        raise AssertionError("Call budget exceeded")
    final_path = ctx.lab_dir / "evaluations" / "final-analysis.json"
    if not final_path.is_file():
        raise AssertionError("Final human analysis is missing")
    report = ctx.lab_dir / "reports" / "report-standalone.html"
    if not report.is_file():
        raise AssertionError("HTML report is missing")
    document = report.read_text(encoding="utf-8")
    if "src='http" in document or 'src="http' in document:
        raise AssertionError("HTML report contains external images")
    for path in (
        ctx.lab_dir / "experiment.json",
        ctx.lab_dir / "ledger.jsonl",
        ctx.lab_dir / "results.jsonl",
        ctx.lab_dir / "evaluations" / "image.jsonl",
    ):
        if path.suffix == ".jsonl":
            read_jsonl(path)
        else:
            read_json(path)
    return {
        "lab": str(ctx.lab_dir),
        "rounds": ROUND_COUNT,
        "candidates": len(candidates),
        "prompt_reserved": reserved_count(ctx, "gemini"),
        "image_reserved": reserved_count(ctx, "image"),
        "audit_reserved": reserved_count(ctx, "audit"),
        "sample_summaries": len(latest_samples(ctx)),
        "report": str(report),
        "report_bytes": report.stat().st_size,
        "production_unchanged": True,
    }


def inspect_lab(ctx: lab_base.LabContext) -> dict[str, Any]:
    experiment = load_experiment(ctx.lab_dir)
    return {
        "lab": str(ctx.lab_dir),
        "status": experiment.get("status"),
        "current_phase": experiment.get("current_phase"),
        "completed_rounds": experiment.get("completed_rounds") or [],
        "candidates": len(report_candidate_ids(ctx)),
        "samples": len(latest_samples(ctx)),
        "reserved": {
            "gemini": reserved_count(ctx, "gemini"),
            "image": reserved_count(ctx, "image"),
            "audit": reserved_count(ctx, "audit"),
        },
        "report": str(ctx.lab_dir / "reports" / "report-standalone.html"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controlled 10x3 roleboard_style_prompt iteration using saodi_0803 frozen assets"
    )
    parser.add_argument("--lab-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("preflight")
    baseline = sub.add_parser("baseline")
    baseline.add_argument("--confirm-paid-calls", action="store_true")
    run = sub.add_parser("run-round")
    run.add_argument("--round", type=int, required=True)
    run.add_argument("--candidates", required=True)
    run.add_argument("--confirm-paid-calls", action="store_true")
    analysis = sub.add_parser("record-analysis")
    analysis.add_argument("--round", type=int, required=True)
    analysis.add_argument("--file", required=True)
    rubric = sub.add_parser("register-rubric")
    rubric.add_argument("--file", required=True)
    final = sub.add_parser("record-final")
    final.add_argument("--file", required=True)
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
        ctx = lab_base.load_lab(lab_dir)
        if args.command == "preflight":
            result = preflight(ctx)
        elif args.command == "baseline":
            result = asyncio.run(audit_baseline(ctx, confirmed=bool(args.confirm_paid_calls)))
        elif args.command == "run-round":
            result = asyncio.run(
                run_round(
                    ctx,
                    round_number=int(args.round),
                    candidate_file=Path(args.candidates).resolve(),
                    confirmed=bool(args.confirm_paid_calls),
                )
            )
        elif args.command == "record-analysis":
            result = record_analysis(ctx, int(args.round), Path(args.file).resolve())
        elif args.command == "register-rubric":
            result = register_rubric(ctx, Path(args.file).resolve())
        elif args.command == "record-final":
            result = record_final(ctx, Path(args.file).resolve())
        elif args.command == "report":
            result = {"report": str(build_report(ctx))}
        elif args.command == "verify":
            result = verify_complete(ctx)
        elif args.command == "inspect":
            result = inspect_lab(ctx)
        else:
            raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
