from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.model_catalog import ModelBinding, ModelSpec  # noqa: E402
from autodrama.providers.base import AssetRef, ImageGenerationResult  # noqa: E402
from autodrama.providers.media_refs import (  # noqa: E402
    is_remote_url_expired,
    refresh_expired_image_ref_urls,
    signed_url_expiration,
)
from autodrama.providers.router import BoundProviderProxy  # noqa: E402


class _CapturingImageProvider:
    name = "capture"
    model = "image"

    def __init__(self) -> None:
        self.received_refs: list[AssetRef] = []

    async def generate_image(self, _prompt: str, refs=None, *, size=None, metadata=None):
        del size, metadata
        self.received_refs = list(refs or [])
        return ImageGenerationResult(provider=self.name, model=self.model)


class _FakeToAPIUploader:
    def __init__(self) -> None:
        self.calls: list[tuple[list[AssetRef], bool]] = []

    async def reupload_expired_reference_images(
        self,
        refs: list[AssetRef] | None,
        *,
        force: bool = False,
    ) -> list[dict[str, str]]:
        targets = list(refs or [])
        self.calls.append((targets, force))
        uploaded: list[dict[str, str]] = []
        for ref in targets:
            old_url = str(ref.url or "")
            new_url = f"https://files.toapis.com/tmp/{Path(str(ref.path)).name}"
            ref.url = new_url
            ref.metadata["expired_asset_url"] = old_url
            ref.metadata["expired_url_reuploaded"] = True
            ref.metadata["uploaded_reference_image_provider"] = "toapi"
            uploaded.append({"url": new_url})
        return uploaded


def main() -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    expired_urls = [
        (
            "https://s3.example.test/image.png?"
            "X-Amz-Date=20260708T111329Z&X-Amz-Expires=86400&X-Amz-Signature=expired"
        ),
        (
            "https://storage.example.test/image.png?"
            "X-Goog-Date=20260708T111329Z&X-Goog-Expires=86400&X-Goog-Signature=expired"
        ),
        "https://cos.example.test/image.png?q-sign-time=1783500000;1783503600&q-signature=expired",
        "https://blob.example.test/image.png?se=2026-07-09T11%3A13%3A29Z&sig=expired",
    ]
    if any(not is_remote_url_expired(url, now=now) for url in expired_urls):
        raise AssertionError("a recognized expired signed URL was treated as usable")

    future_url = (
        "https://s3.example.test/image.png?"
        "X-Amz-Date=20260717T120000Z&X-Amz-Expires=86400&X-Amz-Signature=future"
    )
    if is_remote_url_expired(future_url, now=now):
        raise AssertionError("a future signed URL was treated as expired")
    if signed_url_expiration("https://cdn.example.test/stable.png") is not None:
        raise AssertionError("an ordinary CDN URL was treated as signed")

    tmp_dir = ROOT / ".tmp" / "expired_reference_url_refresh_smoke"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_image = tmp_dir / "layout.png"
    local_image.write_bytes(b"placeholder")
    ref = AssetRef(id="layout", type="image", path=str(local_image), url=expired_urls[0])
    if refresh_expired_image_ref_urls([ref]) != 1:
        raise AssertionError("expired local reference was not selected for refresh")
    if ref.url is not None:
        raise AssertionError("expired URL was not cleared before provider upload")
    if ref.metadata.get("expired_asset_url") != expired_urls[0]:
        raise AssertionError("expired URL was not retained in reference metadata")

    missing_local = AssetRef(
        id="missing",
        type="image",
        path=str(tmp_dir / "missing.png"),
        url=expired_urls[0],
    )
    if refresh_expired_image_ref_urls([missing_local]) != 0 or missing_local.url is None:
        raise AssertionError("expired remote-only reference should remain available for an explicit provider error")

    routed_ref = AssetRef(id="routed", type="image", path=str(local_image), url=expired_urls[0])
    capture = _CapturingImageProvider()
    uploader = _FakeToAPIUploader()
    proxy = BoundProviderProxy(
        capture,
        ModelBinding(
            node_name="smoke_image_generation",
            model_id="capture:image",
            provider="capture",
            capability="image",
            spec=ModelSpec(
                id="capture:image",
                provider="capture",
                capability="image",
                input_modalities=["text", "image"],
                output_modalities=["image"],
                limits={"max_reference_images": 1},
            ),
        ),
        reference_image_uploader=uploader,  # type: ignore[arg-type]
    )
    asyncio.run(proxy.generate_image("smoke", refs=[routed_ref]))
    expected_url = f"https://files.toapis.com/tmp/{local_image.name}"
    if not capture.received_refs or capture.received_refs[0].url != expected_url:
        raise AssertionError("bound provider route did not replace the expired URL through ToAPI")
    if len(uploader.calls) != 1 or uploader.calls[0][0] != [routed_ref] or uploader.calls[0][1]:
        raise AssertionError("bound provider route did not use the shared ToAPI uploader exactly once")
    if routed_ref.metadata.get("uploaded_reference_image_provider") != "toapi":
        raise AssertionError("refreshed reference was not marked as a ToAPI upload")

    print("expired_reference_url_refresh_smoke: ok")


if __name__ == "__main__":
    main()
