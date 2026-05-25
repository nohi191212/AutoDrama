"""Workflow node module boundaries."""

from __future__ import annotations

from typing import Any

from autodrama.workflows.nodes.bgm_nodes import BGM_NODE_NAMES, build_bgm_nodes
from autodrama.workflows.nodes.dynamic_asset_solidification_node import (
    DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
    build_dynamic_asset_solidification_episode_node,
)
from autodrama.workflows.nodes.ref_frame_node import REF_FRAME_NODE_NAME, build_ref_frame_episode_node
from autodrama.workflows.nodes.role_nodes import ROLE_NODE_NAMES, build_role_nodes
from autodrama.workflows.nodes.script_nodes import SCRIPT_NODE_NAMES, build_script_nodes
from autodrama.workflows.nodes.shot_video_node import SHOT_VIDEO_NODE_NAME, build_shot_video_episode_node
from autodrama.workflows.nodes.storyboard_node import STORYBOARD_NODE_NAME, build_storyboard_episode_node
from autodrama.workflows.nodes.static_asset_nodes import STATIC_ASSET_NODE_NAMES, build_static_asset_nodes
from autodrama.workflows.nodes.voice_nodes import VOICE_NODE_NAMES, build_voice_nodes
from autodrama.workflows.runner import EpisodeWorkflowNode, WorkflowNode


def build_pregen_nodes(workflow: Any) -> list[WorkflowNode]:
    return [
        *build_script_nodes(workflow),
        *build_role_nodes(workflow),
        *build_voice_nodes(workflow),
        *build_static_asset_nodes(workflow),
        *build_bgm_nodes(workflow),
    ]


PREGEN_NODE_NAMES = [
    *SCRIPT_NODE_NAMES,
    *ROLE_NODE_NAMES,
    *VOICE_NODE_NAMES,
    *STATIC_ASSET_NODE_NAMES,
    *BGM_NODE_NAMES,
]

GENERATION_NODE_NAMES = [
    STORYBOARD_NODE_NAME,
    REF_FRAME_NODE_NAME,
    SHOT_VIDEO_NODE_NAME,
    DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME,
]


def build_generation_episode_nodes(workflow: Any) -> list[EpisodeWorkflowNode]:
    return [
        build_storyboard_episode_node(workflow),
        build_ref_frame_episode_node(workflow),
        build_shot_video_episode_node(workflow),
        build_dynamic_asset_solidification_episode_node(workflow),
    ]


__all__ = [
    "BGM_NODE_NAMES",
    "DYNAMIC_ASSET_SOLIDIFICATION_NODE_NAME",
    "GENERATION_NODE_NAMES",
    "PREGEN_NODE_NAMES",
    "REF_FRAME_NODE_NAME",
    "ROLE_NODE_NAMES",
    "SCRIPT_NODE_NAMES",
    "SHOT_VIDEO_NODE_NAME",
    "STORYBOARD_NODE_NAME",
    "STATIC_ASSET_NODE_NAMES",
    "VOICE_NODE_NAMES",
    "build_bgm_nodes",
    "build_dynamic_asset_solidification_episode_node",
    "build_generation_episode_nodes",
    "build_pregen_nodes",
    "build_ref_frame_episode_node",
    "build_role_nodes",
    "build_script_nodes",
    "build_shot_video_episode_node",
    "build_storyboard_episode_node",
    "build_static_asset_nodes",
    "build_voice_nodes",
]
