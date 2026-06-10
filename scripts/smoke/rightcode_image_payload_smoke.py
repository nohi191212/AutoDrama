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
            "rightcode_roleboard": "gpt-image-2-roleboard",
        },
        options={
            "size": "1024×1024",
            "rightcode_roleboard_size": "4096×2304",
            "rightcode_roleboard_quality": "high",
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

    roleboard_payload = provider.build_payload(
        "A clean role identity board.",
        metadata={"node_name": "roleboard_generation", "asset_type": "roleboard"},
    )
    if roleboard_payload["model"] != "gpt-image-2-roleboard":
        raise AssertionError(f"Unexpected roleboard model: {roleboard_payload['model']}")
    if roleboard_payload["size"] != "4096x2304":
        raise AssertionError(f"Unexpected roleboard size: {roleboard_payload['size']}")
    if roleboard_payload["quality"] != "high":
        raise AssertionError(f"Unexpected roleboard quality: {roleboard_payload['quality']}")

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
        "roleboard="
        f"{roleboard_payload['model']} size={roleboard_payload['size']} "
        f"quality={roleboard_payload['quality']}"
    )
    print(f"reference_path={reference_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
