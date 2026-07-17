from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings
from autodrama.core.errors import ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


class SmokeOutput(BaseModel):
    value: str


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


async def main() -> None:
    tmp_dir = ROOT / ".tmp" / "rightcode_expired_image_refresh_smoke"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_dir / "layout.png"
    image_path.write_bytes(b"placeholder")

    settings = ProviderSettings(
        base_url="https://www.right.codes",
        api_key_env="sk-smoke",
        models={"storyboard": "gpt-5.6-terra"},
    )
    provider = RightCodeTextProvider(settings, RuntimeSettings(max_text_retry=3), model_key="storyboard")
    uploader = _FakeToAPIUploader()
    provider.reference_image_uploader = uploader
    calls = 0
    payloads: list[dict[str, Any]] = []

    async def fake_post(payload: dict[str, Any], **_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        payloads.append(payload)
        if calls == 1:
            raise ProviderBadResponseError(
                "RightCode text streaming request failed with HTTP 500: "
                "failed to download file, status code: 403; code=count_token_failed"
            )
        return {"output": [{"content": [{"type": "output_text", "text": '{"value":"ok"}'}]}]}

    provider._post_responses = fake_post  # type: ignore[method-assign]
    ref = AssetRef(
        id="layout_001",
        type="image",
        path=str(image_path),
        url="https://expired.example/image.png",
        metadata={"asset_type": "layout", "layout_id": "layout_001"},
    )
    result = await provider.generate_json("smoke", SmokeOutput, refs=[ref])
    if result.value != "ok" or calls != 2:
        raise AssertionError("request was not retried successfully after URL refresh")
    expected_url = f"https://files.toapis.com/tmp/{image_path.name}"
    if ref.url != expected_url:
        raise AssertionError("AssetRef URL was not refreshed through ToAPI")
    if ref.metadata.get("expired_asset_url") != "https://expired.example/image.png":
        raise AssertionError("old URL was not retained in metadata")
    if len(uploader.calls) != 1 or uploader.calls[0] != ([ref], True):
        raise AssertionError("RightCode retry did not force exactly one shared ToAPI re-upload")
    if ref.metadata.get("uploaded_reference_image_provider") != "toapi":
        raise AssertionError("RightCode retry was not marked as a ToAPI upload")
    if "data:image" in str(payloads[1]):
        raise AssertionError("RightCode retry unexpectedly used an inline base64 reference")
    if expected_url not in str(payloads[1]):
        raise AssertionError("RightCode retry payload did not use the refreshed ToAPI URL")
    print("rightcode_expired_image_refresh_smoke: ok")


if __name__ == "__main__":
    asyncio.run(main())
