"""Workflow node module boundaries."""

from __future__ import annotations

from typing import Any

from autodrama.workflows.nodes.bgm_nodes import BGM_NODE_NAMES, build_bgm_nodes
from autodrama.workflows.nodes.director_nodes import DIRECTOR_NODE_NAMES, build_director_nodes
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
from autodrama.workflows.nodes.clip_video_node import CLIP_VIDEO_NODE_NAME, build_clip_video_episode_node
from autodrama.workflows.nodes.storyboard_asset_nodes import STORYBOARD_ASSET_NODE_NAMES, build_storyboard_asset_nodes
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
DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES = [
    "role_subject_frontal_image_generation",
    "role_kling_voice_generation",
    "role_subject_element_generation",
]
MANUAL_PREGEN_ROLE_SUBJECT_NODE_NAMES = [
    node_name for node_name in ROLE_SUBJECT_NODE_NAMES if node_name not in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES
]
DEFERRED_PREGEN_NODE_NAMES = [
    *MANUAL_SCRIPT_NODE_NAMES,
    *MANUAL_PREGEN_ROLE_NODE_NAMES,
    *MANUAL_PREGEN_ROLE_SUBJECT_NODE_NAMES,
    *VOICE_NODE_NAMES,
    *BGM_NODE_NAMES,
]


def build_pregen_nodes(workflow: Any) -> list[WorkflowNode]:
    default_role_names = set(DEFAULT_PREGEN_ROLE_NODE_NAMES)
    default_role_nodes = [
        node for node in build_role_nodes(workflow) if node.name in default_role_names
    ]
    script_runners = build_script_node_runners(workflow)
    clip_segment_node = WorkflowNode(
        name="clip_segment",
        run=script_runners["clip_segment"].run,
    )
    role_subject_by_name = {node.name: node for node in build_role_subject_nodes(workflow)}
    static_nodes = build_static_asset_nodes(workflow)
    static_by_name = {node.name: node for node in static_nodes}
    remaining_static_nodes = [node for node in static_nodes if node.name != "roleboard_image_generation"]
    return [
        *build_script_nodes(workflow),
        *build_director_nodes(workflow),
        *default_role_nodes,
        static_by_name["roleboard_image_generation"],
        role_subject_by_name["role_subject_frontal_image_generation"],
        *remaining_static_nodes,
        *[
            role_subject_by_name[name]
            for name in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES
            if name != "role_subject_frontal_image_generation"
        ],
        clip_segment_node,
        *build_storyboard_asset_nodes(workflow),
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
    *DEFAULT_PREGEN_ROLE_NODE_NAMES,
    "roleboard_image_generation",
    "role_subject_frontal_image_generation",
    *[name for name in STATIC_ASSET_NODE_NAMES if name != "roleboard_image_generation"],
    *[name for name in DEFAULT_PREGEN_ROLE_SUBJECT_NODE_NAMES if name != "role_subject_frontal_image_generation"],
    "clip_segment",
    *STORYBOARD_ASSET_NODE_NAMES,
]
AVAILABLE_PREGEN_NODE_NAMES = [
    *PREGEN_NODE_NAMES,
    *DEFERRED_PREGEN_NODE_NAMES,
]

GENERATION_NODE_NAMES = [
    SHOT_DIALOGUE_AUDIO_NODE_NAME,
    CLIP_VIDEO_NODE_NAME,
    DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
]


def build_generation_episode_nodes(workflow: Any) -> list[EpisodeWorkflowNode]:
    return [
        build_shot_dialogue_audio_episode_node(workflow),
        build_clip_video_episode_node(workflow),
        build_dynamic_asset_solidification_episode_node(workflow),
    ]


__all__ = [
    "BGM_NODE_NAMES",
    "AVAILABLE_PREGEN_NODE_NAMES",
    "DEFERRED_PREGEN_NODE_NAMES",
    "DIRECTOR_NODE_NAMES",
    "DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME",
    "GENERATION_NODE_NAMES",
    "MANUAL_SCRIPT_NODE_NAMES",
    "PREGEN_NODE_NAMES",
    "ROLE_NODE_NAMES",
    "ROLE_SUBJECT_NODE_NAMES",
    "SCRIPT_NODE_NAMES",
    "SHOT_DIALOGUE_AUDIO_NODE_NAME",
    "CLIP_VIDEO_NODE_NAME",
    "STORYBOARD_ASSET_NODE_NAMES",
    "STATIC_ASSET_NODE_NAMES",
    "VOICE_NODE_NAMES",
    "build_bgm_nodes",
    "build_director_nodes",
    "build_dynamic_asset_solidification_episode_node",
    "build_generation_episode_nodes",
    "build_manual_pregen_nodes",
    "build_pregen_nodes",
    "build_role_nodes",
    "build_role_subject_nodes",
    "build_script_node_runners",
    "build_script_nodes",
    "build_shot_dialogue_audio_episode_node",
    "build_clip_video_episode_node",
    "build_storyboard_asset_nodes",
    "build_static_asset_nodes",
    "build_voice_nodes",
]
