from __future__ import annotations

from pathlib import Path
import sys



ROOT = Path(__file__).resolve().parents[2]

SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.static_asset_nodes import RoleAppearanceGenerationBase


def render_roleboard_templates() -> None:
    prompts = PromptStore()
    variables = {
        "role_extract_item": "{}",
        "role_index": "[]",
        "role_novel_extract": "{}",
        "role_novel_full": "{}",
        "project_context": "{}",
        "visual_tone": "克制写实的短剧摄影，低饱和色彩，干净留白，人物质感清晰。",
        "character_intro": (
            '{"name":"陈伶","brief":"归来的红衣少年",'
            '"appearance_notes":["苍白窄长脸","湿乱黑发","大红旧戏袍","赤脚"],'
            '"has_dialogue":true}'
        ),
        "key_vision_asset": "{}",
        "appearance_asset": '{"appearance_name":"base","asset_role":"base","appearance_desc":"稳定基础造型","clothing":"素色日常装","visual_features":"清晰脸型和发型"}',
        "roleboard_style_prompt": "统一角色板风格",
        "roleboard_view_requirement": "角色板视图要求",
        "roleboard_image_provider": "aibox",
        "roleboard_image_model": "gpt-image-2-guan",
    }
    template_names = [
        "roleboard_prompt/default",
        "roleboard_prompt/toapi_gpt_image_2",
        "roleboard_prompt/toapi_gpt_image_2_high",
        "roleboard_prompt/aibox_gpt_image_2_guan",
        "roleboard_prompt/rightcode_gpt_image_2",
        "roleboard_prompt/rightcode_gpt_image_2_vip",
        "roleboard_prompt/volcengine_doubao_seedream_5_0_260128",
    ]
    for template_name in template_names:
        rendered = prompts.render(template_name, **variables)
        if "roleboard_prompt" not in rendered:
            raise AssertionError(f"{template_name} did not render expected schema text")


def validate_configs() -> None:
    for config_name in ("config.yaml.example", "config.yaml"):
        settings = load_settings(ROOT / config_name)
        params = settings.nodes["roleboard_image_generation"].params
        if "roleboard_prompt_template" not in params:
            raise AssertionError(f"{config_name} missing roleboard_prompt_template")
        if int(params.get("roleboard_image_generation_concurrency", 0)) < 1:
            raise AssertionError(f"{config_name} missing roleboard_image_generation_concurrency")


def validate_concurrency_resolution() -> None:
    class Binding:
        params = {"roleboard_image_generation_concurrency": 3}

    class Provider:
        model_binding = Binding()

    if RoleAppearanceGenerationBase.roleboard_image_generation_concurrency(Provider()) != 3:
        raise AssertionError("roleboard_image_generation_concurrency was not read from binding params")

    class HighBinding:
        params = {"roleboard_image_generation_concurrency": 99}

    class HighProvider:
        model_binding = HighBinding()

    if RoleAppearanceGenerationBase.roleboard_image_generation_concurrency(HighProvider()) != 5:
        raise AssertionError("roleboard_image_generation_concurrency was not capped")


def main() -> None:
    render_roleboard_templates()
    validate_configs()
    validate_concurrency_resolution()
    print("roleboard prompt template and concurrency smoke passed")


if __name__ == "__main__":
    main()
