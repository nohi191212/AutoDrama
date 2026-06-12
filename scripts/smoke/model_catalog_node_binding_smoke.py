from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def write_config(path: Path, *, bad_param: bool = False) -> None:
    config = {
        "model_catalog_file": str(ROOT_DIR / "model_catalog.yaml.example"),
        "providers": {
            "rightcode": {
                "base_url": "https://www.right.codes/codex",
                "api_key_env": "RIGHTCODE_API_KEY",
                "models": {"text": "gpt-5.5", "storyboard": "gpt-5.5"},
                "options": {"text_response_format": True, "reasoning_effort": "xhigh"},
            },
            "toapi": {
                "base_url": "https://toapis.com",
                "api_key_env": "TOAPI_API_KEY",
                "models": {"image": "gpt-image-2"},
                "options": {"size": "16:9", "resolution": "4K", "n": 1},
            },
            "volcengine": {
                "base_url": "https://openspeech.bytedance.com",
                "api_key_env": "VOLCENGINE_API_KEY",
                "models": {
                    "video": "doubao-seedance-2-0-260128",
                    "seedance_2": "doubao-seedance-2-0-260128",
                },
                "options": {
                    "seedance_base_url": "https://ark.cn-beijing.volces.com",
                    "video_min_duration_seconds": 4,
                    "video_max_duration_seconds": 10,
                },
            },
        },
        "routing": {
            "text": {"director": "rightcode", "storyboard": "rightcode"},
            "image": {"key_vision": "toapi", "role": "toapi", "storyboard": "toapi"},
            "video": {"shot": "volcengine"},
        },
        "nodes": {
            "design_key_vision_prompt": {
                "model": "rightcode:gpt-5.5",
                "params": {
                    "reasoning_effort": "xhigh",
                    "temperature": 0.2,
                    "response_format": "json_object",
                },
            },
            "design_key_vision_image": {
                "model": "toapi:gpt-image-2",
                "params": {
                    "size": "9:16",
                    "resolution": "4K",
                    "n": 1,
                },
            },
            "storyboard_prompt": {
                "model": "rightcode:gpt-5.5",
                "params": {
                    "reasoning_effort": "xhigh",
                    "temperature": 0.2,
                    "response_format": "json_object",
                },
            },
            "storyboard_sheet_generation": {
                "model": "toapi:gpt-image-2",
                "params": {
                    "resolution": "2K",
                    "n": 1,
                },
            },
            "storyboard_bbox_detection": {
                "model": "rightcode:gpt-5.5",
                "params": {
                    "reasoning_effort": "high",
                    "temperature": 0.1,
                    "response_format": "json_object",
                },
            },
            "roleboard_generation": {
                "model": "toapi:gpt-image-2",
                "params": {
                    "size": "16:9",
                    "resolution": "4K",
                    "n": 1,
                    **({"bad_param": True} if bad_param else {}),
                },
            },
            "shot_video_generation": {
                "model": "volcengine:doubao-seedance-2-0-260128",
                "params": {
                    "ratio": "9:16",
                    "resolution": "720p",
                    "generate_audio": False,
                    "max_reference_images": 4,
                },
            },
        },
    }
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


async def assert_runtime_ref_limit(router: ProviderRouter) -> None:
    provider = router.video("shot", node_name="shot_video_generation")
    refs = [
        AssetRef(id=f"ref_{index}", type="image", url=f"https://example.invalid/{index}.png")
        for index in range(5)
    ]
    try:
        await provider.submit_video(
            "test prompt",
            refs=refs,
            duration=4,
            metadata={"asset_id": "shot_001"},
        )
    except ValueError as exc:
        if "max_reference_images" not in str(exc):
            raise AssertionError(f"Unexpected ref-limit error: {exc}") from exc
        return
    raise AssertionError("Expected video ref limit validation to fail before provider submission")


def main() -> int:
    smoke_dir = ROOT_DIR / ".tmp" / "smoke" / "model_catalog_node_binding"
    smoke_dir.mkdir(parents=True, exist_ok=True)

    config_path = smoke_dir / "config.yaml"
    write_config(config_path)
    settings = load_settings(config_path)
    router = ProviderRouter(settings)

    key_vision_text_provider = router.text("director", node_name="design_key_vision_prompt")
    if getattr(key_vision_text_provider, "model", None) != "gpt-5.5":
        raise AssertionError(f"Unexpected key vision text model: {getattr(key_vision_text_provider, 'model', None)}")
    if getattr(key_vision_text_provider, "reasoning_effort", None) != "xhigh":
        raise AssertionError("Key vision prompt params did not update RightCode reasoning_effort")
    if getattr(key_vision_text_provider, "use_response_format", None) is not True:
        raise AssertionError("Key vision prompt params did not enable RightCode JSON response format")

    text_provider = router.text("storyboard", node_name="storyboard_prompt")
    if getattr(text_provider, "model", None) != "gpt-5.5":
        raise AssertionError(f"Unexpected text model: {getattr(text_provider, 'model', None)}")
    if getattr(text_provider, "reasoning_effort", None) != "xhigh":
        raise AssertionError("Node params did not update RightCode reasoning_effort")
    if getattr(text_provider, "use_response_format", None) is not True:
        raise AssertionError("Node params did not enable RightCode JSON response format")

    bbox_provider = router.text("storyboard", node_name="storyboard_bbox_detection")
    if getattr(bbox_provider, "model", None) != "gpt-5.5":
        raise AssertionError(f"Unexpected bbox detection model: {getattr(bbox_provider, 'model', None)}")
    if getattr(bbox_provider, "reasoning_effort", None) != "high":
        raise AssertionError("BBox detection params did not update RightCode reasoning_effort")
    if getattr(bbox_provider, "use_response_format", None) is not True:
        raise AssertionError("BBox detection params did not enable RightCode JSON response format")

    storyboard_sheet_provider = router.image("storyboard", node_name="storyboard_sheet_generation")
    storyboard_sheet_payload = storyboard_sheet_provider._provider.build_payload(
        "test 12-panel storyboard",
        metadata=storyboard_sheet_provider._metadata({"asset_id": "storyboard_001"}),
    )
    if storyboard_sheet_payload["model"] != "gpt-image-2":
        raise AssertionError(f"Unexpected storyboard sheet image model: {storyboard_sheet_payload['model']}")
    if storyboard_sheet_payload["resolution"] != "2K":
        raise AssertionError(f"Unexpected storyboard sheet resolution: {storyboard_sheet_payload['resolution']}")

    image_provider = router.image("role", node_name="roleboard_generation")
    payload = image_provider._provider.build_payload(
        "test image",
        metadata=image_provider._metadata({"asset_id": "role_001"}),
    )
    if payload["model"] != "gpt-image-2":
        raise AssertionError(f"Unexpected image model: {payload['model']}")
    if payload["size"] != "16:9":
        raise AssertionError(f"Unexpected image size: {payload['size']}")
    if payload["resolution"] != "4K":
        raise AssertionError(f"Unexpected image resolution: {payload['resolution']}")

    key_vision_image_provider = router.image("key_vision", node_name="design_key_vision_image")
    key_vision_payload = key_vision_image_provider._provider.build_payload(
        "test key vision",
        metadata=key_vision_image_provider._metadata({"asset_id": "key_vision_original"}),
    )
    if key_vision_payload["size"] != "9:16":
        raise AssertionError(f"Unexpected key vision image size: {key_vision_payload['size']}")

    asyncio.run(assert_runtime_ref_limit(router))

    invalid_config_path = smoke_dir / "invalid_config.yaml"
    write_config(invalid_config_path, bad_param=True)
    try:
        load_settings(invalid_config_path)
    except ValueError as exc:
        if "bad_param" not in str(exc):
            raise AssertionError(f"Unexpected validation error: {exc}") from exc
    else:
        raise AssertionError("Expected unsupported node param validation to fail")

    example_settings = load_settings(ROOT_DIR / "config.yaml.example")
    if "shot_video_generation" not in example_settings.nodes:
        raise AssertionError("config.yaml.example did not load node model settings")
    if "design_key_vision_image" not in example_settings.nodes:
        raise AssertionError("config.yaml.example did not load key vision node model settings")
    if "storyboard_prompt" not in example_settings.nodes:
        raise AssertionError("config.yaml.example did not load storyboard prompt node model settings")
    if "storyboard_sheet_generation" not in example_settings.nodes:
        raise AssertionError("config.yaml.example did not load storyboard sheet image node model settings")
    if "storyboard_bbox_detection" not in example_settings.nodes:
        raise AssertionError("config.yaml.example did not load storyboard bbox detection node model settings")

    print("model_catalog_node_binding_smoke=ok")
    print(f"config_path={config_path}")
    print(f"text_model={text_provider.model}")
    print(f"storyboard_sheet_resolution={storyboard_sheet_payload['resolution']}")
    print(f"image_payload_size={payload['size']}")
    print(f"key_vision_payload_size={key_vision_payload['size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
