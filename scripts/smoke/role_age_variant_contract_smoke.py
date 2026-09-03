from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    Role,
    RoleAppearance,
    RoleAppearanceExtractItem,
    RoleExtractItem,
    SemanticProvenance,
    StaticAssetGenerationItem,
)
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.image_audit_nodes import RoleboardImageAuditNode  # noqa: E402
from autodrama.workflows.nodes.role_nodes import RolePrimaryExtractNode  # noqa: E402


def provenance(evidence: str) -> SemanticProvenance:
    return SemanticProvenance(source="model", evidence=[evidence], confidence=1.0)


def extracted_appearance(
    *,
    name: str,
    asset_role: str,
    age_band: str,
    identity: list[str],
    reference_asset_name: str | None = None,
    time_period: str | None = None,
    valid_from_event: str | None = None,
    valid_to_event: str | None = None,
) -> RoleAppearanceExtractItem:
    return RoleAppearanceExtractItem(
        name=name,
        asset_role=asset_role,
        reference_asset_name=reference_asset_name,
        episode_keys=["episode_001"],
        age_band=age_band,
        time_period=time_period,
        identity_invariants=identity,
        wardrobe=["灰布衣"],
        valid_from_event=valid_from_event,
        valid_to_event=valid_to_event,
        provenance=provenance(age_band),
    )


def role_item(appearances: list[RoleAppearanceExtractItem]) -> RoleExtractItem:
    return RoleExtractItem(
        name="叶凡",
        role_tier="primary",
        episode_keys=["episode_001"],
        appearance_assets=appearances,
        visual_reuse_required=True,
    )


def check_existing_role_can_gain_age_variant() -> None:
    elderly = extracted_appearance(
        name="base",
        asset_role="base",
        age_band="老年",
        identity=["老年男性", "脸部骨相清晰"],
    )
    youth = extracted_appearance(
        name="少年时期",
        asset_role="variant",
        reference_asset_name="base",
        age_band="少年",
        time_period="八十年前",
        identity=["少年男性", "与基础造型具有同一脸部骨相血缘"],
        valid_from_event="八十年前回忆开始",
        valid_to_event="八十年前回忆结束",
    )
    target = role_item([elderly])
    incoming = role_item([elderly.model_copy(deep=True), youth])
    node = object.__new__(RolePrimaryExtractNode)
    roles = [target]
    roles_by_key = {node.role_name_key(target.name): target}

    new_roles, new_appearances = node._append_new_roles(
        roles=roles,
        roles_by_key=roles_by_key,
        output_roles=[incoming],
        episode_keys=["episode_001"],
    )
    assert (new_roles, new_appearances) == (0, 1)
    assert [appearance.name for appearance in target.appearance_assets] == ["base", "少年时期"]

    new_roles, new_appearances = node._append_new_roles(
        roles=roles,
        roles_by_key=roles_by_key,
        output_roles=[incoming],
        episode_keys=["episode_001"],
    )
    assert (new_roles, new_appearances) == (0, 0)

    time_scoped_only = extracted_appearance(
        name="青年时期",
        asset_role="variant",
        reference_asset_name="base",
        age_band="青年",
        time_period="多年以前",
        identity=["青年男性", "与基础造型具有同一脸部骨相血缘"],
    )
    assert time_scoped_only.time_period == "多年以前"


def check_age_variant_audit_uses_base_reference() -> None:
    base = RoleAppearance(
        id="role_叶凡_appearance_base",
        role_id="role_叶凡",
        name="base",
        asset_role="base",
        age_band="老年",
        identity_invariants=["老年男性", "脸部骨相清晰"],
        wardrobe=["灰布长衫"],
        asset_id="role_叶凡_appearance_base_roleboard",
        asset_url="https://example.invalid/elderly-yefan.png",
    )
    youth = RoleAppearance(
        id="role_叶凡_appearance_少年时期",
        role_id="role_叶凡",
        name="少年时期",
        asset_role="variant",
        reference_asset_name="base",
        age_band="少年",
        time_period="八十年前",
        valid_from_event="八十年前回忆开始",
        valid_to_event="八十年前回忆结束",
        identity_invariants=["少年男性", "与基础造型具有同一脸部骨相血缘"],
        wardrobe=["灰布衣"],
        asset_id="role_叶凡_appearance_少年时期_roleboard",
        asset_url="https://example.invalid/youth-yefan.png",
    )
    role = Role(
        id="role_叶凡",
        name="叶凡",
        intro="同一角色的老年与少年时期",
        appearances={"base": base, "少年时期": youth},
    )
    state = SimpleNamespace(
        project_id="age_variant_smoke",
        roles={role.id: role},
        metadata={"visual_style_prompt": "统一的写实三维电影视觉方向"},
    )
    item = StaticAssetGenerationItem(
        asset_id=youth.asset_id or "",
        asset_type="roleboard",
        owner_id=role.id,
        name="叶凡 / 少年时期",
        prompt="生成少年叶凡三视图。",
        asset_url=youth.asset_url,
        provider="smoke",
        model="smoke",
    )

    node = object.__new__(RoleboardImageAuditNode)
    node.layout = SimpleNamespace(existing_project_file=lambda _project_dir, _value: None)
    node.prompts = PromptStore()
    refs = node._audit_refs(ROOT / ".tmp" / "role_age_variant_smoke", state, item)
    assert len(refs) == 2
    assert refs[0].id == youth.asset_id
    assert refs[1].id == base.asset_id
    assert refs[1].metadata.get("reference_role") == "same_role_identity"
    assert refs[1].metadata.get("reference_index") == 2

    prompt = node._render_audit_request(state, item)
    assert "图片2是同一角色“叶凡”的基础造型“base”" in prompt
    assert "图片1的目标阶段是“少年；八十年前”" in prompt
    assert "不能因为出现合理的年龄变化而判为身份漂移" in prompt
    assert "不得照搬图片2只属于原阶段的皱纹" in prompt


def check_prompt_contract() -> None:
    primary_template = (
        ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "role_extract_primary" / "default.md"
    ).read_text(encoding="utf-8")
    assert "儿童、少年、青年、中年、老年" in primary_template
    assert "不能指望后续提示词把不匹配年龄的参考图直接改成目标年龄" in primary_template
    assert "包含原有基础造型和新增变化造型" in primary_template
    assert '必须返回对象 {"roles": []}' in primary_template


def main() -> int:
    check_existing_role_can_gain_age_variant()
    check_age_variant_audit_uses_base_reference()
    check_prompt_contract()
    print("role age variant contract smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
