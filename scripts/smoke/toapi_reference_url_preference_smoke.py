from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.providers.base import AssetRef
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider


class NoUploadToAPIImageProvider(ToAPIImageProvider):
    async def _upload_reference_image(self, client: Any, path: Path) -> dict[str, Any]:
        raise AssertionError(f"unexpected upload for reference with url: {path}")


async def _run() -> None:
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    local_ref = tmp_dir / "toapi_reference_url_preference_source.png"
    local_ref.write_bytes(b"local placeholder")

    provider = NoUploadToAPIImageProvider(
        ProviderSettings(
            models={"image": "gpt-image-2"},
            options={"max_reference_images": 4},
        ),
        RuntimeSettings(),
    )
    url = "https://cdn.example.test/layout.png"
    images, uploaded = await provider._reference_images(
        object(),
        [
            AssetRef(
                id="layout_a",
                type="image",
                path=str(local_ref),
                url=url,
            )
        ],
    )

    if images != [url]:
        raise AssertionError(f"expected url reference, got {images!r}")
    if uploaded:
        raise AssertionError(f"expected no uploaded refs, got {uploaded!r}")

    (tmp_dir / "toapi_reference_url_preference_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("toapi_reference_url_preference_smoke: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
