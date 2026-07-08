from __future__ import annotations

from pathlib import Path

from autodrama.core.schemas import LayoutPropBoundaryReviewOutput
from autodrama.workflows.nodes.static_asset_nodes import STATIC_ASSET_NODE_NAMES


def main() -> None:
    output = LayoutPropBoundaryReviewOutput.model_validate(
        {
            "props": [],
            "layouts": [
                {
                    "name": "古老殿宇",
                    "group": "古老殿宇",
                    "asset_role": "base",
                    "reference_asset_name": None,
                    "episode_keys": ["episode_001"],
                    "source_chapters": ["古老殿宇-日-外"],
                    "brief": "破败古老殿宇，残垣断壁、断梁蛛网和中央石台构成初始空间。",
                    "space_features": ["残垣断壁", "断梁蛛网", "中央布满厚灰的石台"],
                    "state_delta": "",
                }
            ],
            "review_notes": ["删除误分到 prop 的殿宇石台，并入古老殿宇 space_features。"],
        }
    )
    assert not output.props
    assert output.layouts[0].space_features[-1] == "中央布满厚灰的石台"

    order = STATIC_ASSET_NODE_NAMES
    assert order.index("prop_finalize") < order.index("layout_prop_boundary_review")
    assert order.index("layout_finalize") < order.index("layout_prop_boundary_review")
    assert order.index("layout_prop_boundary_review") < order.index("prop_prompt")
    assert order.index("layout_prop_boundary_review") < order.index("layout_prompt")
    assert order.index("layout_prompt") < order.index("prop_image_generation")

    prompt_path = Path("autodrama/src/autodrama/prompts/layout_prop_boundary_review.md")
    assert prompt_path.exists(), prompt_path
    prompt_text = prompt_path.read_text(encoding="utf-8")
    assert "石台" in prompt_text
    assert "完整 `props` 和完整 `layouts`" in prompt_text


if __name__ == "__main__":
    main()