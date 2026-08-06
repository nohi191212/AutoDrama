from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAB_DIR = PROJECT_ROOT / ".tmp" / "gpt-image-2-film-live-action-evolution-20260805"


def main() -> int:
    style_path = PROJECT_ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "visual_styles" / "live-action-film-v1.yaml"
    style = yaml.safe_load(style_path.read_text(encoding="utf-8"))
    assert style["schema_version"] == 2
    assert style["source"] == "yaml"
    assert set(style) <= {"schema_version", "version", "medium", "source", "render_engine_language", "materials", "palette", "lighting", "camera", "negative_constraints"}

    rubric = json.loads((LAB_DIR / "rubrics" / "live-action-film-v1.json").read_text(encoding="utf-8"))
    batch = json.loads((LAB_DIR / "batches" / "b00.json").read_text(encoding="utf-8"))
    preview = json.loads((LAB_DIR / "batches" / "b01-camera-light-preview.json").read_text(encoding="utf-8"))
    ledger = json.loads((LAB_DIR / "ledger.json").read_text(encoding="utf-8"))
    assert rubric["canvas_contract"]["aspect_ratio"] == "16:9"
    assert len(rubric["hard_gates"]) == 8
    assert batch["image_calls_used"] == 7
    assert preview["image_calls_used_after_batch"] == ledger["image_calls_used"] == 10
    assert ledger["image_calls_used"] <= ledger["image_call_ceiling"] == 100
    assert len(batch["records"]) == 7
    assert len(preview["records"]) == 3
    assert preview["preview_conclusion"]["promotion"].startswith("not_yet")

    records = batch["records"] + preview["records"]
    for record in records:
        image_path = LAB_DIR / record["image"]
        assert image_path.exists(), image_path
        with Image.open(image_path) as image:
            ratio = image.width / image.height
            assert abs(ratio - (16 / 9)) < 0.01, (image_path, image.size)

    report = (LAB_DIR / "report.html").read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r'(?:src|href)="(assets/[^"#]+\.(?:png|yaml|md))"', report)))
    assert refs
    for ref in refs:
        assert (LAB_DIR / ref).exists(), ref

    print(f"ok lab={LAB_DIR} images={len(records)} calls={ledger['image_calls_used']}/{ledger['image_call_ceiling']} refs={len(refs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
