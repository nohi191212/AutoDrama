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
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "models": {"text": "deepseek-v4-pro"},
                "options": {"reasoning_effort": "max", "thinking_enabled": True},
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
            "text": {"storyboard": "deepseek"},
            "image": {"role": "toapi"},
            "video": {"shot": "volcengine"},
        },
        "nodes": {
            "storyboard_generation": {
                "model": "deepseek:deepseek-v4-flash",
                "params": {
                    "reasoning_effort": "low",
                    "thinking_enabled": False,
                    "temperature": 0.2,
                },
            },
            "role_full_body_generation": {
                "model": "toapi:gpt-image-2",
                "params": {
                    "size": "1:2",
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
                },
            },
        },
    }
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


async def assert_runtime_ref_limit(router: ProviderRouter) -> None:
    provider = router.video("shot", node_name="shot_video_generation")
    refs = [
        AssetRef(id=f"ref_{index}", type="image", url=f"https://example.invalid/{index}.png")
        for index in range(3)
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

    text_provider = router.text("storyboard", node_name="storyboard_generation")
    if getattr(text_provider, "model", None) != "deepseek-v4-flash":
        raise AssertionError(f"Unexpected text model: {getattr(text_provider, 'model', None)}")
    if getattr(text_provider, "reasoning_effort", None) != "low":
        raise AssertionError("Node params did not update DeepSeek reasoning_effort")
    if getattr(text_provider, "thinking_enabled", None) is not False:
        raise AssertionError("Node params did not update DeepSeek thinking_enabled")

    image_provider = router.image("role", node_name="role_full_body_generation")
    payload = image_provider._provider.build_payload(
        "test image",
        metadata=image_provider._metadata({"asset_id": "role_001"}),
    )
    if payload["model"] != "gpt-image-2":
        raise AssertionError(f"Unexpected image model: {payload['model']}")
    if payload["size"] != "1:2":
        raise AssertionError(f"Unexpected image size: {payload['size']}")
    if payload["resolution"] != "4K":
        raise AssertionError(f"Unexpected image resolution: {payload['resolution']}")

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

    print("model_catalog_node_binding_smoke=ok")
    print(f"config_path={config_path}")
    print(f"text_model={text_provider.model}")
    print(f"image_payload_size={payload['size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
