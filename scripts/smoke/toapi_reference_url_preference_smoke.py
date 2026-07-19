from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderBadResponseError
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


class FlakyUploadToAPIImageProvider(ToAPIImageProvider):
    def __init__(self, *args: Any, failures_before_success: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failures_before_success = failures_before_success
        self.upload_attempts = 0

    async def _upload_reference_image(self, client: Any, path: Path) -> dict[str, Any]:
        self.upload_attempts += 1
        if self.upload_attempts <= self.failures_before_success:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
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
    if refresh_provider.uploaded_paths != [local_ref]:
        raise AssertionError("concurrent expired refs for one local image were uploaded more than once")
    if any(ref.url != expected_refreshed_url for ref in concurrent_refs):
        raise AssertionError("concurrent expired refs did not share the refreshed ToAPI URL")
    if any(len(result) != 1 for result in concurrent_results):
        raise AssertionError("coalesced ToAPI re-upload did not return one record per reference")

    persistent_project = tmp_dir / "toapi_reference_url_persistence" / "project"
    persistent_ref_path = persistent_project / "assets" / "images" / "layouts" / "layout.png"
    persistent_ref_path.parent.mkdir(parents=True, exist_ok=True)
    persistent_ref_path.write_bytes(b"persistent reference placeholder")
    persistent_cache_path = (
        persistent_project / "assets" / "json" / "cache" / "reference_image_urls.json"
    )
    if persistent_cache_path.exists():
        persistent_cache_path.unlink()

    first_provider = RecordingToAPIImageProvider(
        ProviderSettings(models={"image": "gpt-image-2"}),
        RuntimeSettings(),
    )
    first_ref = AssetRef(id="persistent", type="image", path=str(persistent_ref_path))
    await first_provider.ensure_reference_image_urls([first_ref])
    if len(first_provider.uploaded_paths) != 1 or not persistent_cache_path.exists():
        raise AssertionError("first ToAPI upload did not persist its refreshed URL")
    persistent_payload = json.loads(persistent_cache_path.read_text(encoding="utf-8"))
    persistent_entry = persistent_payload["references"]["assets/images/layouts/layout.png"]
    if not persistent_entry.get("expires_at"):
        raise AssertionError("persisted ToAPI URL is missing its effective cache expiry")

    second_provider = RecordingToAPIImageProvider(
        ProviderSettings(models={"image": "gpt-image-2"}),
        RuntimeSettings(),
    )
    second_ref = AssetRef(
        id="persistent",
        type="image",
        path=str(persistent_ref_path),
        url=expired_url,
    )
    second_result = await second_provider.ensure_reference_image_urls([second_ref])
    if second_provider.uploaded_paths:
        raise AssertionError("a new provider process did not reuse the persisted ToAPI URL")
    if second_ref.url != first_ref.url or not second_result[0].get("persistent_cache"):
        raise AssertionError("persisted ToAPI URL did not override the expired asset URL")

    retry_project = tmp_dir / "toapi_reference_upload_retry" / "project"
    retry_ref_path = retry_project / "assets" / "images" / "storyboards" / "clip_004.png"
    retry_ref_path.parent.mkdir(parents=True, exist_ok=True)
    retry_ref_path.write_bytes(b"retry reference placeholder")
    retry_cache_path = retry_project / "assets" / "json" / "cache" / "reference_image_urls.json"
    if retry_cache_path.exists():
        retry_cache_path.unlink()
    retry_options = {
        "toapi_reference_upload_max_attempts": 3,
        "toapi_reference_upload_retry_initial_delay_seconds": 0,
        "toapi_reference_upload_retry_max_delay_seconds": 0,
    }
    retry_provider = FlakyUploadToAPIImageProvider(
        ProviderSettings(models={"image": "gpt-image-2"}, options=retry_options),
        RuntimeSettings(),
        failures_before_success=2,
    )
    delay_provider = FlakyUploadToAPIImageProvider(
        ProviderSettings(
            models={"image": "gpt-image-2"},
            options={
                "toapi_reference_upload_retry_initial_delay_seconds": 2,
                "toapi_reference_upload_retry_max_delay_seconds": 5,
            },
        ),
        RuntimeSettings(),
        failures_before_success=0,
    )
    delays = [delay_provider._reference_upload_retry_delay_seconds(attempt) for attempt in range(1, 5)]
    if delays != [2, 4, 5, 5]:
        raise AssertionError(f"unexpected ToAPI reference upload exponential backoff: {delays}")
    retry_ref = AssetRef(id="retry", type="image", path=str(retry_ref_path))
    await retry_provider.ensure_reference_image_urls([retry_ref])
    if retry_provider.upload_attempts != 3 or not retry_ref.url:
        raise AssertionError("ToAPI reference upload did not recover on the configured retry attempt")

    failing_ref_path = retry_project / "assets" / "images" / "storyboards" / "always_fail.png"
    failing_ref_path.write_bytes(b"failing reference placeholder")
    failing_provider = FlakyUploadToAPIImageProvider(
        ProviderSettings(models={"image": "gpt-image-2"}, options=retry_options),
        RuntimeSettings(),
        failures_before_success=99,
    )
    failing_ref = AssetRef(id="always_fail", type="image", path=str(failing_ref_path))
    try:
        await failing_provider.ensure_reference_image_urls([failing_ref])
    except ProviderBadResponseError as exc:
        error_text = str(exc)
        if "failed after 3 attempt(s)" not in error_text or str(failing_ref_path) not in error_text:
            raise AssertionError(f"unexpected final reference upload error: {error_text}") from exc
    else:
        raise AssertionError("ToAPI reference upload should fail after exhausting retries")
    if failing_provider.upload_attempts != 3:
        raise AssertionError("ToAPI reference upload did not stop at the configured attempt limit")

    (tmp_dir / "toapi_reference_url_preference_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("toapi_reference_url_preference_smoke: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
