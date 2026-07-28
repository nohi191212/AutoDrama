"""Check the default pregen chain uses the reusable-background shot contract."""

from __future__ import annotations

import sys
from inspect import signature
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.workflows.pregen import PREGEN_NODES, PREGEN_ONLY_NODES, PregenWorkflow


SHOT_CHAIN = [
    "clip_segment",
    "clip_to_shots",
    "layout_to_background_prompt",
    "shot_background_image_generation",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_manifest_generation",
]
LEGACY_DEFAULT_NODES = {
    "clip_prompt",
    "clip_storyboard_prompt",
    "clip_storyboard_prompt_audit",
    "clip_storyboard_image_generation",
    "clip_storyboard_keyframe_generation",
    "clip_manifest_generation",
}


def main() -> None:
    leaked = sorted(LEGACY_DEFAULT_NODES.intersection(PREGEN_NODES))
    if leaked:
        raise AssertionError(f"legacy storyboard node(s) leaked into PREGEN_NODES: {leaked}")
    leaked = sorted(LEGACY_DEFAULT_NODES.intersection(PREGEN_ONLY_NODES))
    if leaked:
        raise AssertionError(f"legacy storyboard node(s) remain selectable: {leaked}")
    start = PREGEN_NODES.index("clip_segment")
    actual_shot_chain = [
        node_name for node_name in PREGEN_NODES[start:]
        if node_name in SHOT_CHAIN
    ]
    if actual_shot_chain != SHOT_CHAIN:
        raise AssertionError("default reusable-background shot chain has an invalid order")
    if any(node_name in PREGEN_NODES for node_name in (
        "role_subject_frontal_image_generation",
        "role_kling_voice_generation",
        "role_subject_element_generation",
    )):
        raise AssertionError("subject-element preparation must not run in the default pregen chain")
    if PREGEN_NODES[-1] != "shot_manifest_generation":
        raise AssertionError("shot_manifest_generation must finish default pregen")
    if signature(PregenWorkflow.run).parameters["until"].default != "shot_manifest_generation":
        raise AssertionError("PregenWorkflow default stop node is not shot_manifest_generation")
    for node_name in SHOT_CHAIN:
        if node_name not in PREGEN_ONLY_NODES:
            raise AssertionError(f"{node_name} must support targeted --only execution")
    settings = load_settings(ROOT / "config.yaml.example")
    for node_name in (
        "clip_to_shots",
        "layout_to_background_prompt",
        "shot_background_image_generation",
        "shot_keyframe_prompt",
        "shot_keyframe_image_generation",
    ):
        if node_name not in settings.nodes:
            raise AssertionError(f"config.yaml.example is missing nodes.{node_name}")
    prompt_root = ROOT / "autodrama" / "src" / "autodrama" / "prompts"
    for node_name in LEGACY_DEFAULT_NODES:
        if list((prompt_root / node_name).glob("*.md")):
            raise AssertionError(f"legacy prompt template remains: {node_name}")
    print("pregen_default_chain_contract_smoke: ok")


if __name__ == "__main__":
    main()
