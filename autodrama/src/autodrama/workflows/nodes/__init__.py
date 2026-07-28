"""Workflow node module boundaries."""

from __future__ import annotations

from typing import Any

from autodrama.workflows.nodes.bgm_nodes import BGM_NODE_NAMES, build_bgm_nodes
from autodrama.workflows.nodes.director_nodes import DIRECTOR_NODE_NAMES, build_director_nodes
from autodrama.workflows.nodes.image_audit_nodes import (
    IMAGE_AUDIT_NODE_NAMES,
    build_image_audit_nodes,
)
from autodrama.workflows.nodes.dynamic_asset_solidification_node import (
    DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
    build_dynamic_asset_solidification_episode_node,
)
from autodrama.workflows.nodes.role_nodes import ROLE_NODE_NAMES, build_role_nodes
from autodrama.workflows.nodes.role_subject_nodes import ROLE_SUBJECT_NODE_NAMES, build_role_subject_nodes
from autodrama.workflows.nodes.script_nodes import (
    MANUAL_SCRIPT_NODE_NAMES,
    SCRIPT_NODE_NAMES,
    build_script_node_runners,
    build_script_nodes,
)
from autodrama.workflows.nodes.shot_dialogue_audio_node import (
    SHOT_DIALOGUE_AUDIO_NODE_NAME,
    build_shot_dialogue_audio_episode_node,
)
from autodrama.workflows.nodes.shot_video_node import SHOT_VIDEO_NODE_NAME, build_shot_video_episode_node
from autodrama.workflows.nodes.video_audit_node import (
    SHOT_VIDEO_AUDIT_NODE_NAME,
    build_shot_video_audit_episode_node,
)
from autodrama.workflows.nodes.shot_asset_nodes import SHOT_ASSET_NODE_NAMES, build_shot_asset_nodes
from autodrama.workflows.nodes.static_asset_nodes import STATIC_ASSET_NODE_NAMES, build_static_asset_nodes
from autodrama.workflows.nodes.voice_nodes import VOICE_NODE_NAMES, build_voice_nodes
from autodrama.workflows.runner import EpisodeWorkflowNode, WorkflowNode

DEFAULT_PREGEN_ROLE_NODE_NAMES = [
    "role_extract_primary",
    "role_extract_functional",
    "role_finalize",
    "roleboard_prompt",
]
MANUAL_PREGEN_ROLE_NODE_NAMES = [
    node_name for node_name in ROLE_NODE_NAMES if node_name not in DEFAULT_PREGEN_ROLE_NODE_NAMES
]
DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES: list[str] = []
MANUAL_PREGEN_ROLE_SUBJECT_NODE_NAMES = [
    node_name for node_name in ROLE_SUBJECT_NODE_NAMES if node_name not in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES
]
DEFERRED_PREGEN_NODE_NAMES = [
    *MANUAL_SCRIPT_NODE_NAMES,
    *MANUAL_PREGEN_ROLE_NODE_NAMES,
    *MANUAL_PREGEN_ROLE_SUBJECT_NODE_NAMES,
    "role_subject_frontal_image_audit",
    *VOICE_NODE_NAMES,
    *BGM_NODE_NAMES,
]


def _image_audits_enabled(workflow: Any) -> bool:
    settings = getattr(workflow, "settings", None) or getattr(getattr(workflow, "repo", None), "settings", None)
    return bool(getattr(getattr(settings, "app", None), "enable_image_audit", True))


def build_pregen_nodes(workflow: Any) -> list[WorkflowNode]:
    default_role_names = set(DEFAULT_PREGEN_ROLE_NODE_NAMES)
    default_role_nodes = [
        node for node in build_role_nodes(workflow) if node.name in default_role_names
    ]
    image_audits_enabled = _image_audits_enabled(workflow)
    image_audit_by_name = {node.name: node for node in build_image_audit_nodes(workflow)}
    director_and_audit_nodes: list[WorkflowNode] = []
    for node in build_director_nodes(workflow):
        director_and_audit_nodes.append(node)
        if image_audits_enabled and node.name == "key_vision_image_generation":
            director_and_audit_nodes.append(image_audit_by_name["key_vision_image_audit"])
    script_runners = build_script_node_runners(workflow)
    clip_segment_node = WorkflowNode(
        name="clip_segment",
        run=script_runners["clip_segment"].run,
    )
    role_subject_by_name = {node.name: node for node in build_role_subject_nodes(workflow)}
    static_nodes = build_static_asset_nodes(workflow)
    static_by_name = {node.name: node for node in static_nodes}
    remaining_static_nodes = [node for node in static_nodes if node.name != "roleboard_image_generation"]
    static_and_audit_nodes: list[WorkflowNode] = []
    audits_after = {
        "prop_image_generation": "prop_image_audit",
        "layout_image_generation": "layout_image_audit",
    }
    for node in remaining_static_nodes:
        static_and_audit_nodes.append(node)
        audit_name = audits_after.get(node.name)
        if image_audits_enabled and audit_name:
            static_and_audit_nodes.append(image_audit_by_name[audit_name])
    shot_asset_and_audit_nodes: list[WorkflowNode] = []
    for node in build_shot_asset_nodes(workflow):
        shot_asset_and_audit_nodes.append(node)
        if image_audits_enabled and node.name == "shot_background_image_generation":
            shot_asset_and_audit_nodes.append(image_audit_by_name["shot_background_image_audit"])
        elif image_audits_enabled and node.name == "shot_keyframe_image_generation":
            shot_asset_and_audit_nodes.append(image_audit_by_name["shot_keyframe_image_audit"])
    return [
        *build_script_nodes(workflow),
        *director_and_audit_nodes,
        *default_role_nodes,
        static_by_name["roleboard_image_generation"],
        *([image_audit_by_name["roleboard_image_audit"]] if image_audits_enabled else []),
        role_subject_by_name["role_subject_frontal_image_generation"],
        *([image_audit_by_name["role_subject_frontal_image_audit"]] if image_audits_enabled else []),
        *static_and_audit_nodes,
        *[
            role_subject_by_name[name]
            for name in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES
            if name != "role_subject_frontal_image_generation"
        ],
        clip_segment_node,
        *shot_asset_and_audit_nodes,
    ]


def build_manual_pregen_nodes(workflow: Any) -> list[WorkflowNode]:
    script_runners = build_script_node_runners(workflow)
    manual_script_nodes = [
        WorkflowNode(name=node_name, run=script_runners[node_name].run)
        for node_name in MANUAL_SCRIPT_NODE_NAMES
    ]
    manual_role_names = set(MANUAL_PREGEN_ROLE_NODE_NAMES)
    manual_role_nodes = [
        node for node in build_role_nodes(workflow) if node.name in manual_role_names
    ]
    role_subject_by_name = {node.name: node for node in build_role_subject_nodes(workflow)}
    return [
        *manual_script_nodes,
        *manual_role_nodes,
        *[role_subject_by_name[name] for name in MANUAL_PREGEN_ROLE_SUBJECT_NODE_NAMES],
        *build_voice_nodes(workflow),
        *build_bgm_nodes(workflow),
    ]


PREGEN_NODE_NAMES = [
    *SCRIPT_NODE_NAMES,
    *DIRECTOR_NODE_NAMES,
    "key_vision_image_audit",
    *DEFAULT_PREGEN_ROLE_NODE_NAMES,
    "roleboard_image_generation",
    "roleboard_image_audit",
    *[
        node_name
        for static_name in STATIC_ASSET_NODE_NAMES
        if static_name != "roleboard_image_generation"
        for node_name in (
            static_name,
            *(
                ("prop_image_audit",)
                if static_name == "prop_image_generation"
                else ("layout_image_audit",)
                if static_name == "layout_image_generation"
                else ()
            ),
        )
    ],
    *[name for name in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES if name != "role_subject_frontal_image_generation"],
    "clip_segment",
    *[
        node_name
        for source_name in SHOT_ASSET_NODE_NAMES
        for node_name in (
            source_name,
            *(
                ("shot_background_image_audit",)
                if source_name == "shot_background_image_generation"
                else ("shot_keyframe_image_audit",)
                if source_name == "shot_keyframe_image_generation"
                else ()
            ),
        )
    ],
]
AVAILABLE_PREGEN_NODE_NAMES = [
    *PREGEN_NODE_NAMES,
    *DEFERRED_PREGEN_NODE_NAMES,
]

GENERATION_NODE_NAMES = [
    SHOT_DIALOGUE_AUDIO_NODE_NAME,
    SHOT_VIDEO_NODE_NAME,
    SHOT_VIDEO_AUDIT_NODE_NAME,
    DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
]


def build_generation_episode_nodes(workflow: Any) -> list[EpisodeWorkflowNode]:
    return [
        build_shot_dialogue_audio_episode_node(workflow),
        build_shot_video_episode_node(workflow),
        build_shot_video_audit_episode_node(workflow),
        build_dynamic_asset_solidification_episode_node(workflow),
    ]


__all__ = [
    "BGM_NODE_NAMES",
    "AVAILABLE_PREGEN_NODE_NAMES",
    "DEFERRED_PREGEN_NODE_NAMES",
    "DIRECTOR_NODE_NAMES",
    "IMAGE_AUDIT_NODE_NAMES",
    "DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME",
    "GENERATION_NODE_NAMES",
    "MANUAL_SCRIPT_NODE_NAMES",
    "PREGEN_NODE_NAMES",
    "ROLE_NODE_NAMES",
    "ROLE_SUBJECT_NODE_NAMES",
    "SCRIPT_NODE_NAMES",
    "SHOT_DIALOGUE_AUDIO_NODE_NAME",
    "SHOT_VIDEO_NODE_NAME",
    "SHOT_VIDEO_AUDIT_NODE_NAME",
    "SHOT_ASSET_NODE_NAMES",
    "STATIC_ASSET_NODE_NAMES",
    "VOICE_NODE_NAMES",
    "build_bgm_nodes",
    "build_director_nodes",
    "build_image_audit_nodes",
    "build_dynamic_asset_solidification_episode_node",
    "build_generation_episode_nodes",
    "build_manual_pregen_nodes",
    "build_pregen_nodes",
    "build_role_nodes",
    "build_role_subject_nodes",
    "build_script_node_runners",
    "build_script_nodes",
    "build_shot_dialogue_audio_episode_node",
    "build_shot_video_episode_node",
    "build_shot_video_audit_episode_node",
    "build_shot_asset_nodes",
    "build_static_asset_nodes",
    "build_voice_nodes",
]
