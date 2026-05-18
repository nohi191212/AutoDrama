from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider  # noqa: E402


def main() -> int:
    settings = ProviderSettings(
        base_url="https://www.right.codes/draw",
        api_key_env="RIGHTCODE_API_KEY",
        models={"image": "gpt-image-2"},
        options={"size": "1024x1024", "n": 1},
    )
    provider = RightCodeImageProvider(settings, RuntimeSettings())
    payload = provider._build_chat_payload("A clean test image.", metadata={})
    if provider.endpoint != "https://www.right.codes/draw/v1/chat/completions":
        raise AssertionError(f"Unexpected endpoint: {provider.endpoint}")
    if payload["model"] != "gpt-image-2":
        raise AssertionError(f"Unexpected model: {payload['model']}")
    if payload["messages"][0]["content"] != "A clean test image.":
        raise AssertionError("Prompt was not placed in messages content")
    if "prompt" in payload:
        raise AssertionError("Chat completions payload should not use top-level prompt")

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

    print("rightcode_chat_image_payload_smoke=ok")
    print(f"endpoint={provider.endpoint}")
    print(f"model={payload['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
