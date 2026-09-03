from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import Layout, Role, RoleAppearance, RoleExtractItem  # noqa: E402
from autodrama.services.role_service import RoleService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import (  # noqa: E402
    LayoutImageGenerationNode,
    RoleAppearanceGenerationBase,
)


def check_layout_reference_contract() -> None:
    node = object.__new__(LayoutImageGenerationNode)
    node.repo = SimpleNamespace(
        settings=SimpleNamespace(config_path=ROOT / "saodi.yaml")
    )
    layout = Layout(
        id="layout_smoke",
        name="smoke",
        desc="empty smoke scene",
        prompt="Create the target scene.",
    )
    refs = node._layout_reference_refs(ROOT / "outputs" / "smoke", layout, {})
    expected = (
        ROOT / ".assets" / "image_templates" / "scene_spatial_anchor_template.png"
    ).resolve()
    assert len(refs) == 1
    assert Path(str(refs[0].path)).resolve() == expected
    assert refs[0].metadata.get("reference_role") == "spatial_template"

    prompt = node._layout_prompt_for_generation(layout, layout.prompt)
    assert "Reference image 1 defines only the spatial-anchor sheet grammar" in prompt
    assert "physically opposite corners" in prompt
    assert "do not use a horizontal mirror" in prompt
    assert node._layout_prompt_for_generation(layout, prompt) == prompt


def check_roleboard_config_contract() -> None:
    settings = load_settings(ROOT / "saodi.yaml")
    expected = (
        ROOT / ".assets" / "image_templates" / "roleboard_template.png"
    ).resolve()
    assert settings.generation.roleboard_spatial_template_path == expected
    roleboard_params = settings.nodes["roleboard_image_generation"].params
    assert "anchor_role" not in roleboard_params
    assert "anchor_appearance" not in roleboard_params


def check_roleboard_prompt_contract() -> None:
    prompts = PromptStore()
    service = RoleService(prompts)
    role_item = RoleExtractItem.model_construct(
        name="示例角色",
        role_tier="primary",
        aliases=[],
        episode_keys=[],
        source_chapters=[],
        brief=None,
        appearance_notes=[],
        appearance_assets=[],
        has_dialogue=False,
        visual_reuse_required=True,
    )
    description = service.roleboard_character_description(
        role_item,
        {
            "appearance_name": "base",
            "identity_invariants": ["稳定外形事实"],
            "wardrobe": ["动态服饰事实"],
        },
    )
    assert "identity_invariants" not in description
    assert "wardrobe" not in description

    forbidden = {
        "identity_invariants",
        "wardrobe",
        "roleboard_prompt",
        "roleboard_negative_prompt",
        "voice_profile_prompt",
        "design_notes",
        "水墨二维国漫",
        "3D国漫",
    }
    template_dir = prompts.prompt_dir / "roleboard_prompt"
    for template_path in template_dir.glob("*.md"):
        template = template_path.read_text(encoding="utf-8")
        assert not forbidden.intersection(template.split()), template_path
        assert all(token not in template for token in forbidden), template_path
        assert "资料没有规定但完整角色设计需要的稳定可见部分必须做具体" in template
        assert "脸型与骨相" in template
        assert "标准美型" in template
        assert not any(line.lstrip().startswith(("- ", "* ", "1. ")) for line in template.splitlines())

    rendered = prompts.render(
        "roleboard_prompt",
        variant="aibox_gpt_image_2_guan",
        roleboard_character_description=description,
        roleboard_style_prompt="由项目动态提供的视觉方向",
        roleboard_view_requirement=service.roleboard_view_requirement(),
    )
    assert "由项目动态提供的视觉方向" in rendered
    assert "稳定外形事实" in rendered
    assert "眉眼结构" in rendered

    audit_prompt = prompts.render(
        "roleboard_image_audit",
        asset_name="示例角色 / base",
        expectation="同一角色的正面、真侧面和背面三视图",
        current_prompt="示例角色身份板提示词",
    )
    assert "具体可辨的五官结构" in audit_prompt
    assert "标准美型、通用脸" in audit_prompt
    assert "字段要求" not in audit_prompt
    assert not any(line.lstrip().startswith(("- ", "* ", "1. ")) for line in audit_prompt.splitlines())

    base = RoleAppearance(
        id="role_smoke_base",
        role_id="role_smoke",
        name="base",
        asset_role="base",
        roleboard_prompt="由动态输入生成的角色身份板提示词。",
        asset_id="role_smoke_base_roleboard",
        asset_url="https://example.invalid/role-smoke-base.png",
    )
    variant = RoleAppearance(
        id="role_smoke_variant",
        role_id="role_smoke",
        name="variant",
        asset_role="variant",
        reference_asset_name="base",
        roleboard_prompt="由动态输入生成的同角色变体身份板提示词。",
    )
    role = Role(
        id="role_smoke",
        name="示例角色",
        intro="smoke",
        appearances={"base": base, "variant": variant},
    )
    state = SimpleNamespace(
        metadata={
            "key_vision_asset": {
                "asset_id": "key_vision",
                "asset_url": "https://example.invalid/key-vision.png",
            }
        }
    )
    node = object.__new__(RoleAppearanceGenerationBase)
    node.repo = SimpleNamespace(
        settings=SimpleNamespace(
            generation=SimpleNamespace(
                roleboard_spatial_template_path=(
                    ROOT / ".assets" / "image_templates" / "roleboard_template.png"
                ).resolve()
            )
        )
    )

    base_refs = node.roleboard_reference_refs(ROOT / "outputs" / "smoke", state, role, base)
    assert len(base_refs) == 2
    assert [ref.metadata.get("reference_role") for ref in base_refs] == [
        "spatial_template",
        "key_vision_style",
    ]
    base_prompt = node.roleboard_prompt_for_generation(
        role=role,
        appearance=base,
        refs=base_refs,
    )
    assert "参考图片1只用于" in base_prompt
    assert "参考图片2用于统一人物设计语言与视觉呈现" in base_prompt
    assert "沿用其共性的面部塑造方式" in base_prompt
    assert "不得复制其中任何具体人物的身份、具体五官" in base_prompt
    assert "不得复制其中的人物身份、脸、体型、发型、服饰" not in base_prompt
    assert "水墨二维国漫" not in base_prompt
    assert "若仍有稳定可见特征未说明" in base_prompt
    assert "通用英雄脸" in base_prompt

    variant_refs = node.roleboard_reference_refs(ROOT / "outputs" / "smoke", state, role, variant)
    assert len(variant_refs) == 3
    assert [ref.metadata.get("reference_role") for ref in variant_refs] == [
        "spatial_template",
        "key_vision_style",
        "same_role_identity",
    ]
    assert variant_refs[2].id == base.asset_id
    assert variant_refs[2].metadata.get("role_id") == role.id
    variant_prompt = node.roleboard_prompt_for_generation(
        role=role,
        appearance=variant,
        refs=variant_refs,
    )
    assert "参考图片3是同一角色的基础身份参考" in variant_prompt

    node.workflow = SimpleNamespace(_force_pregen=False)
    state.metadata["roleboard_anchor"] = {"asset_id": "obsolete_cross_role_anchor"}

    async def check_obsolete_anchor_cleanup() -> None:
        try:
            await node.generate_roleboard_assets(
                provider=SimpleNamespace(supports_reference_images=False),
                project_dir=ROOT / "outputs" / "smoke",
                state=state,
                appearances=[],
                node_name="roleboard_image_generation",
                generated_by_asset_id={},
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported provider should fail before image generation")

    asyncio.run(check_obsolete_anchor_cleanup())
    assert "roleboard_anchor" not in state.metadata


def main() -> int:
    check_layout_reference_contract()
    check_roleboard_config_contract()
    check_roleboard_prompt_contract()
    print("static asset reference and prompt contract smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
