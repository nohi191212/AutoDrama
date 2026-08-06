from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".tmp" / "gpt-image-2-xuanhuan-relative-loop-20260805"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    json_paths = sorted(LAB.rglob("*.json"))
    for path in json_paths:
        read_json(path)

    ledger = read_json(LAB / "ledger.json")
    assert ledger["reserved"]["template_image"] == 100
    assert ledger["limits"]["template_image"] == 100

    production = ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "key_vision_prompt" / "default.md"
    r00 = LAB / "champions" / "r00.md"
    r02 = LAB / "champions" / "r02.md"
    r06 = LAB / "champions" / "r06.md"
    assert sha256(production) == sha256(r00) == "73fa4f6d6f70a86f078ff3ff9044fe1bb6c2e5edf232650dd340dc0d3d95119f"
    assert sha256(r02) == sha256(r06) == "1edd4d2b9a0a5585e714c2d37e1215089d51e6b140d0e631d8de8044ff89a0be"

    history = read_json(LAB / "patch_history.json")
    promoted = [item["round_id"] for item in history if item["promoted"]]
    assert promoted == ["r02"]
    r03_record = next(item for item in history if item["round_id"] == "r03")
    assert r03_record["decision"]["promote"] is True
    assert r03_record["holdout_decision"]["passed"] is False
    assert r03_record["promoted"] is False

    r06_decision = read_json(LAB / "rounds" / "r06" / "decision.json")
    assert r06_decision["formal_round_complete"] is False
    assert r06_decision["budget_exhausted"] is True
    assert r06_decision["observed_provisional_branch"] == "B"
    assert r06_decision["missing_generations"][0]["image_id"] == "img-r06--B--combat_ford_parry"
    b_stats = next(item for item in r06_decision["stats"] if item["branch_id"] == "B")
    assert b_stats["wins_vs_control"] == 2
    assert b_stats["complete"] is False
    assert b_stats["eligible"] is False

    checked_aggregates = 0
    for aggregate_dir in sorted((LAB / "rounds").glob("*/aggregated")):
        for path in aggregate_dir.glob("img-*.json"):
            aggregate = read_json(path)
            assert aggregate["adjudication_required"] is False, path
            assert not aggregate["disagreements"], path
            checked_aggregates += 1

    checked_luna = 0
    for round_id in ("r04", "r05", "r06"):
        round_dir = LAB / "rounds" / round_id
        jobs = read_json(round_dir / "review_jobs.json")
        for job in jobs:
            image_id = job["image_id"]
            primary = read_json(round_dir / "primary_reviews" / f"{image_id}.json")
            assert primary["reviewer"] == "codex-primary-independent"
            luna = read_json(round_dir / "sol_reviews" / f"{image_id}.json")
            assert luna["combined"]["reviewer"] == "gpt-5.6-luna"
            checked_luna += 1

    state = read_json(LAB / "DSE_STATE.json")
    assert state["status"] == "stopped_budget_exhausted"
    assert state["formal_holdout_patience_stop_met"] is False
    assert state["champion_sha256"] == sha256(r06)

    report = LAB / "report.html"
    summary = LAB / "FINAL_SUMMARY.md"
    assert report.is_file() and report.stat().st_size > 1_000_000
    assert summary.is_file() and summary.stat().st_size > 1_000

    print(
        "Final xuanhuan loop smoke passed: "
        f"{len(json_paths)} JSON files parsed, {checked_aggregates} final aggregates resolved, "
        f"{checked_luna} Luna-reviewed images verified, budget 100/100, production template untouched."
    )


if __name__ == "__main__":
    main()
