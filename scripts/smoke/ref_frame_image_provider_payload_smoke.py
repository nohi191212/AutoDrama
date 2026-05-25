from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify ref-frame image routing supports ToAPI GPT Image 2 and Seedream payloads."
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml.example"))
    return parser


def write_reference_image() -> Path:
    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "ref_frame_image_provider_payload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_dir / "reference.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )
    return image_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    image_path = write_reference_image()
    toapi_refs = [AssetRef(id="reference", type="image", url="https://example.invalid/reference.png")]
    local_refs = [AssetRef(id="reference", type="image", path=str(image_path))]
    metadata = {"node_name": "ref_frame_generation"}

    default_provider = ProviderRouter(settings).image("ref_frame")
    if not isinstance(default_provider, ToAPIImageProvider):
        raise AssertionError(f"Expected default ref_frame provider to be ToAPI; got {type(default_provider)}")
    toapi_payload = default_provider.build_payload(
        "生成 9:16 竖屏短剧参考帧，保持人物、场景和道具与参考图一致。",
        refs=toapi_refs,
        metadata=metadata,
    )
    if toapi_payload["model"] != settings.providers["toapi"].models["image"]:
        raise AssertionError(f"Unexpected ToAPI model: {toapi_payload['model']}")
    if toapi_payload["size"] != settings.providers["toapi"].options["ref_frame_size"]:
        raise AssertionError(f"Unexpected ToAPI ref frame size: {toapi_payload['size']}")
    if toapi_payload["resolution"] != settings.providers["toapi"].options["ref_frame_resolution"]:
        raise AssertionError(f"Unexpected ToAPI ref frame resolution: {toapi_payload['resolution']}")
    toapi_images = toapi_payload.get("reference_images")
    if toapi_images != ["https://example.invalid/reference.png"]:
        raise AssertionError(f"ToAPI ref-frame reference image was not sent as URL list: {toapi_images}")

    seedream_settings = settings.model_copy(deep=True)
    seedream_settings.routing.setdefault("image", {})["ref_frame"] = "volcengine"
    seedream_provider = ProviderRouter(seedream_settings).image("ref_frame")
    if not isinstance(seedream_provider, VolcengineSeedreamImageProvider):
        raise AssertionError(f"Expected Seedream ref_frame provider; got {type(seedream_provider)}")
    seedream_payload = seedream_provider.build_payload(
        "生成 9:16 竖屏短剧参考帧，保持人物、场景和道具与参考图一致。",
        refs=local_refs,
        metadata=metadata,
    )
    if seedream_payload["model"] != settings.providers["volcengine"].models["seedream_5"]:
        raise AssertionError(f"Unexpected Seedream model: {seedream_payload['model']}")
    if seedream_payload["size"] != settings.providers["volcengine"].options["seedream_ref_frame_size"]:
        raise AssertionError(f"Unexpected Seedream size: {seedream_payload['size']}")
    if not str(seedream_payload.get("image", "")).startswith("data:image/png;base64,"):
        raise AssertionError("Seedream ref-frame reference image was not encoded as data URL")

    output_path = image_path.parent / "payloads.json"
    output_path.write_text(
        json.dumps(
            {
                "toapi": {
                    **toapi_payload,
                },
                "seedream": {
                    **seedream_payload,
                    "image": "<base64 data URL omitted>",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("ref_frame_image_provider_payload_smoke=ok")
    print(
        f"default_provider={default_provider.name} model={toapi_payload['model']} "
        f"size={toapi_payload['size']} resolution={toapi_payload['resolution']}"
    )
    print(f"alternate_provider={seedream_provider.name} model={seedream_payload['model']} size={seedream_payload['size']}")
    print(f"payloads_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
