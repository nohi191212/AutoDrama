# === Pre-generation asset nodes ===
from autodrama.pipeline.nodes.input_node import InputNode
from autodrama.pipeline.nodes.script_outline_node import ScriptOutlineNode
from autodrama.pipeline.nodes.script_detail_node import ScriptDetailNode
from autodrama.pipeline.nodes.script_polish_node import ScriptPolishNode
from autodrama.pipeline.nodes.character_profile_node import CharacterProfileNode
from autodrama.pipeline.nodes.character_voice_node import CharacterVoiceNode
from autodrama.pipeline.nodes.character_appearance_node import CharacterAppearanceNode
from autodrama.pipeline.nodes.props_node import PropsNode
from autodrama.pipeline.nodes.script_compress_node import ScriptCompressNode
from autodrama.pipeline.nodes.scene_description_node import SceneDescriptionNode
from autodrama.pipeline.nodes.scene_confirm_node import SceneConfirmNode
from autodrama.pipeline.nodes.scene_image_node import SceneImageNode
from autodrama.pipeline.nodes.bgm_node import BgmNode

# === Dynamic generation nodes ===
from autodrama.pipeline.nodes.storyboard_node import StoryboardNode
from autodrama.pipeline.nodes.reference_frame_node import ReferenceFrameNode
from autodrama.pipeline.nodes.video_generation_node import VideoGenerationNode
from autodrama.pipeline.nodes.asset_solidify_node import AssetSolidifyNode

# === Editing / post-production nodes ===
from autodrama.pipeline.nodes.editing_plan_node import EditingPlanNode
from autodrama.pipeline.nodes.av_separation_node import AVSeparationNode
from autodrama.pipeline.nodes.av_editing_node import AVEditingNode
from autodrama.pipeline.nodes.bgm_scoring_node import BgmScoringNode
from autodrama.pipeline.nodes.super_resolution_node import SuperResolutionNode

# === Legacy nodes (kept for backward compatibility) ===
from autodrama.pipeline.nodes.script_node import ScriptNode
from autodrama.pipeline.nodes.scene_node import SceneNode
from autodrama.pipeline.nodes.image_node import ImageNode
from autodrama.pipeline.nodes.audio_node import AudioNode
from autodrama.pipeline.nodes.compose_node import ComposeNode
from autodrama.pipeline.nodes.output_node import OutputNode

__all__ = [
    # Pre-generation
    "InputNode",
    "ScriptOutlineNode",
    "ScriptDetailNode",
    "ScriptPolishNode",
    "CharacterProfileNode",
    "CharacterVoiceNode",
    "CharacterAppearanceNode",
    "PropsNode",
    "ScriptCompressNode",
    "SceneDescriptionNode",
    "SceneConfirmNode",
    "SceneImageNode",
    "BgmNode",
    # Dynamic generation
    "StoryboardNode",
    "ReferenceFrameNode",
    "VideoGenerationNode",
    "AssetSolidifyNode",
    # Editing
    "EditingPlanNode",
    "AVSeparationNode",
    "AVEditingNode",
    "BgmScoringNode",
    "SuperResolutionNode",
    # Legacy
    "ScriptNode",
    "SceneNode",
    "ImageNode",
    "AudioNode",
    "ComposeNode",
    "OutputNode",
]
