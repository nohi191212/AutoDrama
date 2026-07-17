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


class RecordingToAPIImageProvider(ToAPIImageProvider):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.uploaded_paths: list[Path] = []

    async def _upload_reference_image(self, client: Any, path: Path) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        self.uploaded_paths.append(path)
        return {"path": str(path), "url": f"https://files.toapis.com/tmp/{path.name}"}


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

    refresh_provider = RecordingToAPIImageProvider(
        ProviderSettings(
            models={"image": "gpt-image-2"},
            options={"max_reference_images": 4},
        ),
        RuntimeSettings(),
    )
    expired_url = (
        "https://bucket.example.test/layout.png?"
        "X-Amz-Date=20200101T000000Z&X-Amz-Expires=60&X-Amz-Signature=expired"
    )
    expired_ref = AssetRef(
        id="layout_expired",
        type="image",
        path=str(local_ref),
        url=expired_url,
    )
    refreshed_images, refreshed_uploads = await refresh_provider._reference_images(object(), [expired_ref])
    expected_refreshed_url = f"https://files.toapis.com/tmp/{local_ref.name}"
    if refreshed_images != [expected_refreshed_url] or refresh_provider.uploaded_paths != [local_ref]:
        raise AssertionError("expired ToAPI reference URL did not trigger a local re-upload")
    if len(refreshed_uploads) != 1 or expired_ref.url != expected_refreshed_url:
        raise AssertionError("refreshed ToAPI URL was not bound back to the AssetRef")
    if expired_ref.metadata.get("expired_asset_url") != expired_url:
        raise AssertionError("expired ToAPI reference URL was not retained in metadata")
    if expired_ref.metadata.get("expired_url_cleared_for_local_refresh"):
        raise AssertionError("ToAPI left a stale pending-refresh marker after upload")

    concurrent_refs = [
        AssetRef(
            id=f"layout_concurrent_{index}",
            type="image",
            path=str(local_ref),
            url=f"{expired_url}&request={index}",
        )
        for index in range(4)
    ]
    concurrent_results = await asyncio.gather(
        *(
            refresh_provider.reupload_expired_reference_images([ref])
            for ref in concurrent_refs
        )
    )
    if refresh_provider.uploaded_paths != [local_ref, local_ref]:
        raise AssertionError("concurrent expired refs for one local image were uploaded more than once")
    if any(ref.url != expected_refreshed_url for ref in concurrent_refs):
        raise AssertionError("concurrent expired refs did not share the refreshed ToAPI URL")
    if any(len(result) != 1 for result in concurrent_results):
        raise AssertionError("coalesced ToAPI re-upload did not return one record per reference")

    (tmp_dir / "toapi_reference_url_preference_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("toapi_reference_url_preference_smoke: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
