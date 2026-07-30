from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "autodrama" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autodrama.core.schemas import RoleAppearance, ShotEntityState, VisualStyleSpec
from autodrama.core.visual_contract import (
    apply_visual_style_patch,
    build_visual_style_spec,
    identity_brief,
    migrate_legacy_role_appearance,
    migrate_legacy_visual_style,
    render_character_visual_context,
    visual_style_conflicts,
)
from scripts.migrate_visual_contract_v2 import migrate_state
from autodrama.config import load_settings


def main() -> int:
    for config_name in (
        "config.chonghui_jiuba.yaml",
        "config.saodi_bashinian.yaml",
        "config.saodi_bashinian_terra_image2.yaml",
        "config.yushou_xianchao.yaml",
    ):
        settings = load_settings(REPO_ROOT / config_name)
        assert settings.generation.visual_style is not None
        assert settings.generation.visual_style.schema_version == 2

    style = build_visual_style_spec(
        {
            "schema_version": 2,
            "medium": "stylized_3d_cg",
            "render_engine_language": ["说明中同时出现二维与三维，但不能反向修改 medium。"],
            "source": "yaml",
        }
    )
    assert style.medium == "stylized_3d_cg"
    assert style.version.startswith("visual-style-v2-")
    override = apply_visual_style_patch(style, {"palette": ["reviewed palette"]})
    assert override.medium == style.medium
    assert override.source == "user_override"
    assert visual_style_conflicts(style, override) == ["palette"]

    base = RoleAppearance(
        id="role_hero_appearance_base",
        role_id="role_hero",
        identity_invariants=["胸口固定发光纹章", "银色短发"],
        wardrobe=["深色长袍"],
    )
    first_state = ShotEntityState(
        entity_id="role_hero",
        appearance_id=base.id,
        pose="站立",
        held_props=["长剑"],
    )
    second_state = ShotEntityState(
        entity_id="role_hero",
        appearance_id=base.id,
        pose="转身",
        emotion="警觉",
    )
    first = render_character_visual_context(base, first_state)
    second = render_character_visual_context(base, second_state)
    assert first["stable_identity"] == second["stable_identity"]
    assert "发光" in first["stable_identity"]
    assert "长剑" not in first["stable_identity"]
    assert "长剑" in first["current_shot_state"]
    assert "长剑" not in second["current_shot_state"]

    variant = RoleAppearance(
        id="role_hero_appearance_injured",
        role_id="role_hero",
        name="injured",
        asset_role="variant",
        reference_asset_name="base",
        valid_from_event="event_7",
        identity_invariants=["胸口固定发光纹章", "银色短发"],
        wardrobe=["破损深色长袍"],
    )
    variant_context = render_character_visual_context(
        variant,
        ShotEntityState(
            entity_id="role_hero",
            appearance_id=variant.id,
            injury="持续伤势",
        ),
    )
    assert "破损深色长袍" in variant_context["stable_identity"]

    missing = RoleAppearance(id="role_missing_appearance_base", role_id="role_missing")
    try:
        identity_brief(missing)
    except ValueError as exc:
        assert "rerun role_extract" in str(exc)
    else:
        raise AssertionError("missing structured identity must fail")

    migrated_style = migrate_legacy_visual_style(
        "旧说明同时写二维与三维。",
        medium="reviewed_medium",
    )
    assert migrated_style.medium == "reviewed_medium"
    assert migrated_style.source == "migration"
    migrated_appearance = migrate_legacy_role_appearance(
        missing,
        identity_invariants=["人工确认的稳定面貌"],
        evidence=["原始角色设定第一段"],
        confidence=0.9,
        warnings=["服装无法确定"],
    )
    assert identity_brief(migrated_appearance) == "人工确认的稳定面貌"
    assert migrated_appearance.migration_warnings == ["服装无法确定"]

    migrated_state = migrate_state(
        {
            "metadata": {"visual_style_prompt": "旧自由文本"},
            "roles": {
                "role_missing": {
                    "appearances": {
                        "base": missing.model_dump(mode="json"),
                    }
                }
            },
        },
        {
            "visual_style": {"medium": "reviewed_medium"},
            "role_appearances": {
                missing.id: {
                    "identity_invariants": ["人工确认的稳定面貌"],
                    "evidence": ["原始角色设定第一段"],
                    "confidence": 0.9,
                }
            },
        },
    )
    assert "visual_style_prompt" not in migrated_state["metadata"]
    assert migrated_state["metadata"]["visual_style_spec"]["schema_version"] == 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
