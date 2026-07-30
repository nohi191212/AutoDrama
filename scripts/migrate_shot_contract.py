"""Migrate legacy shot JSON to the structured dialogue/overlay contract.

Legacy dialogue is intentionally not parsed here. Supply a reviewed JSON map
from shot_id to DialogueLine objects whenever a shot contains dialogue text.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ClipToShotsEpisodeOutput, DialogueLine, ShotManifestEpisodeOutput


def _load_dialogue_map(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("dialogue map must be a JSON object keyed by shot_id")
    return value


def _migrate_dialogue(
    shot: dict[str, Any],
    dialogue_map: dict[str, list[dict[str, Any]]],
) -> None:
    shot_id = str(shot.get("shot_id") or "")
    legacy_lines = list(shot.get("dialogue") or [])
    supplied = dialogue_map.get(shot_id)
    if legacy_lines and supplied is None:
        raise ValueError(
            f"{shot_id} contains legacy dialogue; provide reviewed DialogueLine objects in --dialogue-map"
        )
    structured: list[dict[str, Any]] = []
    for index, raw in enumerate(supplied or [], start=1):
        item = dict(raw)
        item.setdefault("schema_version", 1)
        item.setdefault("line_index", index)
        item.setdefault("source_text", legacy_lines[index - 1] if index <= len(legacy_lines) else None)
        item.setdefault(
            "provenance",
            {
                "schema_version": 1,
                "source": "migration",
                "evidence": [item["source_text"]] if item.get("source_text") else [],
                "confidence": None,
                "model": None,
            },
        )
        structured.append(DialogueLine.model_validate(item).model_dump(mode="json"))
    shot["dialogue_lines"] = structured
    shot["dialogue"] = [line["text"] for line in structured]


def _migrate_overlay(shot: dict[str, Any], *, target_field: str) -> None:
    raw = shot.pop("overlay_text_spec", None)
    if raw is None:
        raw = shot.get("text_overlay_spec")
    legacy_text = shot.pop("overlay_text", None)
    shot.pop("requires_exact_text", None)
    if raw is None and legacy_text:
        raw = {"text": legacy_text, "render_mode": "postproduction"}
    if raw is not None:
        raw = dict(raw)
        raw.setdefault("schema_version", 1)
        raw.setdefault(
            "provenance",
            {
                "schema_version": 1,
                "source": "migration",
                "evidence": [str(raw.get("text") or "")],
                "confidence": None,
                "model": None,
            },
        )
    shot.pop("text_overlay_spec", None)
    shot.pop("overlay_text_spec", None)
    shot[target_field] = raw


def migrate_payload(
    payload: dict[str, Any],
    dialogue_map: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    migrated = deepcopy(payload)
    if "shots" in migrated:
        migrated["schema_version"] = 5
        rows = migrated["shots"]
        for shot in rows:
            _migrate_dialogue(shot, dialogue_map)
            _migrate_overlay(shot, target_field="text_overlay_spec")
            shot["contract_version"] = 2
        return ShotManifestEpisodeOutput.model_validate(migrated).model_dump(mode="json")
    if "clips" in migrated:
        for clip in migrated["clips"]:
            for shot in clip.get("shots") or []:
                _migrate_dialogue(shot, dialogue_map)
                _migrate_overlay(shot, target_field="overlay_text_spec")
                shot.setdefault("entity_states", [])
                shot.setdefault("allowed_props", [])
        return ClipToShotsEpisodeOutput.model_validate(migrated).model_dump(mode="json")
    raise ValueError("input must be a shot manifest episode or clip_to_shots episode artifact")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dialogue-map", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed migration and print its version transition without writing files.",
    )
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path:
        raise ValueError("output must differ from input; migration never overwrites its source")

    source = json.loads(input_path.read_text(encoding="utf-8"))
    migrated = migrate_payload(source, _load_dialogue_map(args.dialogue_map))
    output_kind = "shot_manifest_v5" if "shots" in migrated else "clip_to_shots_structured"
    print(f"shot_contract_version=legacy->{output_kind}")
    if args.dry_run:
        print(f"dry_run=true output={output_path}")
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"migrated structured shot contract: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
