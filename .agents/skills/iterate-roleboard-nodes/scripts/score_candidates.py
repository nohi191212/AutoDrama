from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected an object at {path}:{line_number}")
        value["_line_number"] = line_number
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def validate_rubric(rubric: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    dimensions = rubric.get("dimensions")
    gates = rubric.get("gates")
    policy = rubric.get("policy")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError("Rubric needs dimensions")
    if not isinstance(gates, list):
        raise ValueError("Rubric needs gates")
    if not isinstance(policy, dict):
        raise ValueError("Rubric needs policy")

    dimensions_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    gates_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dimension_ids: set[str] = set()
    gate_ids: set[str] = set()
    for dimension in dimensions:
        dimension_id = str(dimension.get("id") or "")
        scope = str(dimension.get("scope") or "")
        weight = float(dimension.get("weight") or 0)
        if not dimension_id or not scope or weight <= 0:
            raise ValueError(f"Invalid dimension: {dimension!r}")
        if dimension_id in dimension_ids:
            raise ValueError(f"Duplicate dimension: {dimension_id}")
        dimension_ids.add(dimension_id)
        dimensions_by_scope[scope].append(dimension)
    for gate in gates:
        gate_id = str(gate.get("id") or "")
        scope = str(gate.get("scope") or "")
        if not gate_id or not scope:
            raise ValueError(f"Invalid gate: {gate!r}")
        if gate_id in gate_ids:
            raise ValueError(f"Duplicate gate: {gate_id}")
        gate_ids.add(gate_id)
        gates_by_scope[scope].append(gate)

    scope_weights = policy.get("scope_weights") or {}
    if set(scope_weights) != set(dimensions_by_scope):
        raise ValueError(
            f"scope_weights must match dimension scopes: weights={sorted(scope_weights)} dimensions={sorted(dimensions_by_scope)}"
        )
    if abs(sum(float(value) for value in scope_weights.values()) - 1.0) > 1e-6:
        raise ValueError("scope_weights must sum to 1")
    for scope in dimensions_by_scope:
        if scope not in gates_by_scope:
            gates_by_scope[scope] = []
    return dict(dimensions_by_scope), dict(gates_by_scope)


def validate_scored_row(
    row: dict[str, Any],
    dimensions: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    *,
    require_evidence: bool,
) -> dict[str, Any]:
    line = row.get("_line_number")
    candidate_id = str(row.get("candidate_id") or "").strip()
    sample_id = str(row.get("sample_id") or "").strip()
    if not candidate_id or not sample_id:
        raise ValueError(f"Missing candidate_id/sample_id at evaluation line {line}")

    scores = row.get("scores")
    evidence = row.get("evidence")
    gate_results = row.get("gates")
    not_applicable = {str(value) for value in row.get("not_applicable") or []}
    if not isinstance(scores, dict) or not isinstance(gate_results, dict):
        raise ValueError(f"Missing scores/gates at evaluation line {line}")
    if require_evidence and not isinstance(evidence, dict):
        raise ValueError(f"Missing evidence at evaluation line {line}")
    evidence = evidence if isinstance(evidence, dict) else {}

    expected_dimensions = {str(item["id"]) for item in dimensions}
    unknown_na = not_applicable - expected_dimensions
    if unknown_na:
        raise ValueError(f"Unknown not_applicable dimensions at line {line}: {sorted(unknown_na)}")
    expected_scored = expected_dimensions - not_applicable
    unknown_scores = set(scores) - expected_dimensions
    missing_scores = expected_scored - set(scores)
    forbidden_scores = not_applicable.intersection(scores)
    if unknown_scores or missing_scores or forbidden_scores:
        raise ValueError(
            f"Dimension coverage mismatch at line {line}: unknown={sorted(unknown_scores)} "
            f"missing={sorted(missing_scores)} scored_but_na={sorted(forbidden_scores)}"
        )

    weighted_sum = 0.0
    total_weight = 0.0
    normalized_scores: dict[str, float] = {}
    for dimension in dimensions:
        dimension_id = str(dimension["id"])
        if dimension_id in not_applicable:
            continue
        value = float(scores[dimension_id])
        if value < 0 or value > 10:
            raise ValueError(f"Score out of range for {dimension_id} at line {line}: {value}")
        if require_evidence and not str(evidence.get(dimension_id) or "").strip():
            raise ValueError(f"Missing evidence for {dimension_id} at line {line}")
        weight = float(dimension["weight"])
        normalized_scores[dimension_id] = value
        weighted_sum += weight * value
        total_weight += weight
    if total_weight <= 0:
        raise ValueError(f"No applicable dimensions at line {line}")
    raw_score = weighted_sum / total_weight

    expected_gates = {str(item["id"]) for item in gates}
    if set(gate_results) != expected_gates:
        raise ValueError(
            f"Gate coverage mismatch at line {line}: missing={sorted(expected_gates - set(gate_results))} "
            f"unknown={sorted(set(gate_results) - expected_gates)}"
        )
    normalized_gates: dict[str, bool] = {}
    fatal_failures: list[str] = []
    nonfatal_failures: list[str] = []
    gate_specs = {str(item["id"]): item for item in gates}
    for gate_id, value in gate_results.items():
        if not isinstance(value, bool):
            raise ValueError(f"Gate {gate_id} must be boolean at line {line}")
        normalized_gates[gate_id] = value
        if not value:
            if bool(gate_specs[gate_id].get("fatal", False)):
                fatal_failures.append(gate_id)
            else:
                nonfatal_failures.append(gate_id)

    return {
        "candidate_id": candidate_id,
        "sample_id": sample_id,
        "scope": str(row["scope"]),
        "role_id": str(row.get("role_id") or ""),
        "appearance_id": str(row.get("appearance_id") or ""),
        "judge_id": str(row.get("judge_id") or "unknown"),
        "scores": normalized_scores,
        "gates": normalized_gates,
        "raw_score": raw_score,
        "fatal_failures": fatal_failures,
        "nonfatal_failures": nonfatal_failures,
        "classification": {
            key: row.get(key)
            for key in (
                "gold_action",
                "predicted_action",
                "gold_approved",
                "predicted_approved",
                "gold_critical",
                "gold_failed_gates",
                "predicted_failed_gates",
                "gold_cast_clone",
                "predicted_cast_clone",
            )
            if key in row
        },
    }


def aggregate_judges(rows: list[dict[str, Any]], nonfatal_cap: float) -> dict[str, Any]:
    first = rows[0]
    dimension_ids = sorted(first["scores"])
    gate_ids = sorted(first["gates"])
    for row in rows[1:]:
        if sorted(row["scores"]) != dimension_ids or sorted(row["gates"]) != gate_ids:
            raise ValueError(f"Judges used different coverage for {first['candidate_id']}/{first['sample_id']}")
    averaged_dimensions = {
        dimension_id: statistics.fmean(row["scores"][dimension_id] for row in rows)
        for dimension_id in dimension_ids
    }
    raw_score = statistics.fmean(row["raw_score"] for row in rows)
    gates = {gate_id: all(row["gates"][gate_id] for row in rows) for gate_id in gate_ids}
    fatal_failures = sorted({value for row in rows for value in row["fatal_failures"]})
    nonfatal_failures = sorted({value for row in rows for value in row["nonfatal_failures"]})
    gated_score = min(raw_score, nonfatal_cap) if nonfatal_failures else raw_score

    classification: dict[str, Any] = {}
    keys = {key for row in rows for key in row["classification"]}
    for key in keys:
        values = [row["classification"].get(key) for row in rows if key in row["classification"]]
        canonical = {json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values}
        classification[key] = values[0] if len(canonical) == 1 else "__judge_disagreement__"

    return {
        "candidate_id": first["candidate_id"],
        "sample_id": first["sample_id"],
        "scope": first["scope"],
        "role_id": first["role_id"],
        "appearance_id": first["appearance_id"],
        "judge_count": len(rows),
        "dimension_scores": averaged_dimensions,
        "gates": gates,
        "raw_score": round(raw_score, 4),
        "gated_score": round(gated_score, 4),
        "fatal_failures": fatal_failures,
        "nonfatal_failures": nonfatal_failures,
        "classification": classification,
    }


def macro_f1(gold: list[str], predicted: list[str]) -> float | None:
    if not gold or len(gold) != len(predicted):
        return None
    labels = sorted(set(gold) | set(predicted))
    values: list[float] = []
    for label in labels:
        true_positive = sum(g == label and p == label for g, p in zip(gold, predicted))
        false_positive = sum(g != label and p == label for g, p in zip(gold, predicted))
        false_negative = sum(g == label and p != label for g, p in zip(gold, predicted))
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return statistics.fmean(values) if values else None


def classification_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [sample["classification"] for sample in samples]
    action_pairs = [
        (str(item["gold_action"]), str(item["predicted_action"]))
        for item in usable
        if item.get("gold_action") is not None
        and item.get("predicted_action") is not None
        and item.get("predicted_action") != "__judge_disagreement__"
    ]
    result: dict[str, Any] = {}
    if action_pairs:
        gold = [pair[0] for pair in action_pairs]
        predicted = [pair[1] for pair in action_pairs]
        result["action_samples"] = len(action_pairs)
        result["exact_action_accuracy"] = round(sum(g == p for g, p in action_pairs) / len(action_pairs), 4)
        f1 = macro_f1(gold, predicted)
        result["macro_f1"] = round(f1, 4) if f1 is not None else None

    approval_pairs = [
        (bool(item["gold_approved"]), bool(item["predicted_approved"]), bool(item.get("gold_critical", False)))
        for item in usable
        if isinstance(item.get("gold_approved"), bool) and isinstance(item.get("predicted_approved"), bool)
    ]
    if approval_pairs:
        accepted_gold = [pair for pair in approval_pairs if pair[0]]
        result["approval_samples"] = len(approval_pairs)
        result["critical_false_accepts"] = sum((not gold_ok) and predicted_ok and critical for gold_ok, predicted_ok, critical in approval_pairs)
        result["false_accept_rate"] = round(
            sum((not gold_ok) and predicted_ok for gold_ok, predicted_ok, _critical in approval_pairs)
            / max(1, sum(not gold_ok for gold_ok, _predicted_ok, _critical in approval_pairs)),
            4,
        )
        result["accepted_false_reject_rate"] = round(
            sum(gold_ok and not predicted_ok for gold_ok, predicted_ok, _critical in approval_pairs)
            / max(1, len(accepted_gold)),
            4,
        )

    gold_gate_total = 0
    detected_gate_total = 0
    for item in usable:
        gold_failed = item.get("gold_failed_gates")
        predicted_failed = item.get("predicted_failed_gates")
        if isinstance(gold_failed, list) and isinstance(predicted_failed, list):
            gold_set = {str(value) for value in gold_failed}
            predicted_set = {str(value) for value in predicted_failed}
            gold_gate_total += len(gold_set)
            detected_gate_total += len(gold_set.intersection(predicted_set))
    if gold_gate_total:
        result["hard_gate_recall"] = round(detected_gate_total / gold_gate_total, 4)

    clone_pairs = [
        (bool(item["gold_cast_clone"]), bool(item["predicted_cast_clone"]))
        for item in usable
        if isinstance(item.get("gold_cast_clone"), bool) and isinstance(item.get("predicted_cast_clone"), bool)
    ]
    positive_clones = sum(gold for gold, _predicted in clone_pairs)
    if positive_clones:
        result["cast_clone_recall"] = round(
            sum(gold and predicted for gold, predicted in clone_pairs) / positive_clones,
            4,
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and rank roleboard experiment candidates")
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--evaluations", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rubric_path = Path(args.rubric).resolve()
    evaluations_path = Path(args.evaluations).resolve()
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    dimensions_by_scope, gates_by_scope = validate_rubric(rubric)
    policy = rubric["policy"]
    require_evidence = bool(policy.get("require_evidence", True))
    nonfatal_cap = float(policy.get("nonfatal_gate_cap", 7.0))

    raw_rows = load_jsonl(evaluations_path)
    status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    scored: list[dict[str, Any]] = []
    for row in raw_rows:
        candidate_id = str(row.get("candidate_id") or "").strip() or "__missing_candidate__"
        status = str(row.get("status") or "scored")
        status_counts[candidate_id][status] += 1
        if status != "scored":
            continue
        scope = str(row.get("scope") or "")
        if scope not in dimensions_by_scope:
            raise ValueError(f"Unknown scope at line {row.get('_line_number')}: {scope!r}")
        scored.append(
            validate_scored_row(
                row,
                dimensions_by_scope[scope],
                gates_by_scope[scope],
                require_evidence=require_evidence,
            )
        )

    grouped_judges: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        grouped_judges[(row["candidate_id"], row["sample_id"], row["scope"])].append(row)
    samples = [aggregate_judges(rows, nonfatal_cap) for rows in grouped_judges.values()]

    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_candidate[sample["candidate_id"]].append(sample)

    scope_weights = {key: float(value) for key, value in policy["scope_weights"].items()}
    scope_thresholds = {key: float(value) for key, value in (policy.get("scope_thresholds") or {}).items()}
    minimum_records = {key: int(value) for key, value in (policy.get("minimum_records") or {}).items()}
    minimum_roles = {key: int(value) for key, value in (policy.get("minimum_distinct_roles") or {}).items()}
    classification_thresholds = policy.get("classification_thresholds") or {}

    candidate_summaries: list[dict[str, Any]] = []
    for candidate_id in sorted(set(by_candidate) | set(status_counts)):
        candidate_samples = by_candidate.get(candidate_id, [])
        scopes: dict[str, Any] = {}
        eligibility_reasons: list[str] = []
        fatal_failures: list[dict[str, Any]] = []
        all_gated_scores: list[float] = []
        for scope, scope_weight in scope_weights.items():
            scope_samples = [sample for sample in candidate_samples if sample["scope"] == scope]
            values = [float(sample["gated_score"]) for sample in scope_samples]
            roles = {sample["role_id"] for sample in scope_samples if sample["role_id"]}
            for sample in scope_samples:
                for gate_id in sample["fatal_failures"]:
                    fatal_failures.append(
                        {"sample_id": sample["sample_id"], "scope": scope, "gate_id": gate_id}
                    )
            required_count = minimum_records.get(scope, 1)
            required_roles = minimum_roles.get(scope, 0)
            if len(scope_samples) < required_count:
                eligibility_reasons.append(f"scope {scope} has {len(scope_samples)} record(s), requires {required_count}")
            if len(roles) < required_roles:
                eligibility_reasons.append(f"scope {scope} has {len(roles)} distinct role(s), requires {required_roles}")
            if not values:
                eligibility_reasons.append(f"scope {scope} has no scored samples")
                scope_mean = None
            else:
                scope_mean = statistics.fmean(values)
                threshold = scope_thresholds.get(scope)
                if threshold is not None and scope_mean < threshold:
                    eligibility_reasons.append(
                        f"scope {scope} mean {scope_mean:.4f} is below threshold {threshold:.4f}"
                    )
                all_gated_scores.extend(values)
            scopes[scope] = {
                "weight": scope_weight,
                "records": len(scope_samples),
                "distinct_roles": len(roles),
                "mean": round(scope_mean, 4) if scope_mean is not None else None,
                "minimum": round(min(values), 4) if values else None,
                "population_variance": round(statistics.pvariance(values), 6) if values else None,
                "gate_pass_rate": round(
                    sum(not sample["fatal_failures"] and not sample["nonfatal_failures"] for sample in scope_samples)
                    / len(scope_samples),
                    4,
                )
                if scope_samples
                else None,
            }
        if fatal_failures:
            eligibility_reasons.append(f"{len(fatal_failures)} fatal gate failure(s)")

        composite = None
        if all(scopes[scope]["mean"] is not None for scope in scope_weights):
            composite = sum(scopes[scope]["mean"] * scope_weights[scope] for scope in scope_weights)

        class_metrics = classification_metrics(candidate_samples)
        threshold_map = {
            "critical_false_accepts": lambda value, limit: value <= limit,
            "hard_gate_recall": lambda value, limit: value >= limit,
            "exact_action_accuracy": lambda value, limit: value >= limit,
            "macro_f1": lambda value, limit: value >= limit,
            "accepted_false_reject_rate": lambda value, limit: value <= limit,
            "cast_clone_recall": lambda value, limit: value >= limit,
        }
        for metric, limit in classification_thresholds.items():
            if metric not in class_metrics:
                eligibility_reasons.append(f"classification metric {metric} is missing")
                continue
            if metric in threshold_map and not threshold_map[metric](float(class_metrics[metric]), float(limit)):
                eligibility_reasons.append(
                    f"classification metric {metric}={class_metrics[metric]} fails threshold {limit}"
                )

        candidate_summaries.append(
            {
                "candidate_id": candidate_id,
                "eligible": not eligibility_reasons,
                "eligibility_reasons": eligibility_reasons,
                "composite_score": round(composite, 4) if composite is not None else None,
                "overall_minimum": round(min(all_gated_scores), 4) if all_gated_scores else None,
                "overall_population_variance": round(statistics.pvariance(all_gated_scores), 6)
                if all_gated_scores
                else None,
                "scopes": scopes,
                "fatal_failures": fatal_failures,
                "classification_metrics": class_metrics,
                "status_counts": dict(status_counts[candidate_id]),
            }
        )

    candidate_summaries.sort(
        key=lambda item: (
            not item["eligible"],
            -(item["composite_score"] if item["composite_score"] is not None else -1),
            -(item["overall_minimum"] if item["overall_minimum"] is not None else -1),
            item["overall_population_variance"]
            if item["overall_population_variance"] is not None
            else float("inf"),
            item["candidate_id"],
        )
    )
    for rank, candidate in enumerate(candidate_summaries, start=1):
        candidate["rank"] = rank

    result = {
        "schema_version": 1,
        "rubric_name": rubric.get("name"),
        "rubric_revision": rubric.get("revision"),
        "rubric_sha256": sha256(rubric_path),
        "evaluations_sha256": sha256(evaluations_path),
        "raw_record_count": len(raw_rows),
        "scored_sample_count": len(samples),
        "candidates": candidate_summaries,
        "sample_summaries": sorted(
            samples, key=lambda item: (item["candidate_id"], item["scope"], item["sample_id"])
        ),
    }
    output_path = Path(args.output).resolve()
    write_json(output_path, result)

    for candidate in candidate_summaries:
        score = "-" if candidate["composite_score"] is None else f"{candidate['composite_score']:.4f}"
        print(
            f"{candidate['rank']:>2}  {candidate['candidate_id']:<32} "
            f"eligible={str(candidate['eligible']).lower():<5} score={score}"
        )
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
