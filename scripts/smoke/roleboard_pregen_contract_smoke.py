from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import RoleboardPromptModelOutput  # noqa: E402
from autodrama.workflows.nodes import PREGEN_NODE_NAMES  # noqa: E402
from autodrama.workflows.nodes.role_nodes import ROLE_NODE_NAMES, RoleboardPromptNode  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import STATIC_ASSET_NODE_NAMES, RoleboardGenerationNode  # noqa: E402
from autodrama.workflows.nodes.voice_nodes import VOICE_NODE_NAMES, RoleVoiceSelectNode  # noqa: E402
from autodrama.workflows.pregen import EPISODE_SCOPED_PREGEN_ONLY_NODES, ROLE_SCOPED_PREGEN_ONLY_NODES  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    require(RoleboardPromptNode.name == "roleboard_prompt", "roleboard prompt node name mismatch")
    require(RoleboardGenerationNode.name == "roleboard_generation", "roleboard generation node name mismatch")
    require(RoleVoiceSelectNode.name == "role_voice_select", "role voice select node name mismatch")
    require("roleboard_prompt" in ROLE_NODE_NAMES, "roleboard_prompt missing from role node names")
    require("roleboard_generation" in STATIC_ASSET_NODE_NAMES, "roleboard_generation missing from static names")
    require("role_voice_select" in VOICE_NODE_NAMES, "role_voice_select missing from voice names")

    expected_role_voice_chain = [
        "roleboard_prompt",
        "roleboard_generation",
        "role_voice_select",
        "role_voice_generation",
    ]
    actual_role_voice_chain = [
        node_name
        for node_name in PREGEN_NODE_NAMES
        if node_name.startswith("roleboard_") or node_name.startswith("role_voice_")
    ]
    require(actual_role_voice_chain == expected_role_voice_chain, "roleboard/voice node chain mismatch")
    require(
        PREGEN_NODE_NAMES.index("roleboard_prompt")
        < PREGEN_NODE_NAMES.index("roleboard_generation")
        < PREGEN_NODE_NAMES.index("role_voice_select")
        < PREGEN_NODE_NAMES.index("role_voice_generation"),
        "roleboard/voice pregen order mismatch",
    )
    require(
        {"roleboard_prompt", "roleboard_generation", "role_voice_select", "role_voice_generation"}.issubset(
            EPISODE_SCOPED_PREGEN_ONLY_NODES
        ),
        "episode-scoped pregen set missing roleboard/voice nodes",
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
    print("roleboard_pregen_contract_smoke=ok")


if __name__ == "__main__":
    main()
