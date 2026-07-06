from __future__ import annotations

import sys
from inspect import signature
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.workflows.pregen import PREGEN_NODES, PREGEN_ONLY_NODES, PregenWorkflow
from autodrama.workflows.runner import node_is_completed


REMOVED_FROM_DEFAULT = [
    "role_extract",
    "role_episode_key_audit",
    "role_duplicate_audit",
    "ambient_entity_extract",
    "role_subject_video_generation",
    "role_subject_element_generation",
    "role_voice_select",
]

INSERTED_AFTER_ROLEBOARD = [
    "prop_extract",
    "prop_dedupe",
    "layout_extract",
    "layout_dedupe_review",
    "clip_segment",
    "prop_prompt",
    "prop_image_generation",
    "layout_prompt",
    "layout_image_generation",
]

STORYBOARD_CHAIN = [
    "clip_prompt",
    "storyboard_prompt",
    "storyboard_generation",
    "storyboard_keyframe_generation",
    "clip_manifest_generation",
]

SCRIPT_DEFAULT_PREFIX = [
    "script_import",
    "script_detail_expand",
]

SCRIPT_KEY_VISION_CHAIN = [
    "script_novel_extract",
    "design_key_vision_prompt",
    "design_key_vision_image",
]


def main() -> None:
    if PREGEN_NODES[: len(SCRIPT_DEFAULT_PREFIX)] != SCRIPT_DEFAULT_PREFIX:
        raise AssertionError(
            f"default script chain should start with {SCRIPT_DEFAULT_PREFIX!r}, "
            f"got {PREGEN_NODES[: len(SCRIPT_DEFAULT_PREFIX)]!r}"
        )
    for node_name in ("script_outline", "script_novel"):
        if node_name in PREGEN_NODES:
            raise AssertionError(f"{node_name} should be optional, not in the default pregen chain")
        if node_name not in PREGEN_ONLY_NODES:
            raise AssertionError(f"{node_name} should remain available through pregen --only")
    if not node_is_completed("script_import", ["script_outline"]):
        raise AssertionError("script_outline completion should satisfy script_import")
    if not node_is_completed("script_detail_expand", ["script_novel"]):
        raise AssertionError("script_novel completion should satisfy script_detail_expand")

    for node_name in REMOVED_FROM_DEFAULT:
        if node_name in PREGEN_NODES:
            raise AssertionError(f"{node_name} should not be in the default pregen chain")
        if node_name not in PREGEN_ONLY_NODES:
            raise AssertionError(f"{node_name} should remain available through pregen --only")

    expected_role_chain = ["role_extract_primary", "role_extract_functional", "role_finalize", "roleboard_prompt"]
    role_index = PREGEN_NODES.index("role_extract_primary")
    actual_role_chain = PREGEN_NODES[role_index : role_index + len(expected_role_chain)]
    if actual_role_chain != expected_role_chain:
        raise AssertionError(f"default role chain should be {expected_role_chain!r}, got {actual_role_chain!r}")
    roleboard_index = PREGEN_NODES.index("roleboard_generation")
    expected_slice = INSERTED_AFTER_ROLEBOARD
    actual_slice = PREGEN_NODES[roleboard_index + 1 : roleboard_index + 1 + len(expected_slice)]
    if actual_slice != expected_slice:
        raise AssertionError(
            "prop/layout nodes must run immediately after roleboard_generation; "
            f"got {actual_slice!r}"
        )

    if PREGEN_NODES[-1] != "clip_manifest_generation":
        raise AssertionError(f"default pregen should end at clip_manifest_generation, got {PREGEN_NODES[-1]!r}")
    script_index = PREGEN_NODES.index("script_novel_extract")
    actual_script_key_vision_chain = PREGEN_NODES[
        script_index : script_index + len(SCRIPT_KEY_VISION_CHAIN)
    ]
    if actual_script_key_vision_chain != SCRIPT_KEY_VISION_CHAIN:
        raise AssertionError(
            f"script/key-vision chain should be {SCRIPT_KEY_VISION_CHAIN!r}, "
            f"got {actual_script_key_vision_chain!r}"
        )

    layout_dedupe_index = PREGEN_NODES.index("layout_dedupe_review")
    if PREGEN_NODES[layout_dedupe_index + 1] != "clip_segment":
        raise AssertionError("clip_segment should run immediately after prop/layout extraction and dedupe")
    if PREGEN_NODES[layout_dedupe_index + 2] != "prop_prompt":
        raise AssertionError("prop_prompt should run after clip_segment")
    layout_image_index = PREGEN_NODES.index("layout_image_generation")
    if PREGEN_NODES[layout_image_index + 1] != "clip_prompt":
        raise AssertionError("clip_prompt should run after static asset prompts/images")
    storyboard_index = PREGEN_NODES.index("clip_prompt")
    actual_storyboard_chain = PREGEN_NODES[storyboard_index : storyboard_index + len(STORYBOARD_CHAIN)]
    if actual_storyboard_chain != STORYBOARD_CHAIN:
        raise AssertionError(f"storyboard chain should be {STORYBOARD_CHAIN!r}, got {actual_storyboard_chain!r}")
    until_default = signature(PregenWorkflow.run).parameters["until"].default
    if until_default != "clip_manifest_generation":
        raise AssertionError(f"PregenWorkflow.run default until should be clip_manifest_generation, got {until_default!r}")

    for node_name in [*INSERTED_AFTER_ROLEBOARD, *STORYBOARD_CHAIN]:
        if node_name not in PREGEN_ONLY_NODES:
            raise AssertionError(f"{node_name} should remain available through pregen --only")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "pregen_default_chain_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("pregen_default_chain_contract_smoke: ok")


if __name__ == "__main__":
    main()
