from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "autodrama" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autodrama.core.schemas import RoleAppearance
from autodrama.core.visual_contract import migrate_legacy_role_appearance, migrate_legacy_visual_style


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def migrate_state(state_payload: dict[str, Any], migration_payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _mapping(state_payload.setdefault("metadata", {}), label="state.metadata")
    style_fields = _mapping(migration_payload.get("visual_style"), label="migration.visual_style")
    legacy_prompt = str(metadata.get("visual_style_prompt") or "")
    style_spec = migrate_legacy_visual_style(
        legacy_prompt,
        medium=str(style_fields.get("medium") or ""),
        materials=list(style_fields.get("materials") or []),
        palette=list(style_fields.get("palette") or []),
        lighting=list(style_fields.get("lighting") or []),
        camera=list(style_fields.get("camera") or []),
        negative_constraints=list(style_fields.get("negative_constraints") or []),
    )
    metadata["visual_style_spec"] = style_spec.model_dump(mode="json")
    metadata["style_spec_version"] = style_spec.version
    metadata.pop("visual_style_prompt", None)

    reviewed_appearances = _mapping(
        migration_payload.get("role_appearances", {}),
        label="migration.role_appearances",
    )
    migrated_ids: set[str] = set()
    roles = _mapping(state_payload.get("roles", {}), label="state.roles")
    for role_payload in roles.values():
        role = _mapping(role_payload, label="state.roles[]")
        appearances = _mapping(role.get("appearances", {}), label="state.roles[].appearances")
        for appearance_name, appearance_payload in appearances.items():
            raw_appearance = _mapping(
                appearance_payload,
                label=f"state.roles[].appearances.{appearance_name}",
            )
            appearance_id = str(raw_appearance.get("id") or "")
            reviewed = reviewed_appearances.get(appearance_id)
            if not isinstance(reviewed, dict):
                raise ValueError(
                    f"appearance {appearance_id or appearance_name} has no explicit migration review"
                )
            appearance = RoleAppearance.model_validate(
                {**raw_appearance, "schema_version": 2}
            )
            migrated = migrate_legacy_role_appearance(
                appearance,
                identity_invariants=list(reviewed.get("identity_invariants") or []),
                wardrobe=list(reviewed.get("wardrobe") or []),
                evidence=list(reviewed.get("evidence") or []),
                confidence=reviewed.get("confidence"),
                warnings=list(reviewed.get("warnings") or []),
            )
            appearances[appearance_name] = migrated.model_dump(mode="json")
            migrated_ids.add(appearance_id)

    unused_reviews = set(reviewed_appearances).difference(migrated_ids)
    if unused_reviews:
        raise ValueError(
            "migration contains unknown appearance ids: " + ", ".join(sorted(unused_reviews))
        )
    return state_payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a reviewed project state to the explicit visual contract v2."
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--migration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed migration and print its version transition without writing files.",
    )
    args = parser.parse_args()

    state_path = args.state.resolve()
    migration_path = args.migration.resolve()
    output_path = args.output.resolve()
    if output_path == state_path:
        raise ValueError("--output must differ from --state; migration never overwrites its input")

    state_payload = _mapping(
        json.loads(state_path.read_text(encoding="utf-8")),
        label="state",
    )
    migration_payload = _mapping(
        json.loads(migration_path.read_text(encoding="utf-8")),
        label="migration",
    )
    migrated = migrate_state(state_payload, migration_payload)
    print("visual_contract_version=legacy->2")
    if args.dry_run:
        print(f"dry_run=true output={output_path}")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = output_path.with_suffix(output_path.suffix + ".v1.backup")
    shutil.copy2(state_path, backup_path)
    output_path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"migrated={output_path}")
    print(f"backup={backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
