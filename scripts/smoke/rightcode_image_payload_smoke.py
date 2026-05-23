from __future__ import annotations

import base64
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider  # noqa: E402


def main() -> int:
    settings = ProviderSettings(
        base_url="https://www.right.codes/draw",
        api_key_env="RIGHTCODE_API_KEY",
        models={
            "image": "gpt-image-2",
            "role_design": "gpt-image-2-vip",
            "rightcode_role_multiview": "gpt-image-2-multiview",
            "rightcode_role_full_body": "gpt-image-2-full-body",
        },
        options={
            "size": "1024×1024",
            "role_design_size": "3840×2160",
            "role_design_quality": "high",
            "rightcode_role_multiview_size": "4096×2304",
            "rightcode_role_multiview_quality": "high",
            "rightcode_role_full_body_size": "1024×1536",
            "rightcode_role_full_body_quality": "medium",
            "n": 1,
        },
    )
    provider = RightCodeImageProvider(settings, RuntimeSettings())

    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "rightcode_image_payload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    reference_path = tmp_dir / "reference.png"
    reference_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )

    payload = provider.build_payload(
        "A clean test image.",
        refs=[AssetRef(id="reference", type="image", path=str(reference_path))],
        metadata={},
    )
    if provider.endpoint != "https://www.right.codes/draw/v1/images/generations":
        raise AssertionError(f"Unexpected endpoint: {provider.endpoint}")
    if payload["model"] != "gpt-image-2":
        raise AssertionError(f"Unexpected model: {payload['model']}")
    if payload["prompt"] != "A clean test image.":
        raise AssertionError("Prompt was not placed in the top-level OpenAI image payload")
    if "messages" in payload:
        raise AssertionError("OpenAI image generation payload should not use chat messages")
    if payload.get("size") != "1024x1024":
        raise AssertionError(f"Unexpected size: {payload.get('size')}")
    if payload.get("n") != 1:
        raise AssertionError(f"Unexpected n: {payload.get('n')}")
    images = payload.get("image")
    if not isinstance(images, list) or len(images) != 1:
        raise AssertionError(f"Reference image was not sent as an image array: {images}")
    if not str(images[0]).startswith("data:image/png;base64,"):
        raise AssertionError("Reference image was not encoded as a png data URL")

    role_multiview_payload = provider.build_payload(
        "A clean role multiview sheet.",
        metadata={"node_name": "role_multiview_generation", "asset_type": "role_multiview"},
    )
    if role_multiview_payload["model"] != "gpt-image-2-multiview":
        raise AssertionError(f"Unexpected role multiview model: {role_multiview_payload['model']}")
    if role_multiview_payload["size"] != "4096x2304":
        raise AssertionError(f"Unexpected role multiview size: {role_multiview_payload['size']}")
    if role_multiview_payload["quality"] != "high":
        raise AssertionError(f"Unexpected role multiview quality: {role_multiview_payload['quality']}")

    legacy_role_design_payload = provider.build_payload(
        "A clean legacy role design sheet.",
        metadata={"node_name": "role_appearance_generation", "asset_type": "role_appearance"},
    )
    if legacy_role_design_payload["model"] != "gpt-image-2-multiview":
        raise AssertionError(f"Unexpected legacy role appearance model: {legacy_role_design_payload['model']}")
    if legacy_role_design_payload["size"] != "4096x2304":
        raise AssertionError(f"Unexpected legacy role appearance size: {legacy_role_design_payload['size']}")
    if legacy_role_design_payload["quality"] != "high":
        raise AssertionError(f"Unexpected legacy role appearance quality: {legacy_role_design_payload['quality']}")

    full_body_payload = provider.build_payload(
        "A clean role full-body reference.",
        metadata={"node_name": "role_full_body_generation", "asset_type": "role_full_body"},
    )
    if full_body_payload["model"] != "gpt-image-2-full-body":
        raise AssertionError(f"Unexpected full-body model: {full_body_payload['model']}")
    if full_body_payload["size"] != "1024x1536":
        raise AssertionError(f"Unexpected full-body size: {full_body_payload['size']}")
    if full_body_payload["quality"] != "medium":
        raise AssertionError(f"Unexpected full-body quality: {full_body_payload['quality']}")

    image_urls, image_data = provider._extract_images(
        {
            "choices": [
                {
                    "message": {
                        "content": {
                            "data": [
                                {
                                    "url": "https://example.invalid/generated.png",
                                    "b64_json": "ZmFrZV9pbWFnZQ==",
                                }
                            ]
                        }
                    }
                }
            ]
        }
    )
    if image_urls != ["https://example.invalid/generated.png"]:
        raise AssertionError(f"Unexpected image URLs: {image_urls}")
    if image_data != ["ZmFrZV9pbWFnZQ=="]:
        raise AssertionError(f"Unexpected image data: {image_data}")

    print("rightcode_image_payload_smoke=ok")
    print(f"endpoint={provider.endpoint}")
    print(f"model={payload['model']}")
    print(
        "role_multiview="
        f"{role_multiview_payload['model']} size={role_multiview_payload['size']} "
        f"quality={role_multiview_payload['quality']}"
    )
    print(
        "full_body="
        f"{full_body_payload['model']} size={full_body_payload['size']} "
        f"quality={full_body_payload['quality']}"
    )
    print(f"reference_path={reference_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
