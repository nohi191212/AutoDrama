from __future__ import annotations

from pathlib import Path
import sys
import yaml


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.model_catalog import ModelCatalog
from autodrama.core.schemas import ClipVideoInput, Role, StoryboardPromptClip, StoryboardShot
from autodrama.providers.base import AssetRef
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider
from autodrama.workflows.generation import GenerationWorkflow
from autodrama.workflows.nodes import PREGEN_NODE_NAMES
from autodrama.workflows.nodes.role_subject_nodes import RoleKlingVoiceGenerationNode
from autodrama.workflows.nodes.storyboard_asset_nodes import ClipManifestGenerationNode
from autodrama.utils.prompts import PromptStore


def main() -> None:
    config_payload = yaml.safe_load((ROOT / "config.yaml.example").read_text(encoding="utf-8"))
    catalog_payload = yaml.safe_load((ROOT / "model_catalog.yaml.example").read_text(encoding="utf-8"))
    assert config_payload["providers"]["kling"]["options"]["api_schema"] == "official_v3"
    assert ModelCatalog.from_mapping(catalog_payload).models["kling:kling-v3-omni"].capability == "video"

    provider = KlingOmniVideoProvider(
        ProviderSettings(
            models={"video": "kling-v3-omni"},
            options={
                "api_schema": "official_v3",
                "sound": "native",
                "resolution": "1080p",
                "aspect_ratio": "9:16",
                "multi_shot": False,
                "max_reference_images": 3,
            },
        ),
        RuntimeSettings(),
    )
    refs = [
        AssetRef(
            id="start",
            type="image",
            path="data:image/png;base64,AAAA",
            metadata={"asset_type": "clip_start_frame", "slot": "image_1"},
        ),
        AssetRef(
            id="end",
            type="image",
            path="data:image/png;base64,BBBB",
            metadata={"asset_type": "clip_end_frame", "slot": "image_2"},
        ),
        AssetRef(
            id="storyboard",
            type="image",
            path="data:image/png;base64,CCCC",
            metadata={"asset_type": "storyboard", "slot": "image_3"},
        ),
        AssetRef(
            id="element-jiang",
            type="element",
            metadata={"element_id": "123", "kling_content_id": "role_1"},
        ),
        AssetRef(
            id="element-jiu",
            type="element",
            metadata={"element_id": "456", "kling_content_id": "role_2"},
        ),
    ]
    payload = provider.build_payload(
        "<<<image_1>>> 到 <<<image_2>>>，@role_1 说话，@role_2 回答。",
        refs,
        duration=10,
        metadata={"audio": "native", "multi_shot": False},
    )
    assert set(payload) == {"contents", "settings", "options"}
    assert [item["type"] for item in payload["contents"]] == [
        "prompt",
        "first_frame",
        "last_frame",
        "refer_image",
        "element",
        "element",
    ]
    assert payload["contents"][4]["id"] == "role_1"
    assert payload["contents"][5]["element_id"] == "456"
    assert "@image_1" in payload["contents"][0]["text"]
    assert payload["settings"]["audio"] == "native"
    assert payload["settings"]["duration"] == 10
    assert payload["settings"]["multi_shot"] is False

    kling_clip_inputs = [
        ClipVideoInput(
            slot=f"image_{index}",
            asset_type=asset_type,
            asset_id=asset_type,
            asset_url=f"https://cdn.example.com/{asset_type}.png",
            source_node="smoke",
            label=asset_type,
            order=index,
        )
        for index, asset_type in enumerate(
            ["clip_start_frame", "clip_end_frame", "storyboard", "layout", "prop"],
            start=1,
        )
    ]
    manifest_node = object.__new__(ClipManifestGenerationNode)
    limited_inputs, limit_warnings = manifest_node._limit_clip_video_inputs_for_provider(
        inputs=kling_clip_inputs,
        provider=provider,
        clip_id="episode_001_clip_001",
    )
    assert [item.asset_type for item in limited_inputs] == [
        "clip_start_frame",
        "clip_end_frame",
        "storyboard",
        "layout",
        "prop",
    ]
    assert not limit_warnings

    generation = object.__new__(GenerationWorkflow)
    validated_inputs = generation._clip_video_inputs(
        ROOT,
        StoryboardShot(
            clip_id="episode_001_clip_001",
            index=1,
            title="Clip 001",
            duration_seconds=10,
            video_prompt="Camera Shot 1",
            clip_video_inputs=kling_clip_inputs,
        ),
        provider=provider,
    )
    assert validated_inputs["contract"] == "kling_start_end_storyboard_context_with_subject_elements_v1"
    assert [item["asset_type"] for item in validated_inputs["inputs"]] == [
        "clip_start_frame",
        "clip_end_frame",
        "storyboard",
        "layout",
        "prop",
    ]

    camera_prompt, camera_warnings = manifest_node._camera_shots_only_video_prompt(
        StoryboardPromptClip(
            clip_id="episode_001_clip_001",
            duration_seconds=10,
            layout_ids=["layout_hall"],
            camera_shots=[
                {
                    "camera_shot_id": "Camera Shot 1",
                    "time_range": "0-5秒",
                    "description": "固定机位，江未晞坐在地面；黄色细箭头标示主光方向。",
                },
                {
                    "camera_shot_id": "Camera Shot 2",
                    "time_range": "5-10秒",
                    "description": "固定机位，她仍坐着望向石台。",
                },
            ],
            panel_plan={f"P{i:02d}": f"P{i:02d}（Camera Shot 1）：面板。" for i in range(1, 13)},
            video_prompt="P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12",
        )
    )
    assert not camera_warnings
    assert "Camera Shot 1" in camera_prompt and "Camera Shot 2" in camera_prompt
    assert "P01" not in camera_prompt and "黄色细箭头" not in camera_prompt
    kling_prompt = PromptStore().render(
        "clip_video/kling",
        start_frame_input_slot="image_1",
        end_frame_input_slot="image_2",
        storyboard_input_slot="image_3",
        video_prompt=camera_prompt,
        duration_seconds=10,
        negative_rules="- 无字幕、无水印。",
    )
    assert "输入清单" not in kling_prompt and "P01" not in kling_prompt
    assert "image_1" in kling_prompt and "image_2" in kling_prompt and "image_3" in kling_prompt
    assert len(kling_prompt) < 2800

    frame_free_payload = provider.build_payload(
        "@role_1 与 @role_2 在故事板场景中对话。",
        [
            AssetRef(
                id="storyboard",
                type="image",
                path="data:image/png;base64,STORYBOARD",
                metadata={"asset_type": "storyboard", "slot": "image_1"},
            ),
            AssetRef(
                id="layout",
                type="image",
                path="data:image/png;base64,LAYOUT",
                metadata={"asset_type": "layout", "slot": "image_2"},
            ),
            AssetRef(id="element-1", type="element", metadata={"element_id": "123", "kling_content_id": "role_1"}),
            AssetRef(id="element-2", type="element", metadata={"element_id": "456", "kling_content_id": "role_2"}),
        ],
        duration=10,
        metadata={"audio": "native", "multi_shot": False},
    )
    frame_free_types = [item["type"] for item in frame_free_payload["contents"]]
    assert frame_free_types == ["prompt", "refer_image", "refer_image", "element", "element"]
    assert "first_frame" not in frame_free_types and "last_frame" not in frame_free_types

    subject_payload = provider.build_subject_element_payload(
        element_name="江未晞",
        element_description="女主角",
        reference_type="image_refer",
        image_refs=[
            AssetRef(type="image", path="data:image/png;base64,FRONTAL"),
            AssetRef(type="image", path="data:image/png;base64,ROLEBOARD"),
        ],
    )
    image_list = subject_payload["element_image_list"]
    assert image_list["frontal_image"] == "data:image/png;base64,FRONTAL"
    assert image_list["refer_images"] == [{"image_url": "data:image/png;base64,ROLEBOARD"}]
    try:
        provider.build_subject_element_payload(
            element_name="江未晞",
            element_description="女主角",
            reference_type="image_refer",
            image_refs=[AssetRef(type="image", path="data:image/png;base64,ONLY_ONE")],
        )
    except ValueError as exc:
        assert "one frontal image and 1-3 other reference images" in str(exc)
    else:
        raise AssertionError("single-image Kling subject payload should fail before API submission")

    voice_payload = provider.build_custom_voice_payload(
        voice_name="江未晞",
        voice_url="https://cdn.example.com/voice.wav",
        metadata={"external_task_id": "project_role_voice"},
    )
    assert voice_payload["voice_url"].startswith("https://")
    assert voice_payload["external_task_id"] == "project_role_voice"
    voice_result = provider._voice_result(
        {
            "data": {
                "task_id": "task-voice",
                "task_status": "succeed",
                "task_result": {
                    "voices": [
                        {
                            "voice_id": "voice-123",
                            "voice_name": "江未晞",
                            "trial_url": "https://cdn.example.com/trial.mp3",
                            "owned_by": "42",
                        }
                    ]
                },
            }
        }
    )
    assert voice_result.voice_id == "voice-123"
    assert voice_result.task_id == "task-voice"

    provider.settings.options["role_voice_map"] = {"role_jiang": "voice-123"}
    role = Role(id="role_jiang", name="江未晞", intro="女主角")
    assert RoleKlingVoiceGenerationNode._voice_spec(provider, role) == {
        "voice_id": "voice-123",
        "voice_name": "江未晞",
    }

    assert PREGEN_NODE_NAMES.index("role_subject_frontal_image_generation") < PREGEN_NODE_NAMES.index(
        "role_subject_element_generation"
    )
    assert PREGEN_NODE_NAMES.index("role_kling_voice_generation") < PREGEN_NODE_NAMES.index(
        "role_subject_element_generation"
    )
    assert PREGEN_NODE_NAMES.index("role_subject_element_generation") < PREGEN_NODE_NAMES.index("clip_manifest_generation")
    print("kling native-audio pipeline contract smoke: ok")


if __name__ == "__main__":
    main()
