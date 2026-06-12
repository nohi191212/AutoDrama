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
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Seedream image payload without submitting a request.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml.example"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    provider = VolcengineSeedreamImageProvider(settings.providers["volcengine"], settings.runtime)

    if not getattr(provider, "supports_reference_images", False):
        raise AssertionError("Seedream provider should support reference images")

    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "seedream_payload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_dir / "reference.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )

    payload = provider.build_payload(
        "生成 9:16 竖屏短剧故事板图像，保持角色与参考图一致。",
        refs=[AssetRef(id="reference", type="image", path=str(image_path))],
    )
    url_ref_payload = provider.build_payload(
        "生成破损状态道具，保持与参考图同一主体。",
        refs=[
            AssetRef(
                id="reference",
                type="image",
                path=str(image_path),
                url="https://example.invalid/generated-reference.png",
            )
        ],
        metadata={"node_name": "prop_generation"},
    )
    output_path = tmp_dir / "payload.json"
    output_path.write_text(
        json.dumps(
            {
                "generic": {**payload, "image": "<base64 data URL omitted>"},
                "prop_with_url_ref": url_ref_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    volcengine_settings = settings.providers["volcengine"]
    if payload["model"] != volcengine_settings.models["seedream_5"]:
        raise AssertionError(f"Unexpected model: {payload['model']}")
    if payload["size"] != "1600x2848":
        raise AssertionError(f"Unexpected size: {payload['size']}")
    if payload["output_format"] != "png":
        raise AssertionError(f"Unexpected output_format: {payload['output_format']}")
    if payload["response_format"] != "url":
        raise AssertionError(f"Unexpected response_format: {payload['response_format']}")
    if payload["sequential_image_generation"] != "disabled":
        raise AssertionError(f"Unexpected sequential_image_generation: {payload['sequential_image_generation']}")
    if payload["image"].startswith("data:image/png;base64,") is False:
        raise AssertionError("Reference image was not encoded as a png data URL")
    if url_ref_payload["image"] != "https://example.invalid/generated-reference.png":
        raise AssertionError(f"Reference URL was not preferred over local path: {url_ref_payload['image']}")
    if url_ref_payload["size"] != volcengine_settings.options["seedream_prop_size"]:
        raise AssertionError(f"Unexpected prop size: {url_ref_payload['size']}")

    print("seedream_payload_smoke=ok")
    print(f"payload_path={output_path}")
    print(f"provider={provider.name} model={payload['model']} size={payload['size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
