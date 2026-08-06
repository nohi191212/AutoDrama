from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUBRICS = ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "image_rubrics"
OUTPUT = ROOT / ".tmp" / "image-rubrics-v2-verification.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(path: Path) -> dict:
    rubric = read_json(path)
    guide_path = path.parent / rubric["scoring_guide"]
    guide = guide_path.read_text(encoding="utf-8")
    seen: set[str] = set()
    duplicates: list[str] = []
    missing: list[dict[str, object]] = []
    for dimension in rubric["dimensions"]:
        dimension_id = str(dimension["id"])
        if dimension_id in seen:
            duplicates.append(dimension_id)
        seen.add(dimension_id)
        marker = f"`{dimension_id}`"
        start = guide.find(marker)
        if start < 0:
            missing.append({"id": dimension_id, "reason": "section missing"})
            continue
        end = guide.find("\n### ", start + len(marker))
        section = guide[start : end if end >= 0 else len(guide)]
        absent = [score for score in (10, 8, 6, 4, 2, 0) if f"- {score}：" not in section]
        if absent:
            missing.append({"id": dimension_id, "reason": f"anchors missing: {absent}"})
    return {
        "name": rubric["name"],
        "revision": rubric["revision"],
        "dimension_count": len(rubric["dimensions"]),
        "duplicate_ids": duplicates,
        "missing": missing,
        "ok": not duplicates and not missing,
    }


def main() -> int:
    general_path = RUBRICS / "general_rubrics-v2.json"
    style_path = RUBRICS / "xuanhuan-v2-rubrics.json"
    general = verify(general_path)
    style = verify(style_path)
    group_weight_sum = float(read_json(general_path)["group_weight"]) + float(
        read_json(style_path)["group_weight"]
    )
    result = {
        "general": general,
        "style": style,
        "group_weight_sum": group_weight_sum,
        "ok": general["ok"] and style["ok"] and abs(group_weight_sum - 1.0) < 1e-9,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
