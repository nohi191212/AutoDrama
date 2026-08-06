from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".tmp" / "gpt-image-2-compact-hybrid-evolution-20260805"
RUBRICS = LAB / "rubrics"
OUTPUT = LAB / "rubric-verification.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(path: Path) -> dict:
    rubric = read_json(path)
    guide_path = path.parent / rubric["scoring_guide"]
    guide = guide_path.read_text(encoding="utf-8")
    missing = []
    duplicate_ids = []
    seen = set()
    for item in rubric["dimensions"]:
        key = item["id"]
        if key in seen:
            duplicate_ids.append(key)
        seen.add(key)
        marker = f"`{key}`"
        start = guide.find(marker)
        if start < 0:
            missing.append({"id": key, "reason": "dimension section missing"})
            continue
        end = guide.find("\n### ", start + len(marker))
        section = guide[start : end if end >= 0 else len(guide)]
        absent_anchors = [score for score in (10, 8, 6, 4, 2, 0) if f"- {score}：" not in section]
        if absent_anchors:
            missing.append({"id": key, "reason": f"anchors missing: {absent_anchors}"})
    return {
        "name": rubric["name"],
        "revision": rubric["revision"],
        "dimension_count": len(rubric["dimensions"]),
        "guide": str(guide_path),
        "duplicate_ids": duplicate_ids,
        "missing": missing,
        "ok": not duplicate_ids and not missing,
    }


def main() -> int:
    general = verify(RUBRICS / "general_rubrics-v2.json")
    style = verify(RUBRICS / "xuanhuan-v2-rubrics.json")
    general_weight = float(read_json(RUBRICS / "general_rubrics-v2.json")["group_weight"])
    style_weight = float(read_json(RUBRICS / "xuanhuan-v2-rubrics.json")["group_weight"])
    result = {
        "general": general,
        "style": style,
        "group_weight_sum": general_weight + style_weight,
        "ok": general["ok"] and style["ok"] and abs(general_weight + style_weight - 1.0) < 1e-9,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
