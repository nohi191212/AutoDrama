from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    ClipSegment,
    ClipToShotsModelOutput,
    Prop,
    PropAsset,
    Role,
    RoleAppearance,
)
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.shot_asset_nodes import ShotAssetNodeBase  # noqa: E402


OUTPUT_DIR = ROOT / ".tmp" / "clip_to_shots_lean_contract_smoke"


def role(role_id: str, name: str, reference_id: str) -> tuple[Role, RoleAppearance]:
    appearance = RoleAppearance(
        id=f"{role_id}_appearance_base",
        role_id=role_id,
        name="base",
        identity_invariants=[f"stable identity for {name}"],
        asset_id=reference_id,
    )
    return (
        Role(
            id=role_id,
            name=name,
            intro=name,
            appearances={"base": appearance},
        ),
        appearance,
    )


def model_payload() -> dict[str, object]:
    return {
        "shots": [
            {
                "video_prompt": (
                    "An eye-level medium-wide shot looks along the stone stair axis as Ye Fan pauses "
                    "on the third step, leaning on his wooden staff, while Li Dehai catches up one step "
                    "below and blocks his path; their concerned exchange begins immediately, the camera "
                    "holds locked, and both settle facing each other beneath the gate."
                ),
                "ref_ids": ["ref_role_叶凡", "ref_role_李德海"],
                "duration_seconds": 2,
            },
            {
                "video_prompt": (
                    "A low medium-close view from beside the gate post frames Ye Fan upright behind the "
                    "wooden staff as he tightens his grip and raises his eyes past Li Dehai toward the gate; "
                    "a restrained push-in follows the lifted gaze and ends as his quiet resolve settles."
                ),
                "ref_ids": ["ref_role_叶凡", "ref_prop_木杖"],
                "duration_seconds": 15,
            },
        ]
    }


def main() -> None:
    source = (
        "旁白：天剑宗杂役叶凡，扫地八十年，挑水八十年。\n"
        "李德海（不解）：「老叶，你怎么想的？」\n"
        "叶凡（低沉）：「我还有个执念。」\n"
        "△叶凡指尖拂过花瓣，柔软的触感带着细微湿意；再捏住一片叶子，叶脉清晰，"
        "边缘划过皮肤时有轻轻的痒。她猛地缩回手，又忍不住再次触碰。"
    )
    ye_fan, ye_fan_appearance = role("role_叶凡", "叶凡", "ref_role_叶凡")
    li_dehai, li_dehai_appearance = role("role_李德海", "李德海", "ref_role_李德海")
    staff_asset = PropAsset(
        id="prop_木杖_asset_base",
        prop_id="prop_木杖",
        name="base",
        desc="weathered wooden walking staff",
        asset_id="ref_prop_木杖",
    )
    staff = Prop(
        id="prop_木杖",
        name="木杖",
        intro="叶凡支撑身体的旧木杖",
        assets={"base": staff_asset},
    )
    clip = ClipSegment(
        text=source,
        scene_id="layout_外门牌楼",
        role_names=["叶凡", "李德海"],
        prop_names=["木杖"],
        allocated_seconds=16,
        event_ids=["event_001"],
    )
    assets = {
        "ref_role_叶凡": {
            "kind": "roleboard",
            "label": "叶凡",
            "object": (ye_fan, ye_fan_appearance),
        },
        "ref_role_李德海": {
            "kind": "roleboard",
            "label": "李德海",
            "object": (li_dehai, li_dehai_appearance),
        },
        "ref_prop_木杖": {
            "kind": "prop",
            "label": "木杖",
            "object": (staff, staff_asset),
        },
    }

    planning_text = ShotAssetNodeBase._visual_planning_text(source)
    assert "叶凡（低沉）：「我还有个执念。」" in planning_text
    assert "一滴露水沿花瓣滚动" in planning_text

    entity_index = "\n".join(
        (
            "ref_role_叶凡: character reference for 叶凡 (role_叶凡), appearance base",
            "ref_role_李德海: character reference for 李德海 (role_李德海), appearance base",
            "ref_prop_木杖: prop reference for 木杖 (prop_木杖); weathered wooden walking staff",
        )
    )
    prompt = PromptStore(SRC / "autodrama" / "prompts").render(
        "clip_to_shots",
        scene_description="Stone stairs and an outer-sect gate define the spatial anchor.",
        previous_context="",
        clip_text=planning_text,
        next_context="",
        entity_index=entity_index,
        foreground_reference_budget="2",
    )
    assert "相邻镜头默认通过硬切衔接" in prompt
    assert "只有内容确实能在自然速度下完成时才使用常规的 1–6 秒" in prompt
    assert "7–15 秒长镜头" in prompt
    assert "可用时长" not in prompt
    assert "16.0" not in prompt
    assert "scene_id" not in prompt
    assert "`ref_ids`" in prompt

    model_output = ClipToShotsModelOutput.model_validate(model_payload())
    shots = ShotAssetNodeBase._compile_model_output(
        model_output,
        assets,
        clip_id="episode_001_clip_001",
        clip_index=1,
        scene_id=clip.scene_id,
        reference_budget=3,
    )
    assert [shot.duration_seconds for shot in shots] == [2, 15]
    assert shots[0].ref_ids == ["ref_role_叶凡", "ref_role_李德海"]
    assert shots[1].ref_ids == ["ref_role_叶凡", "ref_prop_木杖"]
    assert all(1 + len(shot.ref_ids) <= 3 for shot in shots)
    invalid_payload = model_payload()
    invalid_payload["shots"][0]["video_prompt"] = "Ye Fan pauses, then cut to Li Dehai's reverse angle."
    try:
        ShotAssetNodeBase._compile_model_output(
            ClipToShotsModelOutput.model_validate(invalid_payload),
            assets,
            clip_id="episode_001_clip_001",
            clip_index=1,
            scene_id=clip.scene_id,
            reference_budget=3,
        )
    except ValueError as exc:
        assert "internal cut" in str(exc)
    else:
        raise AssertionError("internal cut contract was not rejected")

    legacy_payload = model_payload()
    legacy_payload["shots"][0]["purpose"] = "obsolete duplicate field"
    try:
        ClipToShotsModelOutput.model_validate(legacy_payload)
    except ValueError:
        pass
    else:
        raise AssertionError("obsolete clip_to_shots fields were not rejected")

    summary = {
        "durations": [shot.duration_seconds for shot in shots],
        "references": [shot.ref_ids for shot in shots],
        "internal_cut_rejected": True,
        "obsolete_fields_rejected": True,
        "prompt_field_count": 3,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
