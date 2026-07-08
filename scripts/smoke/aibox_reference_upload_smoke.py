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
from autodrama.core.errors import ProviderBadResponseError
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider
from autodrama.providers.base import AssetRef


class FakeReferenceUploader:
    name = "toapi"

    def __init__(self) -> None:
        self.uploaded_paths: list[Path] = []

    async def _upload_reference_image(self, client: Any, path: Path) -> dict[str, Any]:
        self.uploaded_paths.append(path)
        return {
            "path": str(path),
            "url": f"https://files.toapis.com/tmp/{path.name}",
            "id": "upload_1",
        }


async def _run() -> None:
    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    local_ref = tmp_dir / "aibox_reference_upload_source.png"
    local_ref.write_bytes(b"placeholder image bytes")

    provider = AiboxImageProvider(
        ProviderSettings(
            api_key_env="sk-test-aibox",
            models={"image": "gpt-image-2-guan"},
            options={"max_reference_images": 4},
        ),
        RuntimeSettings(),
    )
    uploader = FakeReferenceUploader()
    provider.reference_uploader = uploader

    existing_url_ref = AssetRef(
        id="existing_url",
        type="image",
        path=str(local_ref),
        url="https://cdn.example.test/existing.png",
    )
    local_ref_asset = AssetRef(id="local_ref", type="image", path=str(local_ref))

    images, uploaded = await provider._resolve_reference_images(
        object(),
        [existing_url_ref, local_ref_asset],
        metadata={},
    )

    expected_uploaded_url = f"https://files.toapis.com/tmp/{local_ref.name}"
    if images != ["https://cdn.example.test/existing.png", expected_uploaded_url]:
        raise AssertionError(f"unexpected resolved images: {images!r}")
    if len(uploaded) != 1 or uploaded[0]["url"] != expected_uploaded_url:
        raise AssertionError(f"unexpected uploaded refs: {uploaded!r}")
    if uploader.uploaded_paths != [local_ref]:
        raise AssertionError(f"unexpected upload paths: {uploader.uploaded_paths!r}")
    if local_ref_asset.url != expected_uploaded_url:
        raise AssertionError(f"local AssetRef.url was not bound: {local_ref_asset.url!r}")

    payload = provider.build_payload("prompt", reference_images=images, metadata={})
    payload_images = payload.get("params", {}).get("images")
    if payload_images != images:
        raise AssertionError(f"payload did not use resolved URLs: {payload_images!r}")
    if any(str(value).startswith("data:image/") for value in payload_images):
        raise AssertionError(f"payload leaked inline image data: {payload_images!r}")

    try:
        await provider._resolve_reference_images(
            object(),
            [AssetRef(id="inline", type="image", path="data:image/png;base64,abc")],
            metadata={},
        )
    except ProviderBadResponseError as exc:
        if "public URL" not in str(exc):
            raise AssertionError(f"unexpected inline-data error: {exc}") from exc
    else:
        raise AssertionError("inline data reference should be rejected for AIBOX")

    (tmp_dir / "aibox_reference_upload_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("aibox_reference_upload_smoke: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
