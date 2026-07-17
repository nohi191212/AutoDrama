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
from autodrama.core.model_catalog import ModelBinding, ModelSpec
from autodrama.core.errors import ProviderBadResponseError
from autodrama.providers.base import AssetRef
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider
from autodrama.providers.router import BoundProviderProxy


class SmokeOutput(BaseModel):
    value: str


class _FakeToAPIUploader:
    def __init__(self) -> None:
        self.calls: list[tuple[list[AssetRef], bool]] = []

    async def ensure_reference_image_urls(
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
    calls = 0
    payloads: list[dict[str, Any]] = []

    async def fake_post(payload: dict[str, Any], **_: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        payloads.append(payload)
        return {"output": [{"content": [{"type": "output_text", "text": '{"value":"ok"}'}]}]}

    provider._post_responses = fake_post  # type: ignore[method-assign]
    ref = AssetRef(
        id="layout_001",
        type="image",
        path=str(image_path),
        url=(
            "https://bucket.example.test/image.png?"
            "X-Amz-Date=20200101T000000Z&X-Amz-Expires=60&X-Amz-Signature=expired"
        ),
        metadata={"asset_type": "layout", "layout_id": "layout_001"},
    )
    proxy = BoundProviderProxy(
        provider,
        ModelBinding(
            node_name="clip_storyboard_prompt",
            model_id="rightcode:gpt-5.6-terra",
            provider="rightcode",
            capability="text",
            spec=ModelSpec(
                id="rightcode:gpt-5.6-terra",
                provider="rightcode",
                capability="text",
                input_modalities=["text", "image"],
                output_modalities=["text"],
            ),
        ),
        reference_image_uploader=uploader,  # type: ignore[arg-type]
    )
    result = await proxy.generate_json("smoke", SmokeOutput, refs=[ref])
    if result.value != "ok" or calls != 1:
        raise AssertionError("RightCode should receive one request after URL resolution")
    expected_url = f"https://files.toapis.com/tmp/{image_path.name}"
    if ref.url != expected_url:
        raise AssertionError("AssetRef URL was not refreshed through ToAPI")
    if "X-Amz-Date=20200101" not in str(ref.metadata.get("expired_asset_url")):
        raise AssertionError("old URL was not retained in metadata")
    if len(uploader.calls) != 1 or uploader.calls[0] != ([ref], False):
        raise AssertionError("bound provider did not resolve the image URL exactly once")
    if ref.metadata.get("uploaded_reference_image_provider") != "toapi":
        raise AssertionError("RightCode retry was not marked as a ToAPI upload")
    if "data:image" in str(payloads[0]):
        raise AssertionError("RightCode unexpectedly used an inline base64 reference")
    if expected_url not in str(payloads[0]):
        raise AssertionError("RightCode payload did not use the refreshed ToAPI URL")

    try:
        provider.build_payload(
            "smoke",
            SmokeOutput,
            refs=[AssetRef(id="local_only", type="image", path=str(image_path))],
        )
    except ProviderBadResponseError as exc:
        if "requires a public URL" not in str(exc):
            raise AssertionError(f"unexpected local image error: {exc}") from exc
    else:
        raise AssertionError("RightCode must reject local/base64 image references")
    print("rightcode_expired_image_refresh_smoke: ok")


if __name__ == "__main__":
    asyncio.run(main())
