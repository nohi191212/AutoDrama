from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "toapi_reference_resize"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / "large_reference.png"

    width = 2048
    height = 2048
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    image.save(source_path, format="PNG")

    provider = ToAPIImageProvider(
        ProviderSettings(base_url="https://toapis.com", api_key_env="sk-smoke-test"),
        RuntimeSettings(),
    )
    original_size = source_path.stat().st_size
    require(
        original_size > provider.max_reference_upload_bytes,
        f"Smoke source image should exceed upload limit; size={original_size}",
    )

    upload = provider._prepare_reference_upload(source_path)
    upload_size = int(upload["upload_size"])
    require(upload["resized_for_upload"], "Expected image to be resized for upload")
    require(
        provider.min_reference_upload_bytes <= upload_size <= provider.max_reference_upload_bytes,
        f"Unexpected upload size: {upload_size}",
    )
    require(upload_size < original_size, "Expected upload bytes to be smaller than original")

    resized = Image.open(BytesIO(upload["data"]))
    require(resized.width <= width and resized.height <= height, "Resized dimensions should not grow")
    require(resized.width >= 1 and resized.height >= 1, "Invalid resized dimensions")

    resized_path = output_dir / str(upload["filename"])
    resized_path.write_bytes(upload["data"])

    print("toapi_reference_resize_smoke=ok")
    print(f"original_size={original_size}")
    print(f"upload_size={upload_size}")
    print(f"original_dimensions={width}x{height}")
    print(f"upload_dimensions={resized.width}x{resized.height}")
    print(f"resized_path={resized_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
