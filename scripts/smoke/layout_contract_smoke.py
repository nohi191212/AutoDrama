from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import load_settings
from autodrama.core.schemas import Layout, LayoutDedupeReviewOutput, LayoutExtractOutput, LayoutPromptOutput
from autodrama.workflows.nodes.static_asset_nodes import LayoutImageGenerationNode


def main() -> None:
    settings = load_settings(ROOT / "huyao.yaml")
    assert "layout_prompt" in settings.nodes
    assert "layout_design" not in settings.nodes
    assert settings.nodes["layout_image_generation"].params["concurrency"] == 3
    assert settings.nodes["layout_dedupe_review"].params["max_iterations"] == 8
    example_settings = load_settings(ROOT / "config.yaml.example")
    assert "layout_prompt" in example_settings.nodes
    assert example_settings.nodes["layout_image_generation"].params["concurrency"] == 3

    legacy_extract = LayoutExtractOutput.model_validate(
        {
            "layouts": [
                {
                    "name": "小燕家大厅",
                    "brief": "小燕与母亲发生正面冲突的家庭大厅。",
                    "appearance_notes": ["老旧沙发", "暖色顶灯"],
                }
            ]
        }
    )
    assert legacy_extract.generated_layout_intro == {"小燕家大厅": "小燕与母亲发生正面冲突的家庭大厅。"}

    legacy_dedupe = LayoutDedupeReviewOutput.model_validate(
        {
            "layouts": [
                {
                    "name": "小燕家大厅_黑夜",
                    "desc": "相比小燕家大厅，灯光关闭，只剩窗外冷蓝月光和电视微光。",
                    "prompt": "旧提示词应被忽略",
                }
            ],
            "merge_notes": ["保留状态场景"],
        }
    )
    assert legacy_dedupe.generated_layout_intro == {
        "小燕家大厅_黑夜": "相比小燕家大厅，灯光关闭，只剩窗外冷蓝月光和电视微光。"
    }

    legacy_prompt = LayoutPromptOutput.model_validate(
        {
            "layouts": [
                {
                    "name": "小燕家大厅",
                    "prompt": "专业空场景提示词。",
                }
            ]
        }
    )
    assert legacy_prompt.layout_prompts == {"小燕家大厅": "专业空场景提示词。"}

    base = Layout(id="layout_小燕家大厅", name="小燕家大厅", desc="家庭大厅", prompt="base", episode_keys=[])
    state = Layout(
        id="layout_小燕家大厅_黑夜",
        name="小燕家大厅_黑夜",
        desc="相比原场景变暗",
        prompt="state",
        episode_keys=[],
    )
    batches = LayoutImageGenerationNode._ordered_layout_batches([state, base], {base.name: base, state.name: state})
    assert [[layout.name for layout in batch] for batch in batches] == [["小燕家大厅"], ["小燕家大厅_黑夜"]]
    stages = LayoutImageGenerationNode._layout_generation_stages([state, base], {base.name: base, state.name: state})
    assert [(stage_name, [layout.name for layout in stage_layouts]) for stage_name, stage_layouts in stages] == [
        ("base", ["小燕家大厅"]),
        ("variant", ["小燕家大厅_黑夜"]),
    ]

    class Binding:
        params = {"concurrency": 4}

    class Provider:
        model_binding = Binding()

    assert LayoutImageGenerationNode._generation_concurrency(Provider()) == 4

    for prompt_name in (
        "layout_prompt.md",
        "layout_prompt_toapi_gpt_image_2.md",
        "layout_prompt_rightcode_gpt_image_2.md",
        "layout_prompt_volcengine_seedream.md",
    ):
        assert (ROOT / "autodrama" / "src" / "autodrama" / "prompts" / prompt_name).is_file()

    print("layout contract smoke passed")


if __name__ == "__main__":
    main()
