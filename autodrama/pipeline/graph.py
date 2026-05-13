"""LangGraph pipeline builder for AutoDrama — full 22-node workflow.

Topology (from 短剧生成方案.md)::

    input_document            ← reads .docx
         │
    script_outline            ← 【剧本-大纲】
         │
    script_detail             ← 【剧本-详细剧本生成】
         │
    script_polish             ← 【剧本-打磨】rebuttle 5X
         │
    character_profile         ← 【角色-基本设定】rebuttle 3X
         │
    character_voice           ← 【角色-声音】
         │
    character_appearance      ← 【角色-基本外形图】
         │
    props_setting             ← 【道具-基本设定】
         │
    script_compress           ← 【剧本-压缩】
         │
    scene_description         ← 【场景-场景描述生成】
         │
    scene_confirm             ← 【场景确认】
         │
    scene_image               ← 【场景-场景图生成】
         │
    bgm_assets                ← 【音乐资产】
         │
    storyboard                ← 【分镜生成】
         │
    reference_frame           ← 【参考帧生成】
         │
    video_generation          ← 【视频生成】
         │
    asset_solidify            ← 【资产固化】→ loop back or continue
         │
    editing_plan              ← 【剪辑方案】
         │
    av_separation             ← 【音画分离-可选】
         │
    av_editing                ← 【基础音画剪辑】
         │
    bgm_scoring               ← 【配乐方案】
         │
    super_resolution          ← 【画面超分】
         │
    final_output              → END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from autodrama.pipeline.nodes import (
    InputNode,
    ScriptOutlineNode,
    ScriptDetailNode,
    ScriptPolishNode,
    CharacterProfileNode,
    CharacterVoiceNode,
    CharacterAppearanceNode,
    PropsNode,
    ScriptCompressNode,
    SceneDescriptionNode,
    SceneConfirmNode,
    SceneImageNode,
    BgmNode,
    StoryboardNode,
    ReferenceFrameNode,
    VideoGenerationNode,
    AssetSolidifyNode,
    EditingPlanNode,
    AVSeparationNode,
    AVEditingNode,
    BgmScoringNode,
    SuperResolutionNode,
    OutputNode,
)
from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


def build_pipeline() -> StateGraph:
    """Build and compile the full 22-node AutoDrama LangGraph pipeline."""

    # Instantiate all nodes
    input_node = InputNode()
    script_outline_node = ScriptOutlineNode()
    script_detail_node = ScriptDetailNode()
    script_polish_node = ScriptPolishNode()
    character_profile_node = CharacterProfileNode()
    character_voice_node = CharacterVoiceNode()
    character_appearance_node = CharacterAppearanceNode()
    props_node = PropsNode()
    script_compress_node = ScriptCompressNode()
    scene_description_node = SceneDescriptionNode()
    scene_confirm_node = SceneConfirmNode()
    scene_image_node = SceneImageNode()
    bgm_node = BgmNode()
    storyboard_node = StoryboardNode()
    reference_frame_node = ReferenceFrameNode()
    video_generation_node = VideoGenerationNode()
    asset_solidify_node = AssetSolidifyNode()
    editing_plan_node = EditingPlanNode()
    av_separation_node = AVSeparationNode()
    av_editing_node = AVEditingNode()
    bgm_scoring_node = BgmScoringNode()
    super_resolution_node = SuperResolutionNode()
    output_node = OutputNode()

    builder = StateGraph(DramaState)

    # =====================================================================
    # Register all nodes
    # =====================================================================
    builder.add_node("input_document", input_node.execute)
    builder.add_node("script_outline", script_outline_node.execute)
    builder.add_node("script_detail", script_detail_node.execute)
    builder.add_node("script_polish", script_polish_node.execute)
    builder.add_node("character_profile", character_profile_node.execute)
    builder.add_node("character_voice", character_voice_node.execute)
    builder.add_node("character_appearance", character_appearance_node.execute)
    builder.add_node("props_setting", props_node.execute)
    builder.add_node("script_compress", script_compress_node.execute)
    builder.add_node("scene_description", scene_description_node.execute)
    builder.add_node("scene_confirm", scene_confirm_node.execute)
    builder.add_node("scene_image", scene_image_node.execute)
    builder.add_node("bgm_assets", bgm_node.execute)
    builder.add_node("storyboard", storyboard_node.execute)
    builder.add_node("reference_frame", reference_frame_node.execute)
    builder.add_node("video_generation", video_generation_node.execute)
    builder.add_node("asset_solidify", asset_solidify_node.execute)
    builder.add_node("editing_plan", editing_plan_node.execute)
    builder.add_node("av_separation", av_separation_node.execute)
    builder.add_node("av_editing", av_editing_node.execute)
    builder.add_node("bgm_scoring", bgm_scoring_node.execute)
    builder.add_node("super_resolution", super_resolution_node.execute)
    builder.add_node("final_output", output_node.execute)
    builder.add_node("error_handler", _error_handler)

    # =====================================================================
    # Entry point
    # =====================================================================
    builder.set_entry_point("input_document")

    # =====================================================================
    # Linear chain with conditional retry on each node
    # =====================================================================
    _add_retry_edge(builder, "input_document", "script_outline")
    _add_retry_edge(builder, "script_outline", "script_detail")
    _add_retry_edge(builder, "script_detail", "script_polish")
    _add_retry_edge(builder, "script_polish", "character_profile")
    _add_retry_edge(builder, "character_profile", "character_voice")
    _add_retry_edge(builder, "character_voice", "character_appearance")
    _add_retry_edge(builder, "character_appearance", "props_setting")
    _add_retry_edge(builder, "props_setting", "script_compress")
    _add_retry_edge(builder, "script_compress", "scene_description")
    _add_retry_edge(builder, "scene_description", "scene_confirm")
    _add_retry_edge(builder, "scene_confirm", "scene_image")
    _add_retry_edge(builder, "scene_image", "bgm_assets")
    _add_retry_edge(builder, "bgm_assets", "storyboard")
    _add_retry_edge(builder, "storyboard", "reference_frame")
    _add_retry_edge(builder, "reference_frame", "video_generation")

    # Asset solidify: loop back to storyboard if more episodes, else continue
    builder.add_conditional_edges(
        "asset_solidify",
        _asset_solidify_router,
        {
            "storyboard": "storyboard",
            "editing_plan": "editing_plan",
            "abort": "error_handler",
        },
    )

    _add_retry_edge(builder, "video_generation", "asset_solidify")
    _add_retry_edge(builder, "editing_plan", "av_separation")

    # AV separation is conditional (optional)
    builder.add_conditional_edges(
        "av_separation",
        _av_separation_router,
        {
            "av_editing": "av_editing",
            "skip": "av_editing",
        },
    )

    _add_retry_edge(builder, "av_editing", "bgm_scoring")
    _add_retry_edge(builder, "bgm_scoring", "super_resolution")
    _add_retry_edge(builder, "super_resolution", "final_output")

    builder.add_edge("final_output", END)
    builder.add_edge("error_handler", END)

    return builder.compile()


# =========================================================================
# Routing helpers
# =========================================================================


def _add_retry_edge(builder: StateGraph, from_node: str, to_node: str) -> None:
    """Add a conditional edge: continue → to_node, retry → from_node, abort → error_handler."""
    builder.add_conditional_edges(
        from_node,
        _retry_or_abort,
        {
            "continue": to_node,
            "retry": from_node,
            "abort": "error_handler",
        },
    )


def _retry_or_abort(state: DramaState) -> str:
    """Route to 'continue', 'retry', or 'abort' based on recent errors."""
    errors: list[dict] = state.get("errors", [])
    if not errors:
        return "continue"

    last_error = errors[-1]
    retry_count = last_error.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    # retry_count >= 99 means non-recoverable — abort immediately
    if retry_count >= 99:
        logger.error(f"Non-recoverable error at '{last_error.get('stage', '?')}'")
        return "abort"

    if retry_count < max_retries:
        logger.warning(
            f"Retrying '{last_error.get('stage', '?')}' "
            f"(attempt {retry_count + 1}/{max_retries})"
        )
        return "retry"

    logger.error(
        f"Aborting after {retry_count} retries for stage "
        f"'{last_error.get('stage', '?')}'"
    )
    return "abort"


def _asset_solidify_router(state: DramaState) -> str:
    """After solidifying assets, loop back to storyboard if episodes remain."""
    errors = state.get("errors", [])
    if errors:
        last_error = errors[-1]
        if last_error.get("retry_count", 0) >= state.get("max_retries", 3):
            return "abort"

    remaining = state.get("remaining_episodes", [])
    if remaining:
        logger.info(f"Looping back to storyboard — {len(remaining)} episodes left")
        return "storyboard"
    return "editing_plan"


def _av_separation_router(state: DramaState) -> str:
    """Skip AV separation if editing plan doesn't require it."""
    plan = state.get("editing_plan") or {}
    if plan.get("requires_separation"):
        return "av_editing"  # process through separation
    return "skip"  # skip directly to editing


def _error_handler(state: DramaState) -> dict:
    """Terminal error handler — log all accumulated errors."""
    logger.error(f"Pipeline failed. Errors: {state.get('errors', [])}")
    return {"final_video_path": None}
