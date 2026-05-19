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
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify ref-frame image routing supports RightCode GPT Image 2 and Seedream payloads."
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
    refs = [AssetRef(id="reference", type="image", path=str(image_path))]
    metadata = {"node_name": "ref_frame_generation"}

    default_provider = ProviderRouter(settings).image("ref_frame")
    if not isinstance(default_provider, RightCodeImageProvider):
        raise AssertionError(f"Expected default ref_frame provider to be RightCode; got {type(default_provider)}")
    rightcode_payload = default_provider.build_payload(
        "生成 9:16 竖屏短剧参考帧，保持人物、场景和道具与参考图一致。",
        refs=refs,
        metadata=metadata,
    )
    if rightcode_payload["model"] != settings.providers["rightcode"].models["image"]:
        raise AssertionError(f"Unexpected RightCode model: {rightcode_payload['model']}")
    if rightcode_payload["size"] != settings.providers["rightcode"].options["ref_frame_size"]:
        raise AssertionError(f"Unexpected RightCode ref frame size: {rightcode_payload['size']}")
    rightcode_images = rightcode_payload.get("image")
    if not isinstance(rightcode_images, list) or not rightcode_images[0].startswith("data:image/png;base64,"):
        raise AssertionError("RightCode ref-frame reference image was not encoded as data URL list")

    seedream_settings = settings.model_copy(deep=True)
    seedream_settings.routing.setdefault("image", {})["ref_frame"] = "volcengine"
    seedream_provider = ProviderRouter(seedream_settings).image("ref_frame")
    if not isinstance(seedream_provider, VolcengineSeedreamImageProvider):
        raise AssertionError(f"Expected Seedream ref_frame provider; got {type(seedream_provider)}")
    seedream_payload = seedream_provider.build_payload(
        "生成 9:16 竖屏短剧参考帧，保持人物、场景和道具与参考图一致。",
        refs=refs,
        metadata=metadata,
    )
    if seedream_payload["model"] != settings.providers["volcengine"].models["seedream_5_lite"]:
        raise AssertionError(f"Unexpected Seedream model: {seedream_payload['model']}")
    if seedream_payload["size"] != settings.providers["volcengine"].options["seedream_image_size"]:
        raise AssertionError(f"Unexpected Seedream size: {seedream_payload['size']}")
    if not str(seedream_payload.get("image", "")).startswith("data:image/png;base64,"):
        raise AssertionError("Seedream ref-frame reference image was not encoded as data URL")

    output_path = image_path.parent / "payloads.json"
    output_path.write_text(
        json.dumps(
            {
                "rightcode": {
                    **rightcode_payload,
                    "image": ["<base64 data URL omitted>"],
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
    print(f"default_provider={default_provider.name} model={rightcode_payload['model']} size={rightcode_payload['size']}")
    print(f"alternate_provider={seedream_provider.name} model={seedream_payload['model']} size={seedream_payload['size']}")
    print(f"payloads_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
