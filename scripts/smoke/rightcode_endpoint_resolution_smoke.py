from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    args = parser.parse_args()

    settings = load_settings(args.config)
    rightcode_settings = settings.providers["rightcode"]

    text_provider = RightCodeTextProvider(rightcode_settings, settings.runtime, model_key="script")
    image_provider = RightCodeImageProvider(rightcode_settings, settings.runtime)
    vip_image_provider = RightCodeImageProvider(rightcode_settings, settings.runtime)
    vip_image_provider.model = "gpt-image-2-vip"
    vip_image_provider.refresh_endpoint()

    expected_text_endpoint = "https://www.right.codes/codex/v1/responses"
    expected_image_endpoint = "https://www.right.codes/draw/v1/images/generations"

    assert text_provider.endpoint == expected_text_endpoint, text_provider.endpoint
    assert text_provider.stream is True, text_provider.stream
    assert image_provider.endpoint == expected_image_endpoint, image_provider.endpoint
    assert vip_image_provider.endpoint == expected_image_endpoint, vip_image_provider.endpoint

    print(f"text_endpoint={text_provider.endpoint}")
    print(f"text_stream={text_provider.stream}")
    print(f"image_endpoint={image_provider.endpoint}")
    print(f"vip_image_endpoint={vip_image_provider.endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
