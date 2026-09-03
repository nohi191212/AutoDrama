from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    SceneMultiviewImageGenerationItem,
    SceneMultiviewPlanItem,
)
from autodrama.workflows.nodes.shot_asset_nodes import (  # noqa: E402
    SceneMultiviewImageGenerationNode,
)


class _ExistingProjectLayout:
    @staticmethod
    def existing_project_file(_project_dir: Path, _path: str) -> bool:
        return True


def _valid_plan_payload() -> dict[str, Any]:
    return {
        "episode_key": "episode_001",
        "scene_id": "scene_001",
        "layout_id": "scene_001",
        "views": [
            {
                "view_id": f"scene_001_view_{index:02d}",
                "view_index": index,
                "camera_description": f"camera {index}",
                "visible_anchors": [],
                "shot_ids": [f"shot_{index:03d}"] if index <= 2 else [],
            }
            for index in range(1, 5)
        ],
        "assignments": [
            {
                "shot_id": "shot_001",
                "primary_view_id": "scene_001_view_01",
                "secondary_view_ids": ["scene_001_view_03"],
            },
            {
                "shot_id": "shot_002",
                "primary_view_id": "scene_001_view_02",
                "secondary_view_ids": [],
            },
        ],
        "fingerprint": "plan-fingerprint",
    }


def _valid_generated_payload(output_dir: Path) -> dict[str, Any]:
    return {
        "episode_key": "episode_001",
        "scene_id": "scene_001",
        "layout_id": "scene_001",
        "plan_fingerprint": "plan-fingerprint",
        "prompt": "scene multiview prompt",
        "fingerprint": "generation-fingerprint",
        "board_asset_id": "scene_001_multiview_board",
        "board_asset_path": str(output_dir / "board.png"),
        "views": [
            {
                "view_id": f"scene_001_view_{index:02d}",
                "view_index": index,
                "camera_description": f"camera {index}",
                "asset_path": str(output_dir / f"view_{index:02d}.png"),
                "fingerprint": f"view-fingerprint-{index}",
            }
            for index in range(1, 5)
        ],
        "provider": "smoke",
        "model": "smoke",
    }


def _expect_invalid(factory: Callable[[], object], expected_fragment: str | None = None) -> None:
    try:
        factory()
    except ValidationError as exc:
        if expected_fragment is not None:
            assert expected_fragment in str(exc), str(exc)
    else:
        raise AssertionError("invalid scene multiview package was accepted")


def run(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_payload = _valid_plan_payload()
    plan = SceneMultiviewPlanItem.model_validate(plan_payload)
    assert [view.view_index for view in plan.views] == [1, 2, 3, 4]

    missing_view = _valid_plan_payload()
    missing_view["views"] = missing_view["views"][:3]
    _expect_invalid(lambda: SceneMultiviewPlanItem.model_validate(missing_view))

    duplicate_index = _valid_plan_payload()
    duplicate_index["views"][3]["view_index"] = 3
    _expect_invalid(
        lambda: SceneMultiviewPlanItem.model_validate(duplicate_index),
        "unique view_index values 1, 2, 3, 4",
    )

    duplicate_view_id = _valid_plan_payload()
    duplicate_view_id["views"][3]["view_id"] = "scene_001_view_03"
    _expect_invalid(
        lambda: SceneMultiviewPlanItem.model_validate(duplicate_view_id),
        "exactly four unique view_id values",
    )

    unknown_view = _valid_plan_payload()
    unknown_view["assignments"][0]["primary_view_id"] = "missing_view"
    _expect_invalid(
        lambda: SceneMultiviewPlanItem.model_validate(unknown_view),
        "unknown primary view",
    )

    incomplete_assignments = _valid_plan_payload()
    incomplete_assignments["assignments"] = incomplete_assignments["assignments"][:1]
    _expect_invalid(
        lambda: SceneMultiviewPlanItem.model_validate(incomplete_assignments),
        "cover every planned shot exactly once",
    )

    mismatched_primary = _valid_plan_payload()
    mismatched_primary["assignments"][0]["primary_view_id"] = "scene_001_view_02"
    _expect_invalid(
        lambda: SceneMultiviewPlanItem.model_validate(mismatched_primary),
        "does not match its planned primary view",
    )

    generated_payload = _valid_generated_payload(output_dir)
    generated = SceneMultiviewImageGenerationItem.model_validate(generated_payload)
    assert len(generated.views) == 4
    node = object.__new__(SceneMultiviewImageGenerationNode)
    node.layout = _ExistingProjectLayout()
    assert node._scene_item_reusable(
        output_dir,
        generated,
        plan,
        "generation-fingerprint",
    )
    stale_plan_package = generated.model_copy(update={"plan_fingerprint": "stale-plan"})
    assert not node._scene_item_reusable(
        output_dir,
        stale_plan_package,
        plan,
        "generation-fingerprint",
    )
    mismatched_view_package = generated.model_copy(
        update={
            "views": [
                *generated.views[:3],
                generated.views[3].model_copy(update={"view_id": "unexpected_view"}),
            ]
        }
    )
    assert not node._scene_item_reusable(
        output_dir,
        mismatched_view_package,
        plan,
        "generation-fingerprint",
    )

    generated_missing_view = _valid_generated_payload(output_dir)
    generated_missing_view["views"] = generated_missing_view["views"][:3]
    _expect_invalid(
        lambda: SceneMultiviewImageGenerationItem.model_validate(generated_missing_view)
    )

    generated_duplicate_index = _valid_generated_payload(output_dir)
    generated_duplicate_index["views"][3]["view_index"] = 3
    _expect_invalid(
        lambda: SceneMultiviewImageGenerationItem.model_validate(generated_duplicate_index),
        "unique view_index values 1, 2, 3, 4",
    )

    generated_duplicate_id = _valid_generated_payload(output_dir)
    generated_duplicate_id["views"][3]["view_id"] = "scene_001_view_03"
    _expect_invalid(
        lambda: SceneMultiviewImageGenerationItem.model_validate(generated_duplicate_id),
        "exactly four unique view_id values",
    )

    prompt_path = (
        SRC
        / "autodrama"
        / "prompts"
        / "scene_multiview_image_generation"
        / "default.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")
    required_prompt_fragments = (
        "继承 Image 1 的太阳方向",
        "阴影投射方向",
        "整体色温",
        "不得按格重新打光、重新调色",
        "只保留其材质、轮廓和几何结构",
        "不得生成可辨识文字",
        "不得生成类似文字的字形或伪字",
    )
    for fragment in required_prompt_fragments:
        assert fragment in prompt, fragment

    summary = {
        "status": "passed",
        "checks": [
            "exactly four unique planned and generated views",
            "view_index set is exactly 1 through 4",
            "assignments reference valid views and cover planned shots",
            "assignment primary views agree with per-view shot plans",
            "reuse rejects stale plans and view mappings",
            "shared lighting and color-temperature prompt contract",
            "signage geometry-only and no pseudo-text prompt contract",
        ],
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["report_path"] = str(report_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check scene multiview package integrity")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "scene-multiview-package-smoke",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    tmp_root = (ROOT / ".tmp").resolve()
    if output_dir != tmp_root and tmp_root not in output_dir.parents:
        raise ValueError(f"output-dir must stay under {tmp_root}: {output_dir}")
    print(json.dumps(run(output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
