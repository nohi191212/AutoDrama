from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import roleboard_phase1_round1_smoke as round1  # noqa: E402


ROUND = "p1-r01-knockout"
BATCH_ID = "p1-r01-knockout-v2"
PARENT = "p1-r01-exploration-v1"

# Supervised selection: keep the Round-0 reference-policy winner as control and
# replicate the restrained-design challenger, whose cast-level face/AI-risk
# scores were stronger than the nearly-tied layout-proof candidate.
VARIANTS = {
    "p1-r01-knockout-incumbent-v1": (
        "incumbent-reference-policy-control",
        "Replicate the frozen Round-0 spatial-template-plus-key-vision policy with no wrapper-family change.",
    ),
    "p1-r01-knockout-restrained-v1": (
        "restrained-design-only-replication",
        "Replicate the restrained-design wrapper because it improves cast-level anti-archetype and role-specific design while keeping all hard gates passing.",
    ),
}
CLAUSES = {
    "p1-r01-knockout-incumbent-v1": round1.CLAUSES["p1-r01-incumbent-v1"],
    "p1-r01-knockout-restrained-v1": round1.CLAUSES["p1-r01-restrained-design-v1"],
}


def configure_module() -> None:
    # The round-1 executor is deliberately reused so reference ordering,
    # reservation semantics, transport accounting, and safe_progress remain
    # identical. Only the declared candidate set and batch identity change.
    round1.ROUND = ROUND
    round1.BATCH_ID = BATCH_ID
    round1.PARENT = PARENT
    round1.SPLIT = "knockout"
    round1.VARIANTS = VARIANTS
    round1.CLAUSES = CLAUSES


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 knockout roleboard smoke harness")
    parser.add_argument("--lab-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    run = sub.add_parser("execute")
    run.add_argument("--confirm-paid-calls", action="store_true")
    args = parser.parse_args()

    configure_module()
    ctx = round1.base.load_lab(Path(args.lab_dir))
    if args.command == "prepare":
        value = round1.prepare(ctx)
    else:
        value = asyncio.run(round1.execute(ctx, confirmed=bool(args.confirm_paid_calls)))
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
