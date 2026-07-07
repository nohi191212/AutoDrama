from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import load_settings
from autodrama.core.schemas import Layout, LayoutDedupeReviewOutput, LayoutExtractOutput, LayoutPromptOutput
from autodrama.workflows.nodes.static_asset_nodes import LayoutImageGenerationNode


class FakeProjectLayout:
    def absolute_project_path(self, project_dir: Path, path: str | Path) -> Path:
        return project_dir / path


def main() -> None:
    settings = load_settings(ROOT / "config.yaml")
    assert "layout_prompt" in settings.nodes
    assert "layout_design" not in settings.nodes
    assert settings.nodes["layout_image_generation"].params["concurrency"] == 3
    assert settings.nodes["layout_finalize"].params["max_iterations"] == 8
    example_settings = load_settings(ROOT / "config.yaml.example")
    assert "layout_prompt" in example_settings.nodes
    assert example_settings.nodes["layout_image_generation"].params["concurrency"] == 3

    extract = LayoutExtractOutput.model_validate(
        {
            "layouts": [
                {
                    "name": "小燕家大厅",
                    "group": "小燕家大厅",
                    "asset_role": "base",
                    "reference_asset_name": "",
                    "episode_keys": ["episode_001"],
                    "source_chapters": ["第1章"],
                    "brief": "小燕与母亲发生正面冲突的家庭大厅，老旧沙发、暖色顶灯和入户门构成稳定空间。",
                    "space_features": ["老旧沙发", "暖色顶灯", "入户门"],
                    "state_delta": "",
                },
                {
                    "name": "小燕家大厅_黑夜",
                    "group": "小燕家大厅",
                    "asset_role": "variant",
                    "reference_asset_name": "小燕家大厅",
                    "episode_keys": ["episode_002"],
                    "source_chapters": ["第2章"],
                    "brief": "保持小燕家大厅结构不变，只改为黑夜停灯后的冷蓝月光和电视微光状态。",
                    "space_features": [],
                    "state_delta": "灯光关闭，只剩窗外冷蓝月光和电视微光。",
                },
            ],
            "notes": ["保留黑夜状态变体"],
        }
    )
    assert [item.asset_role for item in extract.layouts] == ["base", "variant"]
    assert extract.layouts[1].reference_asset_name == "小燕家大厅"

    dedupe = LayoutDedupeReviewOutput.model_validate(
        {"layouts": [item.model_dump() for item in extract.layouts], "merge_notes": ["无重复空间"]}
    )
    assert dedupe.layouts[0].group == "小燕家大厅"

    prompt = LayoutPromptOutput.model_validate(
        {
            "layout_prompts": [
                {
                    "name": "小燕家大厅",
                    "group": "小燕家大厅",
                    "asset_role": "base",
                    "reference_asset_name": "",
                    "prompt_type": "text_to_image",
                    "prompt": "无人空场景资产图，小燕家大厅，老旧沙发、暖色顶灯和入户门清晰。",
                },
                {
                    "name": "小燕家大厅_黑夜",
                    "group": "小燕家大厅",
                    "asset_role": "variant",
                    "reference_asset_name": "小燕家大厅",
                    "prompt_type": "image_edit",
                    "prompt": "以小燕家大厅基准图为参考，保持结构不变，只改为黑夜停灯状态。",
                },
            ]
        }
    )
    assert [item.prompt_type for item in prompt.layout_prompts] == ["text_to_image", "image_edit"]

    base = Layout(
        id="layout_小燕家大厅",
        name="小燕家大厅",
        group="小燕家大厅",
        asset_role="base",
        reference_asset_name="",
        desc="家庭大厅",
        prompt="base",
        episode_keys=["episode_001"],
        asset_id="layout_小燕家大厅",
        asset_path="assets/images/layouts/layout_小燕家大厅.png",
    )
    variant = Layout(
        id="layout_小燕家大厅_黑夜",
        name="小燕家大厅_黑夜",
        group="小燕家大厅",
        asset_role="variant",
        reference_asset_name="小燕家大厅",
        desc="相比原场景变暗",
        prompt="variant",
        episode_keys=["episode_002"],
        state_delta="灯光关闭",
    )
    stages = LayoutImageGenerationNode._layout_generation_stages(
        [variant, base], {base.name: base, variant.name: variant}
    )
    assert [(stage_name, [layout.name for layout in stage_layouts]) for stage_name, stage_layouts in stages] == [
        ("base", ["小燕家大厅"]),
        ("variant", ["小燕家大厅_黑夜"]),
    ]

    node = LayoutImageGenerationNode.__new__(LayoutImageGenerationNode)
    node.layout = FakeProjectLayout()
    refs = node._layout_reference_refs(Path("project"), variant, {base.name: base, variant.name: variant})
    assert len(refs) == 1
    assert refs[0].metadata["reference_role"] == "base_layout_for_variant"
    assert refs[0].metadata["layout_name"] == "小燕家大厅"

    missing_asset_base = base.model_copy(update={"asset_path": None, "asset_url": None})
    try:
        node._layout_reference_refs(Path("project"), variant, {missing_asset_base.name: missing_asset_base, variant.name: variant})
    except ValueError as exc:
        assert "requires generated base image" in str(exc)
    else:
        raise AssertionError("variant layout should require a generated base image")

    prompt_dir = ROOT / "autodrama" / "src" / "autodrama" / "prompts"
    for prompt_name in (
        "layout_extract.md",
        "layout_finalize.md",
        "layout_prompt.md",
        "layout_prompt_toapi_gpt_image_2.md",
        "layout_prompt_rightcode_gpt_image_2.md",
        "layout_prompt_volcengine_seedream.md",
    ):
        text = (prompt_dir / prompt_name).read_text(encoding="utf-8")
        assert "generated_layout_intro" not in text
    assert "{{existing_layouts}}" in (prompt_dir / "layout_extract.md").read_text(encoding="utf-8")
    assert "{{layouts}}" in (prompt_dir / "layout_finalize.md").read_text(encoding="utf-8")
    assert "{{layouts}}" in (prompt_dir / "layout_prompt.md").read_text(encoding="utf-8")

    class Binding:
        params = {"concurrency": 4}

    class Provider:
        model_binding = Binding()

    assert LayoutImageGenerationNode._generation_concurrency(Provider()) == 4

    print("layout contract smoke passed")


if __name__ == "__main__":
    main()
