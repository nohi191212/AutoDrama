from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import Role, RoleAppearance, RoleboardPromptModelOutput  # noqa: E402
from autodrama.workflows.nodes import (  # noqa: E402
    AVAILABLE_PREGEN_NODE_NAMES,
    DEFERRED_PREGEN_NODE_NAMES,
    PREGEN_NODE_NAMES,
)
from autodrama.workflows.nodes.role_nodes import ROLE_NODE_NAMES, RoleboardPromptNode  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import (  # noqa: E402
    STATIC_ASSET_NODE_NAMES,
    RoleAppearanceGenerationBase,
    RoleboardGenerationNode,
)
from autodrama.workflows.nodes.storyboard_asset_nodes import (  # noqa: E402
    STORYBOARD_ASSET_NODE_NAMES,
    StoryboardBBoxDetectionNode,
    StoryboardGenerationNode as PregenStoryboardGenerationNode,
    StoryboardPanelCropNode,
    StoryboardPromptNode,
)
from autodrama.workflows.nodes.voice_nodes import VOICE_NODE_NAMES, RoleVoiceSelectNode  # noqa: E402
from autodrama.workflows.pregen import EPISODE_SCOPED_PREGEN_ONLY_NODES, ROLE_SCOPED_PREGEN_ONLY_NODES  # noqa: E402


class _RoleboardPromptProbe(RoleAppearanceGenerationBase):
    def roleboard_style_prefix(self, **kwargs) -> str:  # type: ignore[override]
        del kwargs
        return "STYLE PREFIX"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    require(RoleboardPromptNode.name == "roleboard_prompt", "roleboard prompt node name mismatch")
    require(RoleboardGenerationNode.name == "roleboard_generation", "roleboard generation node name mismatch")
    require(StoryboardPromptNode.name == "storyboard_prompt", "storyboard prompt node name mismatch")
    require(PregenStoryboardGenerationNode.name == "storyboard_generation", "storyboard generation node name mismatch")
    require(StoryboardBBoxDetectionNode.name == "storyboard_bbox_detection", "storyboard bbox node name mismatch")
    require(StoryboardPanelCropNode.name == "storyboard_panel_crop", "storyboard panel crop node name mismatch")
    require(RoleVoiceSelectNode.name == "role_voice_select", "role voice select node name mismatch")
    require("roleboard_prompt" in ROLE_NODE_NAMES, "roleboard_prompt missing from role node names")
    require("roleboard_generation" in STATIC_ASSET_NODE_NAMES, "roleboard_generation missing from static names")
    require(
        STORYBOARD_ASSET_NODE_NAMES
        == [
            "storyboard_prompt",
            "storyboard_generation",
            "storyboard_bbox_detection",
            "storyboard_panel_crop",
        ],
        "storyboard asset node names mismatch",
    )
    require("role_voice_select" in VOICE_NODE_NAMES, "role_voice_select missing from voice names")

    expected_visual_voice_chain = [
        "roleboard_prompt",
        "roleboard_generation",
        "storyboard_prompt",
        "storyboard_generation",
        "storyboard_bbox_detection",
        "storyboard_panel_crop",
        "role_voice_select",
        "role_voice_generation",
    ]
    actual_visual_voice_chain = [
        node_name
        for node_name in PREGEN_NODE_NAMES
        if node_name.startswith("roleboard_")
        or node_name.startswith("storyboard_")
        or node_name.startswith("role_voice_")
    ]
    require(actual_visual_voice_chain == expected_visual_voice_chain, "roleboard/storyboard/voice node chain mismatch")
    require(PREGEN_NODE_NAMES[-1] == "role_voice_generation", "default pregen should stop at role_voice_generation")
    require(
        not any(node_name in PREGEN_NODE_NAMES for node_name in DEFERRED_PREGEN_NODE_NAMES),
        "deferred prop/layout/BGM nodes should be hidden from default pregen",
    )
    require(
        all(node_name in AVAILABLE_PREGEN_NODE_NAMES for node_name in DEFERRED_PREGEN_NODE_NAMES),
        "deferred prop/layout/BGM nodes should remain available for manual --only runs",
    )
    require(
        PREGEN_NODE_NAMES.index("roleboard_prompt")
        < PREGEN_NODE_NAMES.index("roleboard_generation")
        < PREGEN_NODE_NAMES.index("storyboard_prompt")
        < PREGEN_NODE_NAMES.index("storyboard_generation")
        < PREGEN_NODE_NAMES.index("storyboard_bbox_detection")
        < PREGEN_NODE_NAMES.index("storyboard_panel_crop")
        < PREGEN_NODE_NAMES.index("role_voice_select")
        < PREGEN_NODE_NAMES.index("role_voice_generation"),
        "roleboard/storyboard/voice pregen order mismatch",
    )
    require(
        {
            "roleboard_prompt",
            "roleboard_generation",
            "storyboard_prompt",
            "storyboard_generation",
            "storyboard_bbox_detection",
            "storyboard_panel_crop",
            "role_voice_select",
            "role_voice_generation",
        }.issubset(EPISODE_SCOPED_PREGEN_ONLY_NODES),
        "episode-scoped pregen set missing roleboard/storyboard/voice nodes",
    )
    require(
        ROLE_SCOPED_PREGEN_ONLY_NODES == {"role_voice_select", "role_voice_generation"},
        "role-scoped pregen set mismatch",
    )
    require(
        set(RoleboardPromptModelOutput.model_fields)
        == {"roleboard_prompt", "roleboard_negative_prompt", "voice_profile_prompt", "design_notes"},
        "roleboard prompt model output must be prompt-only",
    )

    prompt_template = (ROOT_DIR / "autodrama" / "src" / "autodrama" / "prompts" / "roleboard_prompt.md").read_text(
        encoding="utf-8"
    )
    require("角色：<角色名> | base" in prompt_template, "roleboard prompt template should require role label text")
    require("除指定角色名和视图标签外" in prompt_template, "roleboard prompt template should allow only designated labels")

    probe = object.__new__(_RoleboardPromptProbe)
    role = Role(id="role_lin_zhou", name="林舟", intro="职场青年")
    appearance = RoleAppearance(
        id="role_lin_zhou_base",
        role_id=role.id,
        name="base",
        roleboard_prompt="旧提示词片段：干净设计板背景，无字幕、水印、logo 或可读文字。",
        roleboard_negative_prompt="变脸，可读文字",
    )
    generation_prompt = probe.roleboard_prompt_for_generation(
        role=role,
        appearance=appearance,
        style_reference_count=0,
        key_vision_reference_index=1,
    )
    require("角色：林舟 | base" in generation_prompt, "roleboard generation prompt should include concrete role label")
    require("正面、侧面、背面、头部、表情、动作、服装细节、配饰细节" in generation_prompt, "view labels missing")
    require("除指定角色名和视图标签外" in generation_prompt, "label-only negative constraint missing")
    require(
        generation_prompt.index("旧提示词片段") < generation_prompt.index("生成一张角色身份板"),
        "final roleboard requirement should come after model-generated base prompt",
    )
    print("roleboard_pregen_contract_smoke=ok")


if __name__ == "__main__":
    main()
