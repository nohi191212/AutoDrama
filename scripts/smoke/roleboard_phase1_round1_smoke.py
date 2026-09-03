from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import roleboard_node_evolution_smoke as base  # noqa: E402

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402


ROUND = "p1-r01"
BATCH_ID = "p1-r01-exploration-v1"
PARENT = "p1-r00-spatial-style-explicit-v1"
SPLIT = "exploration"
ROLE_KEYS = ("叶凡/base", "柳菡烟/base", "李德海/base")


def safe_progress(message: str) -> None:
    """Do not turn a closed progress pipe into a fake provider failure."""
    try:
        print(message, flush=True)
    except OSError:
        pass

SPATIAL = """Reference image 1 is a spatial white-model guide only. Copy only its one-board layout: exactly three separated, equal-scale, complete full-body views in a horizontal 16:9 frame, ordered front, true profile, back, with a shared foot baseline, similar margins, and a neutral readable stance. Do not copy its male identity, face, body build, crown, ponytail, armor, shoulder pieces, sword, boots, garment construction, ornament, or white-clay material."""
STYLE = """Reference image 2 is the sole project-style reference. Transfer only its cinematic high-finish 3D rendering medium, facial rendering treatment, age-appropriate skin finish, hair and cloth response, restrained material detail, palette relationships, physical light character, atmospheric restraint, and production finish. Do not copy any depicted person, scenery, prop, action, pose, environment, or composition."""
COMMON = """[CHARACTER IDENTITY AND DESIGN]
{character_contract}

Render this one named character three times as the same canonical identity. Preserve the same skull and facial geometry, hair construction, body proportions, garment pattern and layers, footwear, palette, and restrained signature details in all three views. The face must be role-specific and visibly readable, with concrete face length, jaw, cheekbone, brow, eye spacing and shape, nose, mouth, age structure, and restrained asymmetry. Keep the build anatomically plausible for the declared age, sex, occupation, status, and life history.

Use one dominant silhouette idea and at most two supporting signature details. Avoid random fantasy filigree, unnecessary armor, excessive jewelry, glowing trim, ornamental overload, fashion-editorial posing, and mobile-game skin design.

[EXACT OUTPUT GEOMETRY]
Produce one clean horizontal 16:9 single-character identity board, not a collage or concept sheet. Show exactly three separated complete full-body views and no others: left is front view looking straight ahead; center is a true side profile facing image-left; right is back view. All three figures have approximately equal height, share one horizontal foot baseline, keep every hair tip, hand, garment hem, and foot visible, and use a neutral natural standing pose. Arms stay slightly away from the torso so hands, seams, waist construction, and silhouette remain visible. Use an orthographic or long-lens turntable feel with no dramatic perspective, overlap, crop, inset, or extra view.

[CLEAN DELIVERY]
Use a simple low-contrast neutral studio background. No view labels, name, readable text, symbols, watermark, logo, frame lines, portrait close-up, action pose, held prop, extra subject, duplicate limb, or stray object. Layout, identity, anatomy, reference isolation, and role-specific design take priority over decorative beauty."""

VARIANTS: dict[str, tuple[str, str]] = {
    "p1-r01-incumbent-v1": (
        "incumbent-wrapper",
        "The Round 0 reference-policy winner remains the control; no wrapper family is changed.",
    ),
    "p1-r01-layout-proof-v1": (
        "layout-proof-only",
        "Stronger explicit layout/profile/scale clauses should reduce profile drift and baseline/spacing variance without changing identity design.",
    ),
    "p1-r01-identity-proof-v1": (
        "identity-proof-only",
        "Stronger observable face geometry and cross-view identity clauses should improve facial specificity and reduce cast-level genericity without changing layout or wardrobe design.",
    ),
    "p1-r01-restrained-design-v1": (
        "restrained-design-only",
        "Stronger silhouette/material/ornament-budget clauses should preserve role fit and avoid cheap AI/game-skin decoration without changing reference semantics.",
    ),
    "p1-r01-balanced-v1": (
        "balanced-shortest-successful-clauses",
        "A short priority-ordered combination of the three proven clause families should improve the weakest dimensions without prompt bloat.",
    ),
}

CLAUSES: dict[str, str] = {
    "p1-r01-incumbent-v1": "",
    "p1-r01-layout-proof-v1": """[LAYOUT PROOF — HIGHEST PRIORITY]
Before rendering details, lock a single 16:9 board with exactly three full-body subjects. The left subject is a straight front view, the center subject is a true 90-degree side profile facing image-left with only one eye and one ear visible, and the right subject is a straight back view. Keep equal head heights, equal apparent scale, one shared foot baseline, equal panel spacing, and generous margins. Do not turn the profile into a three-quarter angle and do not add a second front or rear angle.""",
    "p1-r01-identity-proof-v1": """[IDENTITY PROOF — HIGHEST PRIORITY]
Make the face geometry observable at board scale: preserve the contract's skull length/width, brow height, eye spacing and asymmetry, nose bridge and tip, lip proportions, jaw angle, hairline, and age structure. These landmarks must agree in front, true profile, and back-view hair/neck construction. Keep one unchanged person across views; do not average the face into a generic attractive oval and do not borrow another cast member's skull, eyes, jaw, or body.""",
    "p1-r01-restrained-design-v1": """[RESTRAINED DESIGN PROOF — HIGHEST PRIORITY]
Use one readable role-specific silhouette and only one or two supporting construction details. Make fabric weight, seams, closures, wear, and footwear physically coherent from front, profile, and back. Prefer quiet material variation and age/status cues over decorative novelty. No random filigree, ornamental repetition, glow, armor-like panels, oversized jewelry, or polished mobile-game costume treatment.""",
    "p1-r01-balanced-v1": """[PRIORITY ORDER]
1) First lock exactly three equal-scale front/profile/back full-body views on one baseline with a true profile.
2) Then preserve concrete skull, brow, eye, nose, mouth, jaw, hairline, and body landmarks across views and keep the target face distinct from the cast.
3) Then use one restrained role silhouette with physically coherent cloth seams, wear, closures, and footwear; avoid decorative clutter.""",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text_immutable(path: Path, value: str) -> None:
    base.write_text_immutable(path, value)


def write_json_immutable(path: Path, value: Any) -> None:
    base.write_json_immutable(path, value)


def prompt_for(candidate_id: str, contract: str) -> str:
    parts = [SPATIAL, STYLE]
    clause = CLAUSES[candidate_id]
    if clause:
        parts.append(clause)
    parts.append(COMMON.format(character_contract=contract.strip()))
    return "\n\n".join(parts)


def prepare(ctx: base.LabContext) -> dict[str, Any]:
    appearances, _matrix = base.frozen_contracts(ctx)
    by_key = {str(item["appearance_key"]): item for item in appearances}
    missing = [key for key in ROLE_KEYS if key not in by_key]
    if missing:
        raise ValueError("Missing exploration appearances: " + ", ".join(missing))
    controls = base.config_controls(ctx.config_path)
    items: list[dict[str, Any]] = []
    for candidate_id, (family, hypothesis) in VARIANTS.items():
        wrapper = prompt_for(candidate_id, "{{character_contract}}")
        candidate_dir = ctx.lab_dir / "candidates" / "image_generation" / candidate_id
        wrapper_path = candidate_dir / "wrapper.md"
        write_text_immutable(wrapper_path, wrapper)
        write_json_immutable(
            candidate_dir / "candidate.json",
            {
                "candidate_id": candidate_id,
                "phase": "image_generation",
                "round": ROUND,
                "parent_id": PARENT,
                "status": "challenger" if candidate_id != "p1-r01-incumbent-v1" else "incumbent_control",
                "changed_family": family,
                "hypothesis": hypothesis,
                "frozen_controls": {
                    **controls,
                    "n": 1,
                    "reference_policy": "spatial_template_then_key_vision_independent_base",
                    "key_vision_sha256": ctx.key_vision_hash,
                    "spatial_template_sha256": ctx.spatial_template_hash,
                },
                "files": [{"path": "wrapper.md", "sha256": sha256(wrapper_path)}],
                "created_at": ctx.experiment.get("created_at"),
            },
        )
        for role_key in ROLE_KEYS:
            appearance = by_key[role_key]
            sample_id = f"{candidate_id}--{base.appearance_slug(role_key)}--r01"
            prompt_path = ctx.lab_dir / "batches" / "prompts" / f"{sample_id}.txt"
            write_text_immutable(prompt_path, prompt_for(candidate_id, str(appearance["character_contract"])))
            refs = [base.input_ref(ctx, "spatial_template"), base.input_ref(ctx, "key_vision")]
            items.append(
                {
                    "sample_id": sample_id,
                    "candidate_id": candidate_id,
                    "phase": "image_generation",
                    "round": ROUND,
                    "split": SPLIT,
                    "role_name": appearance["role_name"],
                    "appearance_name": appearance["appearance_name"],
                    "appearance_key": role_key,
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
        "batch_id": BATCH_ID,
        "phase": "image_generation",
        "round": ROUND,
        "split": SPLIT,
        "created_at": ctx.experiment.get("created_at"),
        "parent_candidate": PARENT,
        "candidate_ids": list(VARIANTS),
        "exploration_appearances": list(ROLE_KEYS),
        "frozen_controls": {
            **controls,
            "n": 1,
            "key_vision_sha256": ctx.key_vision_hash,
            "spatial_template_sha256": ctx.spatial_template_hash,
        },
        "items": items,
    }
    batch_path = ctx.lab_dir / "batches" / f"{BATCH_ID}.json"
    created = not batch_path.exists()
    write_json_immutable(batch_path, batch)
    if created:
        base.append_jsonl(
            ctx.lab_dir / "ledger.jsonl",
            {
                "event": "phase_plan_created",
                "phase": "image_generation",
                "round": ROUND,
                "batch_id": BATCH_ID,
                "sample_count": len(items),
                "batch_sha256": sha256(batch_path),
                "at": base.utc_now(),
            },
        )
    return {"lab": str(ctx.lab_dir), "batch": str(batch_path), "candidate_count": len(VARIANTS), "sample_count": len(items), "created": created}


def failure_status(exc: Exception) -> str:
    if isinstance(exc, ProviderAuthError):
        return "provider_rejection"
    if isinstance(exc, (ProviderBadResponseError, OSError, TimeoutError)):
        text = str(exc).lower()
        return "provider_rejection" if any(word in text for word in ("safety", "policy", "validation", "invalid prompt")) else "transport_failure"
    return "provider_rejection"


async def execute(ctx: base.LabContext, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Paid calls require --confirm-paid-calls")
    if ctx.experiment.get("mode") != "execute":
        raise ValueError("Paid calls require mode=execute")
    batch_path = ctx.lab_dir / "batches" / f"{BATCH_ID}.json"
    batch = base.read_json(batch_path)
    completed = base.completed_by_sample(ctx)
    pending = [item for item in batch["items"] if item["sample_id"] not in completed]
    outstanding = base.outstanding_reservations(ctx)
    if outstanding:
        raise ValueError("Unresolved paid reservations exist: " + ", ".join(outstanding))
    image_budget = int((ctx.experiment.get("budgets") or {}).get("image") or 0)
    used = base.reserved_image_count(ctx)
    if len(pending) > image_budget - used:
        raise ValueError(f"Pending calls={len(pending)} exceed remaining image budget={image_budget-used}")

    base.verify_runtime_dependency_snapshot(ctx, "model_catalog")
    settings = load_settings(str(base.source_config_for_runtime(ctx)))
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(ctx.lab_dir)
    provider = router.image("role", node_name="roleboard_image_generation")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    success = 0
    for item in pending:
        sample_id = str(item["sample_id"])
        prompt_path = (ctx.lab_dir / str(item["prompt_path"])).resolve()
        if not prompt_path.is_file() or sha256(prompt_path) != item["prompt_sha256"]:
            raise ValueError(f"Prompt changed or missing: {prompt_path}")
        refs = base.resolve_refs(ctx, item, completed)
        call_id = f"img-{uuid.uuid4().hex}"
        base.append_jsonl(ctx.lab_dir / "ledger.jsonl", {"event": "reserve", "call_id": call_id, "kind": "image", "phase": "image_generation", "candidate_id": item["candidate_id"], "sample_id": sample_id, "count": 1, "at": base.utc_now()})
        try:
            result = await provider.generate_image(
                prompt_path.read_text(encoding="utf-8"),
                refs=refs,
                size=str(item["size"]),
                metadata={"node_name": "roleboard_image_generation", "project_id": ctx.experiment["project_id"], "asset_id": sample_id, "asset_type": "roleboard_experiment", "prompt_asset_type": "roleboard_image_generation_experiment", "prompt_asset_name": sample_id, "model": item["model"], "size": item["size"], "quality": item["quality"], "max_reference_images": len(refs)},
            )
            output_path = ctx.lab_dir / str(item["expected_output_path"])
            await media_store.write_first_generated_image(ctx.lab_dir, output_path, result)
            response_path = ctx.lab_dir / "responses" / "image" / f"{sample_id}.json"
            base.write_json_immutable(response_path, {"sample_id": sample_id, "provider": result.provider, "model": result.model, "request_id": result.request_id, "task_id": result.task_id, "image_urls": result.image_urls, "usage": result.usage, "raw_response": result.raw_response})
            row = {"event": "image_result", "phase": "image_generation", "candidate_id": item["candidate_id"], "sample_id": sample_id, "status": "success", "output_path": str(output_path.relative_to(ctx.lab_dir)).replace("\\", "/"), "output_sha256": sha256(output_path), "response_path": str(response_path.relative_to(ctx.lab_dir)).replace("\\", "/"), "provider": result.provider, "model": result.model, "request_id": result.request_id, "usage": result.usage, "at": base.utc_now()}
            base.append_jsonl(ctx.lab_dir / "results.jsonl", row)
            base.append_jsonl(ctx.lab_dir / "ledger.jsonl", {"event": "complete", "call_id": call_id, "status": "success", "request_id": result.request_id, "output_path": row["output_path"], "output_sha256": row["output_sha256"], "at": base.utc_now()})
            completed[sample_id] = row
            success += 1
            safe_progress(f"[roleboard-r01] {success}/{len(pending)} complete: {sample_id}")
        except Exception as exc:
            status = failure_status(exc)
            base.append_jsonl(ctx.lab_dir / "results.jsonl", {"event": "image_result", "phase": "image_generation", "candidate_id": item["candidate_id"], "sample_id": sample_id, "status": status, "error_type": type(exc).__name__, "error": str(exc), "at": base.utc_now()})
            base.append_jsonl(ctx.lab_dir / "ledger.jsonl", {"event": "complete", "call_id": call_id, "status": status, "error_type": type(exc).__name__, "error": str(exc), "at": base.utc_now()})
            raise
    return {"lab": str(ctx.lab_dir), "status": "round_complete", "successful_this_run": success, "successful_total": len(base.completed_by_sample(ctx)), "reserved_image_calls": base.reserved_image_count(ctx)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 Round 1 roleboard smoke harness")
    parser.add_argument("--lab-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    run = sub.add_parser("execute")
    run.add_argument("--confirm-paid-calls", action="store_true")
    args = parser.parse_args()
    ctx = base.load_lab(Path(args.lab_dir))
    if args.command == "prepare":
        value = prepare(ctx)
    else:
        value = asyncio.run(execute(ctx, confirmed=bool(args.confirm_paid_calls)))
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
