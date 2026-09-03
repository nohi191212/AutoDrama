from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment diagnostic
    raise SystemExit("PyYAML is required. Run this script with the AutoDrama Python environment.") from exc


FORBIDDEN_TRANSIENT_STATE = [
    "pose",
    "emotion",
    "injury",
    "held_props",
    "action",
    "energy_state",
    "event_only_damage",
    "event_refs",
]

DESIGNABLE_AXES = [
    "face_and_skull_geometry",
    "jaw_cheek_brow_eye_nose_mouth_geometry",
    "restrained_facial_asymmetry",
    "body_build_and_proportion",
    "neutral_posture",
    "hairline_mass_and_construction",
    "garment_silhouette_and_layer_construction",
    "material_palette_and_footwear",
    "one_dominant_and_up_to_two_supporting_signature_details",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def image_metadata(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from PIL import Image

        with Image.open(path) as image:
            result = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "format": image.format,
            }
    except Exception as exc:  # retain hash even when Pillow cannot inspect
        result["inspection_warning"] = f"{type(exc).__name__}: {exc}"
    return result


def file_record(logical_role: str, source: Path, snapshot: Path, *, required: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "logical_role": logical_role,
        "source_path": str(source),
        "snapshot_path": str(snapshot),
        "required": required,
        "bytes": snapshot.stat().st_size,
        "sha256": sha256(snapshot),
    }
    if snapshot.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        record["image"] = image_metadata(snapshot)
    return record


def appearances_contract(role_finalize: dict[str, Any]) -> list[dict[str, Any]]:
    roles = role_finalize.get("final_roles")
    if not isinstance(roles, list) or not roles:
        raise ValueError("role_finalize.json has no final_roles")

    result: list[dict[str, Any]] = []
    bases_by_role: dict[str, set[str]] = {}
    for role in roles:
        role_name = str(role.get("name") or "").strip()
        assets = role.get("appearance_assets") or []
        if not role_name or not isinstance(assets, list) or not assets:
            raise ValueError(f"Role has no structured appearance assets: {role_name or role!r}")
        bases_by_role[role_name] = {
            str(asset.get("name") or "base").strip()
            for asset in assets
            if str(asset.get("asset_role") or "base").strip().lower() == "base"
        }
        if not bases_by_role[role_name]:
            raise ValueError(f"Role has no base appearance: {role_name}")

    for role in roles:
        role_name = str(role.get("name") or "").strip()
        for asset in role.get("appearance_assets") or []:
            appearance_name = str(asset.get("name") or "base").strip() or "base"
            asset_role = str(asset.get("asset_role") or "base").strip().lower()
            reference_name = str(asset.get("reference_asset_name") or "").strip() or None
            if asset_role == "variant" and reference_name not in bases_by_role[role_name]:
                raise ValueError(
                    f"Variant {role_name}/{appearance_name} references missing base {reference_name!r}"
                )
            result.append(
                {
                    "role_name": role_name,
                    "role_tier": role.get("role_tier"),
                    "appearance_name": appearance_name,
                    "appearance_key": f"{role_name}/{appearance_name}",
                    "asset_role": asset_role,
                    "reference_asset_name": reference_name,
                    "hard_facts": {
                        "identity_invariants": list(asset.get("identity_invariants") or []),
                        "wardrobe": list(asset.get("wardrobe") or []),
                        "time_period": asset.get("time_period"),
                        "age_band": asset.get("age_band"),
                        "valid_from_event": asset.get("valid_from_event"),
                        "valid_to_event": asset.get("valid_to_event"),
                    },
                    "design_decisions": {axis: "" for axis in DESIGNABLE_AXES},
                    "character_contract": "",
                    "designable_axes_pending_review": DESIGNABLE_AXES,
                    "forbidden_transient_state": FORBIDDEN_TRANSIENT_STATE,
                    "provenance": asset.get("provenance") or {},
                    "review_status": "pending_human_contract_review",
                }
            )
    return result


def contrast_matrix(appearances: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(appearances):
        for right in appearances[index + 1 :]:
            pairs.append(
                {
                    "left": left["appearance_key"],
                    "right": right["appearance_key"],
                    "same_role_variant_pair": left["role_name"] == right["role_name"],
                    "face_geometry_difference_or_lineage": [],
                    "body_and_posture_difference_or_lineage": [],
                    "hair_silhouette_difference_or_lineage": [],
                    "wardrobe_silhouette_difference_or_lineage": [],
                    "shared_world_invariants": [],
                    "review_status": "pending_human_design",
                }
            )
    return {"schema_version": 1, "pairs": pairs}


def copy_snapshot(
    logical_role: str,
    source: Path,
    destination: Path,
    manifest: list[dict[str, Any]],
    *,
    required: bool,
) -> None:
    if not source.is_file():
        if required:
            raise FileNotFoundError(f"Required input is missing: {source}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    manifest.append(file_record(logical_role, source, destination, required=required))


def ensure_within(path: Path, parent: Path, label: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"{label} must remain under {parent}: {path}") from exc


def resolve_config_dependency(raw_value: Any, config_path: Path) -> Path | None:
    """Resolve a non-secret file referenced by the active YAML config."""
    if raw_value is None or not str(raw_value).strip():
        return None
    value = Path(str(raw_value))
    if not value.is_absolute():
        value = config_path.parent / value
    return value.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a reproducible AutoDrama roleboard node lab")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default="saodi.yaml")
    parser.add_argument("--project-id")
    parser.add_argument("--project-dir")
    parser.add_argument(
        "--contract-bundle",
        help="Optional directory containing frozen appearances.json and cast-contrast-matrix.json to snapshot into the lab.",
    )
    parser.add_argument(
        "--key-vision",
        required=True,
        help=(
            "User-confirmed current locked key-vision image, as an absolute path or a path relative to the "
            "repository root. The script intentionally does not infer this from key_vision_original.png."
        ),
    )
    parser.add_argument(
        "--key-vision-selection-basis",
        required=True,
        choices=("user_confirmed", "promotion_record"),
        help="Why this exact image is authoritative for the new lab.",
    )
    parser.add_argument(
        "--key-vision-selection-record",
        help="Required with promotion_record: the absolute or repository-relative promotion/selection record.",
    )
    parser.add_argument("--lab-dir")
    parser.add_argument("--mode", choices=("design", "execute", "resume", "promote"), default="design")
    parser.add_argument("--budget-image", type=int, default=96)
    parser.add_argument("--budget-edit", type=int, default=16)
    parser.add_argument("--budget-gemini", type=int, default=128)
    parser.add_argument("--budget-audit", type=int, default=96)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    config_path = (repo_root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config).resolve()
    ensure_within(config_path, repo_root, "config")
    if not config_path.is_file():
        raise FileNotFoundError(config_path)

    settings = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    project_id = str(args.project_id or settings.get("project", {}).get("id") or "").strip()
    if not project_id:
        raise ValueError("Project ID is missing from both arguments and config")

    if args.project_dir:
        project_dir = Path(args.project_dir)
        if not project_dir.is_absolute():
            project_dir = repo_root / project_dir
        project_dir = project_dir.resolve()
    else:
        output_root = Path(str(settings.get("output", {}).get("root_dir") or "outputs"))
        if not output_root.is_absolute():
            output_root = repo_root / output_root
        project_dir = (output_root / project_id).resolve()
    ensure_within(project_dir, repo_root, "project directory")

    key_vision_path = Path(args.key_vision)
    if not key_vision_path.is_absolute():
        key_vision_path = repo_root / key_vision_path
    key_vision_path = key_vision_path.resolve()
    if not key_vision_path.is_file():
        raise FileNotFoundError(f"Explicit key-vision image does not exist: {key_vision_path}")

    selection_record_path: Path | None = None
    if args.key_vision_selection_record:
        selection_record_path = Path(args.key_vision_selection_record)
        if not selection_record_path.is_absolute():
            selection_record_path = repo_root / selection_record_path
        selection_record_path = selection_record_path.resolve()
        if not selection_record_path.is_file():
            raise FileNotFoundError(f"Key-vision selection record does not exist: {selection_record_path}")
    if args.key_vision_selection_basis == "promotion_record" and selection_record_path is None:
        raise ValueError("--key-vision-selection-record is required when selection basis is promotion_record")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_lab = repo_root / ".tmp" / f"roleboard-node-evolution-{project_id}-{timestamp}"
    lab_dir = Path(args.lab_dir).resolve() if args.lab_dir else default_lab.resolve()
    ensure_within(lab_dir, (repo_root / ".tmp").resolve(), "lab directory")
    if lab_dir.exists():
        raise FileExistsError(f"Refusing to overwrite an existing lab: {lab_dir}")

    role_finalize_path = project_dir / "assets" / "json" / "nodes" / "role_finalize.json"
    spatial_template_path = repo_root / ".assets" / "image_templates" / "roleboard_template.png"
    role_finalize = load_json(role_finalize_path)
    contracts = appearances_contract(role_finalize)

    contract_bundle_path: Path | None = None
    contract_bundle_files: dict[str, dict[str, Any]] = {}
    if args.contract_bundle:
        contract_bundle_path = Path(args.contract_bundle)
        if not contract_bundle_path.is_absolute():
            contract_bundle_path = repo_root / contract_bundle_path
        contract_bundle_path = contract_bundle_path.resolve()
        ensure_within(contract_bundle_path, (repo_root / ".tmp").resolve(), "contract bundle")
        if not contract_bundle_path.is_dir():
            raise FileNotFoundError(f"Contract bundle directory does not exist: {contract_bundle_path}")
        bundle_appearances = contract_bundle_path / "appearances.json"
        bundle_matrix = contract_bundle_path / "cast-contrast-matrix.json"
        if not bundle_appearances.is_file() or not bundle_matrix.is_file():
            raise FileNotFoundError(
                "Contract bundle must contain appearances.json and cast-contrast-matrix.json"
            )
        bundled_payload = load_json(bundle_appearances)
        bundled_appearances = bundled_payload.get("appearances")
        if not isinstance(bundled_appearances, list) or not bundled_appearances:
            raise ValueError("Contract bundle appearances.json has no appearances")
        bundled_matrix = load_json(bundle_matrix)
        if not isinstance(bundled_matrix.get("pairs"), list):
            raise ValueError("Contract bundle cast-contrast-matrix.json has no pairs list")
        contracts = bundled_appearances
        contract_bundle_files = {
            "appearances": {"source_path": str(bundle_appearances), "sha256": sha256(bundle_appearances)},
            "cast_matrix": {"source_path": str(bundle_matrix), "sha256": sha256(bundle_matrix)},
        }

    required_inputs = [role_finalize_path, spatial_template_path, key_vision_path]
    missing = [path for path in required_inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required input(s): " + ", ".join(str(path) for path in missing))

    directories = [
        "inputs",
        "contracts",
        "candidates/image_generation",
        "candidates/roleboard_prompt",
        "candidates/image_audit",
        "candidates/seeds",
        "batches",
        "responses/gemini",
        "responses/image",
        "responses/audit",
        "images/exploration",
        "images/holdout",
        "images/checkpoint",
        "images/repair",
        "evaluations/rankings",
        "contact_sheets/manifests",
        "contact_sheets/blind",
        "contact_sheets/answer_keys",
        "reports",
        "promotion",
    ]
    for relative in directories:
        (lab_dir / relative).mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    copy_snapshot("active_config", config_path, lab_dir / "inputs" / "config.yaml", manifest, required=True)
    model_catalog_source = resolve_config_dependency(settings.get("model_catalog_file"), config_path)
    if model_catalog_source is not None:
        copy_snapshot(
            "model_catalog",
            model_catalog_source,
            lab_dir / "inputs" / model_catalog_source.name,
            manifest,
            required=bool(settings.get("nodes")),
        )
    copy_snapshot("role_finalize", role_finalize_path, lab_dir / "inputs" / "role_finalize.json", manifest, required=True)
    copy_snapshot("spatial_template", spatial_template_path, lab_dir / "inputs" / "roleboard_template.png", manifest, required=True)
    copy_snapshot("key_vision", key_vision_path, lab_dir / "inputs" / f"key_vision{key_vision_path.suffix.lower()}", manifest, required=True)
    if selection_record_path is not None:
        copy_snapshot(
            "key_vision_selection_record",
            selection_record_path,
            lab_dir / "inputs" / f"key_vision_selection_record{selection_record_path.suffix.lower()}",
            manifest,
            required=True,
        )

    node_dir = project_dir / "assets" / "json" / "nodes"
    optional_snapshots = {
        "roleboard_prompt_baseline": node_dir / "roleboard_prompt.json",
        "key_vision_prompt": node_dir / "key_vision_prompt.json",
        "key_vision_generation": node_dir / "key_vision_image_generation.json",
        "key_vision_audit": node_dir / "key_vision_image_audit.json",
    }
    for logical_role, source in optional_snapshots.items():
        copy_snapshot(logical_role, source, lab_dir / "inputs" / f"{logical_role}.json", manifest, required=False)

    production_relative_paths = [
        "saodi.yaml",
        "model_catalog.yaml.example",
        "autodrama/src/autodrama/workflows/nodes/role_nodes.py",
        "autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py",
        "autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py",
        "autodrama/src/autodrama/services/role_service.py",
        "autodrama/src/autodrama/core/schemas.py",
        "autodrama/src/autodrama/image_audit_rubrics.py",
        "autodrama/src/autodrama/providers/aibox/image/gpt_image.py",
        "autodrama/src/autodrama/prompts/roleboard_prompt/aibox_gpt_image_2_guan.md",
        "autodrama/src/autodrama/prompts/roleboard_prompt/default.md",
        "autodrama/src/autodrama/prompts/image_asset_audit/default.md",
    ]
    production_files: list[dict[str, Any]] = []
    for relative in production_relative_paths:
        source = repo_root / relative
        if source.is_file():
            production_files.append(
                {
                    "path": relative.replace("\\", "/"),
                    "bytes": source.stat().st_size,
                    "sha256": sha256(source),
                }
            )
    write_json(lab_dir / "inputs" / "production-files.json", production_files)
    write_json(lab_dir / "inputs" / "manifest.json", {"created_at": utc_now(), "files": manifest})
    if contract_bundle_path is not None:
        shutil.copy2(contract_bundle_path / "appearances.json", lab_dir / "contracts" / "appearances.json")
        shutil.copy2(
            contract_bundle_path / "cast-contrast-matrix.json",
            lab_dir / "contracts" / "cast-contrast-matrix.json",
        )
    else:
        write_json(lab_dir / "contracts" / "appearances.json", {"schema_version": 1, "appearances": contracts})
        write_json(lab_dir / "contracts" / "cast-contrast-matrix.json", contrast_matrix(contracts))

    skill_root = Path(__file__).resolve().parents[1]
    protocol_source = skill_root / "references" / "experiment-protocol.md"
    if protocol_source.is_file():
        shutil.copy2(protocol_source, lab_dir / "PROTOCOL.md")
    for source in sorted((skill_root / "assets").iterdir()):
        if source.is_file():
            shutil.copy2(source, lab_dir / "candidates" / "seeds" / source.name)

    budgets = {
        "image": args.budget_image,
        "edit": args.budget_edit,
        "gemini": args.budget_gemini,
        "audit": args.budget_audit,
    }
    if any(value < 0 for value in budgets.values()):
        raise ValueError("Budgets must be non-negative")
    experiment = {
        "schema_version": 1,
        "mode": args.mode,
        "status": "preflight_contract_review",
        "project_id": project_id,
        "repo_root": str(repo_root),
        "project_dir": str(project_dir),
        "created_at": utc_now(),
        "budgets": budgets,
        "authoritative_call_record": "ledger.jsonl",
        "current_phase": "preflight",
        "key_vision_selection": {
            "source_path": str(key_vision_path),
            "selection_basis": args.key_vision_selection_basis,
            "selection_record": str(selection_record_path) if selection_record_path else None,
            "sha256": sha256(key_vision_path),
        },
        "input_manifest": "inputs/manifest.json",
        "production_file_manifest": "inputs/production-files.json",
        "contract_bundle": {
            "source_dir": str(contract_bundle_path) if contract_bundle_path else None,
            "files": contract_bundle_files,
        },
        "notes": [
            "Review designable axes and complete the cast contrast matrix before paid calls.",
            "Resolve any media/style contract conflict before treating a candidate as eligible.",
        ],
    }
    write_json(lab_dir / "experiment.json", experiment)
    write_jsonl(
        lab_dir / "ledger.jsonl",
        [
            {
                "event": "lab_initialized",
                "at": utc_now(),
                "mode": args.mode,
                "budgets": budgets,
                "input_manifest_sha256": sha256(lab_dir / "inputs" / "manifest.json"),
            }
        ],
    )
    write_jsonl(lab_dir / "results.jsonl", [])
    for name in ("image.jsonl", "prompt.jsonl", "audit.jsonl"):
        write_jsonl(lab_dir / "evaluations" / name, [])

    print(str(lab_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
