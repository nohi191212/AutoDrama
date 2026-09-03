from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402


SKILL_ROOT = ROOT / ".agents" / "skills" / "iterate-roleboard-nodes"
DEFAULT_EXPLORATION_KEYS = ("叶凡/base", "柳菡烟/base", "李德海/base")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    changed_family: str
    reference_policy: str
    hypothesis: str


CANDIDATES = (
    CandidateSpec(
        candidate_id="p1-r00-current-v1",
        changed_family="incumbent-wrapper-and-anchor-chain",
        reference_policy="key_vision_for_anchor_then_cross_role_anchor",
        hypothesis="The current production wrapper and cross-role anchor chain establish the diagnostic incumbent.",
    ),
    CandidateSpec(
        candidate_id="p1-r00-conflict-free-v1",
        changed_family="remove-2d-versus-3d-media-conflict-only",
        reference_policy="key_vision_for_anchor_then_cross_role_anchor",
        hypothesis="Removing only the ink-2D conflict improves finish while exposing any remaining anchor contamination.",
    ),
    CandidateSpec(
        candidate_id="p1-r00-spatial-style-explicit-v1",
        changed_family="disjoint-spatial-and-style-reference-responsibilities",
        reference_policy="spatial_template_then_key_vision_independent_base",
        hypothesis="Explicitly separated spatial and style references improve three-view compliance without cloning cast identity.",
    ),
    CandidateSpec(
        candidate_id="p1-r00-spatial-only-control-v1",
        changed_family="spatial-template-only-control",
        reference_policy="spatial_template_only_independent_base",
        hypothesis="The spatial-only control measures layout benefit and male-warrior identity leakage from the white template.",
    ),
    CandidateSpec(
        candidate_id="p1-r00-style-only-control-v1",
        changed_family="key-vision-only-control",
        reference_policy="key_vision_only_independent_base",
        hypothesis="The style-only control measures style transfer and the layout loss caused by omitting the white template.",
    ),
)


COMMON_CHARACTER_AND_BOARD = """[CHARACTER IDENTITY AND DESIGN]
{character_contract}

Render this one named character three times as the same canonical identity. Preserve the same skull and facial geometry, hair construction, body proportions, garment pattern and layers, footwear, palette, and restrained signature details in all three views. The face must be role-specific and visibly readable, with concrete face length, jaw, cheekbone, brow, eye spacing and shape, nose, mouth, age structure, and restrained asymmetry. Keep the build anatomically plausible for the declared age, sex, occupation, status, and life history.

Use one dominant silhouette idea and at most two supporting signature details. Avoid random fantasy filigree, unnecessary armor, excessive jewelry, glowing trim, ornamental overload, fashion-editorial posing, and mobile-game skin design.

[EXACT OUTPUT GEOMETRY]
Produce one clean horizontal 16:9 single-character identity board, not a collage or concept sheet. Show exactly three separated complete full-body views and no others: left is front view looking straight ahead; center is a true side profile facing image-left; right is back view. All three figures have approximately equal height, share one horizontal foot baseline, keep every hair tip, hand, garment hem, and foot visible, and use a neutral natural standing pose. Arms stay slightly away from the torso so hands, seams, waist construction, and silhouette remain visible. Use an orthographic or long-lens turntable feel with no dramatic perspective, overlap, crop, inset, or extra view.

[CLEAN DELIVERY]
Use a simple low-contrast neutral studio background. No view labels, name, readable text, symbols, watermark, logo, frame lines, portrait close-up, action pose, held prop, extra subject, duplicate limb, or stray object. Layout, identity, anatomy, reference isolation, and role-specific design take priority over decorative beauty."""


SPATIAL_REFERENCE_CLAUSE = """Reference image 1 is a spatial white-model guide only. Copy only its one-board layout: exactly three separated, equal-scale, complete full-body views in a horizontal 16:9 frame, ordered front, true profile, back, with a shared foot baseline, similar margins, and a neutral readable stance. Do not copy its male identity, face, body build, crown, ponytail, armor, shoulder pieces, sword, boots, garment construction, ornament, or white-clay material."""

STYLE_REFERENCE_IMAGE_1 = """Reference image 1 is the sole project-style reference. Transfer only its rendering medium, facial rendering treatment, age-appropriate skin finish, hair and cloth response, restrained material detail, palette relationships, physical light character, atmospheric restraint, and production finish. Do not copy any depicted person, scenery, prop, action, pose, environment, or composition."""

STYLE_REFERENCE_IMAGE_2 = STYLE_REFERENCE_IMAGE_1.replace("Reference image 1", "Reference image 2")


@dataclass
class LabContext:
    lab_dir: Path
    experiment: dict[str, Any]
    input_manifest: dict[str, Any]
    config_path: Path
    key_vision_path: Path
    key_vision_hash: str
    spatial_template_path: Path
    spatial_template_hash: str


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


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_text_immutable(path: Path, value: str) -> bool:
    normalized = value if value.endswith("\n") else value + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != normalized:
            raise FileExistsError(f"Refusing to mutate immutable artifact: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8", newline="\n")
    return True


def write_json_immutable(path: Path, value: Any) -> bool:
    return write_text_immutable(path, json_text(value))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def ensure_under(path: Path, parent: Path, label: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"{label} must remain under {parent}: {path}") from exc


def manifest_record(manifest: dict[str, Any], logical_role: str) -> dict[str, Any]:
    matches = [row for row in manifest.get("files", []) if row.get("logical_role") == logical_role]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {logical_role!r} input record, found {len(matches)}")
    return matches[0]


def source_config_for_runtime(ctx: LabContext) -> Path:
    """Load provider credentials from the configured source without copying secrets into the lab."""
    record = manifest_record(ctx.input_manifest, "active_config")
    source = Path(str(record.get("source_path") or "")).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Active source config is missing: {source}")
    expected_hash = str(record.get("sha256") or "")
    if not expected_hash or sha256(source) != expected_hash:
        raise ValueError(f"Active source config changed after lab initialization: {source}")
    return source


def verify_runtime_dependency_snapshot(ctx: LabContext, logical_role: str) -> None:
    """Ensure non-secret config dependencies still match their frozen lab snapshots."""
    record = manifest_record(ctx.input_manifest, logical_role)
    source = Path(str(record.get("source_path") or "")).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"{logical_role} source is missing: {source}")
    expected_hash = str(record.get("sha256") or "")
    if not expected_hash or sha256(source) != expected_hash:
        raise ValueError(f"{logical_role} source changed after lab initialization: {source}")


def verify_snapshot(record: dict[str, Any], lab_dir: Path) -> tuple[Path, str]:
    path = Path(str(record.get("snapshot_path") or "")).resolve()
    ensure_under(path, (lab_dir / "inputs").resolve(), "input snapshot")
    if not path.is_file():
        raise FileNotFoundError(path)
    expected_hash = str(record.get("sha256") or "")
    actual_hash = sha256(path)
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError(f"Frozen input hash mismatch: {path}")
    return path, actual_hash


def load_lab(lab_dir: Path, *, allow_invalidated: bool = False) -> LabContext:
    lab_dir = lab_dir.resolve()
    ensure_under(lab_dir, (ROOT / ".tmp").resolve(), "lab directory")
    experiment = read_json(lab_dir / "experiment.json")
    status = str(experiment.get("status") or "")
    if status.startswith("invalidated") and not allow_invalidated:
        reason = (experiment.get("invalidation") or {}).get("reason")
        raise ValueError(f"Lab is invalidated and cannot be used: {status}; {reason or 'no reason recorded'}")

    manifest = read_json(lab_dir / "inputs" / "manifest.json")
    config_path, _config_hash = verify_snapshot(manifest_record(manifest, "active_config"), lab_dir)
    key_vision_path, key_vision_hash = verify_snapshot(manifest_record(manifest, "key_vision"), lab_dir)
    spatial_path, spatial_hash = verify_snapshot(manifest_record(manifest, "spatial_template"), lab_dir)
    if key_vision_path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported key-vision image type: {key_vision_path}")

    selection = experiment.get("key_vision_selection")
    if not isinstance(selection, dict):
        raise ValueError("Lab lacks explicit key_vision_selection provenance; initialize a new lab")
    basis = str(selection.get("selection_basis") or "")
    if basis not in {"user_confirmed", "promotion_record"}:
        raise ValueError(f"Invalid key-vision selection basis: {basis!r}")
    if str(selection.get("sha256") or "") != key_vision_hash:
        raise ValueError("Key-vision selection hash does not match the frozen snapshot")
    if basis == "promotion_record":
        verify_snapshot(manifest_record(manifest, "key_vision_selection_record"), lab_dir)

    return LabContext(
        lab_dir=lab_dir,
        experiment=experiment,
        input_manifest=manifest,
        config_path=config_path,
        key_vision_path=key_vision_path,
        key_vision_hash=key_vision_hash,
        spatial_template_path=spatial_path,
        spatial_template_hash=spatial_hash,
    )


def frozen_contracts(ctx: LabContext) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = read_json(ctx.lab_dir / "contracts" / "appearances.json")
    appearances = payload.get("appearances")
    if not isinstance(appearances, list) or not appearances:
        raise ValueError("contracts/appearances.json contains no appearances")
    pending: list[str] = []
    for item in appearances:
        key = str(item.get("appearance_key") or "")
        contract = str(item.get("character_contract") or "").strip()
        decisions = item.get("design_decisions")
        incomplete_decisions = [name for name, value in (decisions or {}).items() if not str(value).strip()]
        if item.get("review_status") != "frozen" or not contract or not isinstance(decisions, dict) or incomplete_decisions:
            pending.append(key or "<unnamed>")
    matrix = read_json(ctx.lab_dir / "contracts" / "cast-contrast-matrix.json")
    for pair in matrix.get("pairs", []):
        if pair.get("review_status") != "frozen":
            pending.append(f"pair:{pair.get('left')} vs {pair.get('right')}")
    if pending:
        raise ValueError("Character contracts/cast matrix are not frozen: " + ", ".join(pending))
    return appearances, matrix


def config_controls(config_path: Path) -> dict[str, str]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    binding = ((raw.get("nodes") or {}).get("roleboard_image_generation") or {})
    model_route = str(binding.get("model") or "")
    if ":" not in model_route:
        raise ValueError("roleboard_image_generation model route must be provider:model")
    provider, model = model_route.split(":", 1)
    params = binding.get("params") or {}
    provider_options = (((raw.get("providers") or {}).get(provider) or {}).get("options") or {})
    size = str(params.get("size") or provider_options.get("roleboard_size") or provider_options.get("size") or "auto")
    quality = str(params.get("quality") or provider_options.get("roleboard_quality") or provider_options.get("quality") or "auto")
    return {"provider": provider, "model": model, "size": size, "quality": quality}


def appearance_slug(appearance_key: str) -> str:
    readable = re.sub(r"[^0-9A-Za-z_-]+", "-", appearance_key).strip("-").lower()
    digest = hashlib.sha256(appearance_key.encode("utf-8")).hexdigest()[:8]
    return f"{readable or 'appearance'}-{digest}"


def render_prompt(candidate_id: str, character_contract: str) -> str:
    common = COMMON_CHARACTER_AND_BOARD.format(character_contract=character_contract.strip())
    if candidate_id == "p1-r00-current-v1":
        return common + "\n\n保持参考图的水墨二维国漫渲染、光影与色彩处理。\n只生成一张干净的 16:9 单角色身份板；避免身份漂移、重复主体、畸形肢体、文字、logo 和水印。"
    if candidate_id == "p1-r00-conflict-free-v1":
        return common + "\n\n保持参考图的项目既定渲染媒介、光影、材质与色彩处理，不得改成水墨二维或真人摄影。\n只生成一张干净的 16:9 单角色身份板；避免身份漂移、重复主体、畸形肢体、文字、logo 和水印。"
    if candidate_id == "p1-r00-spatial-style-explicit-v1":
        return "\n\n".join([SPATIAL_REFERENCE_CLAUSE, STYLE_REFERENCE_IMAGE_2, common])
    if candidate_id == "p1-r00-spatial-only-control-v1":
        return "\n\n".join(
            [
                SPATIAL_REFERENCE_CLAUSE,
                "No project-style image is supplied in this control. Do not adopt the white-clay material or warrior design from reference image 1; render the character contract cleanly with restrained neutral 3D materials.",
                common,
            ]
        )
    if candidate_id == "p1-r00-style-only-control-v1":
        return "\n\n".join([STYLE_REFERENCE_IMAGE_1, common])
    raise KeyError(candidate_id)


def input_ref(ctx: LabContext, logical_role: str) -> dict[str, Any]:
    if logical_role == "key_vision":
        path, digest = ctx.key_vision_path, ctx.key_vision_hash
    elif logical_role == "spatial_template":
        path, digest = ctx.spatial_template_path, ctx.spatial_template_hash
    else:
        raise KeyError(logical_role)
    return {
        "kind": "input",
        "logical_role": logical_role,
        "path": str(path),
        "sha256": digest,
    }


def candidate_refs(
    ctx: LabContext,
    candidate: CandidateSpec,
    appearance_key: str,
    *,
    anchor_key: str,
    anchor_sample_id: str,
) -> list[dict[str, Any]]:
    policy = candidate.reference_policy
    if policy == "key_vision_for_anchor_then_cross_role_anchor":
        if appearance_key == anchor_key:
            return [input_ref(ctx, "key_vision")]
        return [
            {
                "kind": "sample",
                "logical_role": "cross_role_anchor",
                "sample_id": anchor_sample_id,
                "path": f"images/exploration/{anchor_sample_id}.png",
            }
        ]
    if policy == "spatial_template_then_key_vision_independent_base":
        return [input_ref(ctx, "spatial_template"), input_ref(ctx, "key_vision")]
    if policy == "spatial_template_only_independent_base":
        return [input_ref(ctx, "spatial_template")]
    if policy == "key_vision_only_independent_base":
        return [input_ref(ctx, "key_vision")]
    raise KeyError(policy)


def prepare_phase1_round0(ctx: LabContext) -> dict[str, Any]:
    appearances, _matrix = frozen_contracts(ctx)
    by_key = {str(item["appearance_key"]): item for item in appearances}
    missing = [key for key in DEFAULT_EXPLORATION_KEYS if key not in by_key]
    if missing:
        raise ValueError("Missing default exploration appearances: " + ", ".join(missing))
    exploration = [by_key[key] for key in DEFAULT_EXPLORATION_KEYS]
    anchor = next(
        (item for item in exploration if str(item.get("role_tier") or "").lower() == "primary"),
        exploration[0],
    )
    anchor_key = str(anchor["appearance_key"])
    controls = config_controls(ctx.config_path)
    batch_items: list[dict[str, Any]] = []
    created_any = False

    for candidate in CANDIDATES:
        wrapper_text = render_prompt(candidate.candidate_id, "{{character_contract}}")
        candidate_dir = ctx.lab_dir / "candidates" / "image_generation" / candidate.candidate_id
        wrapper_path = candidate_dir / "wrapper.md"
        created_any |= write_text_immutable(wrapper_path, wrapper_text)
        if candidate.candidate_id == "p1-r00-current-v1":
            candidate_status = "incumbent_control"
        elif "-control-" in candidate.candidate_id:
            candidate_status = "diagnostic_control"
        else:
            candidate_status = "challenger"
        candidate_manifest = {
            "candidate_id": candidate.candidate_id,
            "phase": "image_generation",
            "round": "p1-r00",
            "parent_id": None,
            "status": candidate_status,
            "changed_family": candidate.changed_family,
            "hypothesis": candidate.hypothesis,
            "frozen_controls": {
                **controls,
                "n": 1,
                "reference_policy": candidate.reference_policy,
                "key_vision_sha256": ctx.key_vision_hash,
                "spatial_template_sha256": ctx.spatial_template_hash,
            },
            "files": [{"path": "wrapper.md", "sha256": sha256(wrapper_path)}],
            "created_at": ctx.experiment.get("created_at"),
        }
        created_any |= write_json_immutable(candidate_dir / "candidate.json", candidate_manifest)

        anchor_sample_id = f"{candidate.candidate_id}--{appearance_slug(anchor_key)}--r01"
        ordered_appearances = [anchor, *[item for item in exploration if item is not anchor]]
        for appearance in ordered_appearances:
            appearance_key = str(appearance["appearance_key"])
            sample_id = f"{candidate.candidate_id}--{appearance_slug(appearance_key)}--r01"
            prompt = render_prompt(candidate.candidate_id, str(appearance["character_contract"]))
            prompt_path = ctx.lab_dir / "batches" / "prompts" / f"{sample_id}.txt"
            created_any |= write_text_immutable(prompt_path, prompt)
            refs = candidate_refs(
                ctx,
                candidate,
                appearance_key,
                anchor_key=anchor_key,
                anchor_sample_id=anchor_sample_id,
            )
            batch_items.append(
                {
                    "sample_id": sample_id,
                    "candidate_id": candidate.candidate_id,
                    "phase": "image_generation",
                    "round": "p1-r00",
                    "split": "exploration",
                    "role_name": appearance["role_name"],
                    "appearance_name": appearance["appearance_name"],
                    "appearance_key": appearance_key,
                    "replicate_id": "r01",
                    "prompt_path": str(prompt_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                    "prompt_sha256": sha256(prompt_path),
                    "ordered_references": refs,
                    "provider": controls["provider"],
                    "model": controls["model"],
                    "size": controls["size"],
                    "quality": controls["quality"],
                    "n": 1,
                    "expected_output_path": f"images/exploration/{sample_id}.png",
                }
            )

    batch = {
        "schema_version": 1,
        "batch_id": "p1-r00-exploration-v1",
        "phase": "image_generation",
        "round": "p1-r00",
        "split": "exploration",
        "created_at": ctx.experiment.get("created_at"),
        "candidate_ids": [item.candidate_id for item in CANDIDATES],
        "exploration_appearances": list(DEFAULT_EXPLORATION_KEYS),
        "anchor_appearance": anchor_key,
        "frozen_controls": {
            **controls,
            "n": 1,
            "key_vision_sha256": ctx.key_vision_hash,
            "spatial_template_sha256": ctx.spatial_template_hash,
        },
        "items": batch_items,
    }
    batch_path = ctx.lab_dir / "batches" / "p1-r00-exploration-v1.json"
    batch_created = write_json_immutable(batch_path, batch)
    created_any |= batch_created
    if batch_created:
        append_jsonl(
            ctx.lab_dir / "ledger.jsonl",
            {
                "event": "phase_plan_created",
                "phase": "image_generation",
                "round": "p1-r00",
                "batch_id": batch["batch_id"],
                "sample_count": len(batch_items),
                "batch_sha256": sha256(batch_path),
                "at": utc_now(),
            },
        )
    return {
        "lab": str(ctx.lab_dir),
        "batch": str(batch_path),
        "created": created_any,
        "candidate_count": len(CANDIDATES),
        "sample_count": len(batch_items),
        "paid_calls_dispatched": 0,
    }


def completed_by_sample(ctx: LabContext) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ctx.lab_dir / "results.jsonl"):
        sample_id = str(row.get("sample_id") or "")
        if sample_id and row.get("status") == "success":
            completed[sample_id] = row
            replacement_for = str(row.get("replacement_for") or "")
            if replacement_for:
                aliased = dict(row)
                aliased["sample_id"] = replacement_for
                aliased["replacement_sample_id"] = sample_id
                completed[replacement_for] = aliased
    return completed


def latest_result_by_sample(ctx: LabContext) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ctx.lab_dir / "results.jsonl"):
        sample_id = str(row.get("sample_id") or "")
        if sample_id:
            latest[sample_id] = row
    return latest


def prepare_independent_replacement(ctx: LabContext, original_sample_id: str) -> dict[str, Any]:
    """Create an immutable independent replacement batch after two transport failures."""
    batch_path = ctx.lab_dir / "batches" / "p1-r00-exploration-v1.json"
    batch = read_json(batch_path)
    matches = [item for item in batch.get("items", []) if str(item.get("sample_id") or "") == original_sample_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one original batch item for independent replacement, found {len(matches)}")
    history = [
        row
        for row in read_jsonl(ctx.lab_dir / "results.jsonl")
        if str(row.get("sample_id") or "") == original_sample_id
    ]
    if len(history) < 2 or any(row.get("status") != "transport_failure" for row in history):
        raise ValueError("Independent replacement requires at least two recorded transport failures")
    original = matches[0]
    replacement_id = f"{original_sample_id}--independent-r{len(history) + 1:02d}"
    replacement_item = dict(original)
    replacement_item.update(
        {
            "sample_id": replacement_id,
            "replicate_id": f"r{len(history) + 1:02d}",
            "split": "exploration_independent_replacement",
            "replacement_for": original_sample_id,
            "replacement_reason": "Two identical transport failures; one independent sample is authorized because role coverage is incomplete.",
            "expected_output_path": f"images/exploration/{replacement_id}.png",
        }
    )
    replacement_batch = {
        "schema_version": 1,
        "batch_id": f"p1-r00-independent-replacement-{appearance_slug(original_sample_id)}",
        "phase": "image_generation",
        "round": "p1-r00",
        "split": "exploration_independent_replacement",
        "created_at": ctx.experiment.get("created_at"),
        "replacement_for": original_sample_id,
        "items": [replacement_item],
    }
    replacement_path = ctx.lab_dir / "batches" / f"{replacement_batch['batch_id']}.json"
    created = write_json_immutable(replacement_path, replacement_batch)
    if created:
        append_jsonl(
            ctx.lab_dir / "ledger.jsonl",
            {
                "event": "phase_plan_created",
                "phase": "image_generation",
                "round": "p1-r00",
                "batch_id": replacement_batch["batch_id"],
                "sample_count": 1,
                "replacement_for": original_sample_id,
                "batch_sha256": sha256(replacement_path),
                "at": utc_now(),
            },
        )
    return {"batch": str(replacement_path), "item": replacement_item, "created": created}


def outstanding_reservations(ctx: LabContext) -> dict[str, dict[str, Any]]:
    reservations: dict[str, dict[str, Any]] = {}
    completed_calls: set[str] = set()
    for row in read_jsonl(ctx.lab_dir / "ledger.jsonl"):
        call_id = str(row.get("call_id") or "")
        if row.get("event") == "reserve" and call_id:
            reservations[call_id] = row
        elif row.get("event") == "complete" and call_id:
            completed_calls.add(call_id)
    return {call_id: row for call_id, row in reservations.items() if call_id not in completed_calls}


def reserved_image_count(ctx: LabContext) -> int:
    return sum(
        int(row.get("count") or 1)
        for row in read_jsonl(ctx.lab_dir / "ledger.jsonl")
        if row.get("event") == "reserve" and row.get("kind") == "image"
    )


def resolve_refs(ctx: LabContext, item: dict[str, Any], completed: dict[str, dict[str, Any]]) -> list[AssetRef]:
    refs: list[AssetRef] = []
    for index, ref in enumerate(item.get("ordered_references") or [], start=1):
        if ref.get("kind") == "input":
            path = Path(str(ref["path"])).resolve()
            if not path.is_file() or sha256(path) != ref.get("sha256"):
                raise ValueError(f"Input reference changed or missing: {path}")
            digest = str(ref["sha256"])
        elif ref.get("kind") == "sample":
            dependency = completed.get(str(ref.get("sample_id") or ""))
            if dependency is None:
                raise ValueError(f"Unresolved sample dependency for {item['sample_id']}: {ref.get('sample_id')}")
            path = (ctx.lab_dir / str(dependency["output_path"])).resolve()
            if not path.is_file() or sha256(path) != dependency.get("output_sha256"):
                raise ValueError(f"Generated reference changed or missing: {path}")
            digest = str(dependency["output_sha256"])
        else:
            raise ValueError(f"Unknown reference kind: {ref}")
        refs.append(
            AssetRef(
                id=f"{item['sample_id']}-ref-{index}",
                type="image",
                path=str(path),
                metadata={
                    "logical_role": ref.get("logical_role"),
                    "sha256": digest,
                    "reference_index": index,
                },
            )
        )
    return refs


def failure_status(exc: Exception) -> str:
    if isinstance(exc, ProviderAuthError):
        return "provider_rejection"
    if isinstance(exc, (ProviderBadResponseError, OSError, TimeoutError)):
        message = str(exc).lower()
        if any(token in message for token in ("safety", "policy", "validation", "invalid prompt")):
            return "provider_rejection"
        return "transport_failure"
    return "provider_rejection"


async def execute_phase1_round0(
    ctx: LabContext,
    *,
    confirmed: bool,
    confirm_transport_replacements: bool = False,
    independent_replacement_sample: str | None = None,
    skip_exhausted_transport_failures: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Paid calls require --confirm-paid-calls")
    if ctx.experiment.get("mode") != "execute":
        raise ValueError("Paid calls require a lab initialized with mode=execute")
    batch_path = ctx.lab_dir / "batches" / "p1-r00-exploration-v1.json"
    if not batch_path.is_file():
        raise FileNotFoundError("Prepare Phase 1 Round 0 before execution")
    completed = completed_by_sample(ctx)
    unresolved = outstanding_reservations(ctx)
    if unresolved:
        raise ValueError(
            "Lab has unresolved paid-call reservations; audit them before dispatching replacements: "
            + ", ".join(unresolved)
        )
    if independent_replacement_sample:
        replacement_batch_path = (
            ctx.lab_dir
            / "batches"
            / f"p1-r00-independent-replacement-{appearance_slug(independent_replacement_sample)}.json"
        )
        if not replacement_batch_path.is_file():
            raise FileNotFoundError(
                "Prepare the independent replacement batch before execution: "
                + str(replacement_batch_path)
            )
        batch = read_json(replacement_batch_path)
        pending = list(batch.get("items") or [])
    else:
        batch = read_json(batch_path)
        pending = [item for item in batch.get("items", []) if item.get("sample_id") not in completed]
        if not pending:
            return {"lab": str(ctx.lab_dir), "status": "already_complete", "successful_samples": len(completed)}
        latest_results = latest_result_by_sample(ctx)
        previous_failures = {
            str(item["sample_id"]): latest_results.get(str(item["sample_id"]))
            for item in pending
        }
        previous_failures = {
            sample_id: row
            for sample_id, row in previous_failures.items()
            if row is not None and row.get("status") != "success"
        }
        retryable_transport_failures = {
            sample_id: row
            for sample_id, row in previous_failures.items()
            if row.get("status") == "transport_failure"
        }
        non_retryable_failures = {
            sample_id: row
            for sample_id, row in previous_failures.items()
            if row.get("status") != "transport_failure"
        }
        if non_retryable_failures:
            raise ValueError(
                "Failed samples require a separately authorized replacement plan; automatic redispatch is forbidden: "
                + ", ".join(f"{sample_id}={row.get('status')}" for sample_id, row in non_retryable_failures.items())
            )
        exhausted_transport_failures = {
            sample_id
            for sample_id in retryable_transport_failures
            if sum(
                1
                for row in read_jsonl(ctx.lab_dir / "results.jsonl")
                if str(row.get("sample_id") or "") == sample_id
            ) > 1
        }
        if exhausted_transport_failures and not skip_exhausted_transport_failures:
            raise ValueError(
                "Transport replacement limit reached; prepare an independent replacement and explicitly skip the exhausted sample: "
                + ", ".join(sorted(exhausted_transport_failures))
            )
        if exhausted_transport_failures:
            pending = [item for item in pending if str(item.get("sample_id") or "") not in exhausted_transport_failures]
            retryable_transport_failures = {
                sample_id: row
                for sample_id, row in retryable_transport_failures.items()
                if sample_id not in exhausted_transport_failures
            }
        if retryable_transport_failures and not confirm_transport_replacements:
            raise ValueError(
                "Transport-failed samples require --confirm-transport-replacements for one same-request replacement: "
                + ", ".join(retryable_transport_failures)
            )
    image_budget = int((ctx.experiment.get("budgets") or {}).get("image") or 0)
    used = reserved_image_count(ctx)
    if len(pending) > image_budget - used:
        raise ValueError(f"Pending calls={len(pending)} exceed remaining authorized image budget={image_budget - used}")

    # The lab config is immutable and intentionally excludes credential files.
    # Load the verified source config for runtime secrets while checking the
    # non-secret model catalog against its frozen snapshot first.
    verify_runtime_dependency_snapshot(ctx, "model_catalog")
    settings = load_settings(str(source_config_for_runtime(ctx)))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    provider = router.image("role", node_name="roleboard_image_generation")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    successful_this_run = 0

    for item in pending:
        sample_id = str(item["sample_id"])
        prompt_path = (ctx.lab_dir / str(item["prompt_path"])).resolve()
        ensure_under(prompt_path, ctx.lab_dir, "batch prompt")
        if not prompt_path.is_file() or sha256(prompt_path) != item.get("prompt_sha256"):
            raise ValueError(f"Prompt changed or missing: {prompt_path}")
        refs = resolve_refs(ctx, item, completed)
        call_id = f"img-{uuid.uuid4().hex}"
        append_jsonl(
            ctx.lab_dir / "ledger.jsonl",
            {
                "event": "reserve",
                "call_id": call_id,
                "kind": "image",
                "phase": "image_generation",
                "candidate_id": item["candidate_id"],
                "sample_id": sample_id,
                "count": 1,
                "at": utc_now(),
                **({"replacement_for": item["replacement_for"]} if item.get("replacement_for") else {}),
            },
        )
        try:
            result = await provider.generate_image(
                prompt_path.read_text(encoding="utf-8"),
                refs=refs,
                size=str(item["size"]),
                metadata={
                    "node_name": "roleboard_image_generation",
                    "project_id": ctx.experiment["project_id"],
                    "asset_id": sample_id,
                    "asset_type": "roleboard_experiment",
                    "prompt_asset_type": "roleboard_image_generation_experiment",
                    "prompt_asset_name": sample_id,
                    "model": item["model"],
                    "size": item["size"],
                    "quality": item["quality"],
                    "max_reference_images": len(refs),
                },
            )
            output_path = ctx.lab_dir / str(item["expected_output_path"])
            await media_store.write_first_generated_image(ctx.lab_dir, output_path, result)
            output_hash = sha256(output_path)
            response_path = ctx.lab_dir / "responses" / "image" / f"{sample_id}.json"
            response_payload = {
                "sample_id": sample_id,
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "task_id": result.task_id,
                "image_urls": result.image_urls,
                "usage": result.usage,
                "raw_response": result.raw_response,
            }
            write_json_immutable(response_path, response_payload)
            result_row = {
                "event": "image_result",
                "phase": "image_generation",
                "candidate_id": item["candidate_id"],
                "sample_id": sample_id,
                "status": "success",
                "output_path": str(output_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "output_sha256": output_hash,
                "response_path": str(response_path.relative_to(ctx.lab_dir)).replace("\\", "/"),
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "usage": result.usage,
                "at": utc_now(),
                **({"replacement_for": item["replacement_for"]} if item.get("replacement_for") else {}),
            }
            append_jsonl(ctx.lab_dir / "results.jsonl", result_row)
            append_jsonl(
                ctx.lab_dir / "ledger.jsonl",
                {
                    "event": "complete",
                    "call_id": call_id,
                    "status": "success",
                    "request_id": result.request_id,
                    "output_path": result_row["output_path"],
                    "output_sha256": output_hash,
                    "at": utc_now(),
                },
            )
            completed[sample_id] = result_row
            successful_this_run += 1
            print(f"[roleboard-smoke] {successful_this_run}/{len(pending)} complete: {sample_id}", flush=True)
        except Exception as exc:
            status = failure_status(exc)
            append_jsonl(
                ctx.lab_dir / "results.jsonl",
                {
                    "event": "image_result",
                    "phase": "image_generation",
                    "candidate_id": item["candidate_id"],
                    "sample_id": sample_id,
                    "status": status,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": utc_now(),
                    **({"replacement_for": item["replacement_for"]} if item.get("replacement_for") else {}),
                },
            )
            append_jsonl(
                ctx.lab_dir / "ledger.jsonl",
                {
                    "event": "complete",
                    "call_id": call_id,
                    "status": status,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": utc_now(),
                },
            )
            raise

    return {
        "lab": str(ctx.lab_dir),
        "status": "round_complete",
        "successful_this_run": successful_this_run,
        "successful_total": len(completed),
        "reserved_image_calls": reserved_image_count(ctx),
    }


def inspect_lab(ctx: LabContext) -> dict[str, Any]:
    frozen = True
    contract_error = ""
    try:
        appearances, matrix = frozen_contracts(ctx)
    except ValueError as exc:
        frozen = False
        contract_error = str(exc)
        appearances = (read_json(ctx.lab_dir / "contracts" / "appearances.json").get("appearances") or [])
        matrix = read_json(ctx.lab_dir / "contracts" / "cast-contrast-matrix.json")
    batch_path = ctx.lab_dir / "batches" / "p1-r00-exploration-v1.json"
    return {
        "lab": str(ctx.lab_dir),
        "mode": ctx.experiment.get("mode"),
        "status": ctx.experiment.get("status"),
        "key_vision": str(ctx.key_vision_path),
        "key_vision_sha256": ctx.key_vision_hash,
        "key_vision_selection_basis": ctx.experiment["key_vision_selection"]["selection_basis"],
        "spatial_template": str(ctx.spatial_template_path),
        "appearance_count": len(appearances),
        "cast_pair_count": len(matrix.get("pairs", [])),
        "contracts_frozen": frozen,
        "contract_error": contract_error or None,
        "phase1_round0_prepared": batch_path.is_file(),
        "successful_image_samples": len(completed_by_sample(ctx)),
        "reserved_image_calls": reserved_image_count(ctx),
        "paid_calls_dispatched_by_inspect": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Isolated, ledgered smoke harness for AutoDrama roleboard-node evolution."
    )
    parser.add_argument("--lab-dir", required=True, help="Roleboard lab directory under repository .tmp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect", help="Validate frozen inputs and report readiness without paid calls")
    subparsers.add_parser("prepare-phase1-round0", help="Freeze five Phase-1 diagnostic candidates and 15 calls")
    execute = subparsers.add_parser("execute-phase1-round0", help="Dispatch the prepared paid image batch")
    execute.add_argument(
        "--confirm-paid-calls",
        action="store_true",
        help="Required in addition to mode=execute and a sufficient recorded image-call budget.",
    )
    execute.add_argument(
        "--confirm-transport-replacements",
        action="store_true",
        help="Authorize one same-request replacement for recorded transport failures; history is preserved.",
    )
    execute.add_argument(
        "--independent-replacement-sample",
        help="Dispatch a prepared independent replacement batch for an exhausted transport-failed sample.",
    )
    execute.add_argument(
        "--skip-exhausted-transport-failures",
        action="store_true",
        help="Skip samples whose one same-request transport replacement already failed, preserving them for independent replacement.",
    )
    subparsers.add_parser(
        "prepare-independent-replacement",
        help="Prepare one independent replacement batch after two identical transport failures.",
    ).add_argument("--sample-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = load_lab(Path(args.lab_dir))
    if args.command == "inspect":
        result = inspect_lab(ctx)
    elif args.command == "prepare-phase1-round0":
        result = prepare_phase1_round0(ctx)
    elif args.command == "prepare-independent-replacement":
        result = prepare_independent_replacement(ctx, str(args.sample_id))
    elif args.command == "execute-phase1-round0":
        result = asyncio.run(
            execute_phase1_round0(
                ctx,
                confirmed=bool(args.confirm_paid_calls),
                confirm_transport_replacements=bool(args.confirm_transport_replacements),
                independent_replacement_sample=args.independent_replacement_sample,
                skip_exhausted_transport_failures=bool(args.skip_exhausted_transport_failures),
            )
        )
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
