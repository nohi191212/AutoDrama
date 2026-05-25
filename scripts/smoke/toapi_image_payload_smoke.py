from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings, load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = ProviderSettings(
        base_url="https://toapis.com",
        api_key_env="sk-smoke-test",
        models={
            "image": "gpt-image-2",
            "role_design": "gpt-image-2",
            "role_full_body": "gpt-image-full-body",
            "role_multiview": "gpt-image-multiview",
        },
        options={
            "resolution": "4K",
            "size": "16:9",
            "role_design_size": "16:9",
            "role_design_resolution": "4K",
            "role_full_body_size": "1:2",
            "role_full_body_resolution": "2K",
            "role_multiview_size": "16:9",
            "role_multiview_resolution": "4K",
            "prop_size": "1:1",
            "prop_resolution": "2K",
            "layout_size": "16:9",
            "layout_resolution": "4K",
            "ref_frame_size": "16:9",
            "ref_frame_resolution": "4K",
            "n": 1,
        },
    )
    provider = ToAPIImageProvider(settings, RuntimeSettings())

    full_body_payload = provider.build_payload(
        "full body prompt",
        metadata={"node_name": "role_full_body_generation"},
    )
    require(full_body_payload["model"] == "gpt-image-full-body", f"Unexpected full-body model: {full_body_payload['model']}")
    require(full_body_payload["size"] == "1:2", f"Unexpected full-body size: {full_body_payload['size']}")
    require(full_body_payload["resolution"] == "2K", f"Unexpected full-body resolution: {full_body_payload['resolution']}")

    multiview_payload = provider.build_payload(
        "multiview prompt",
        refs=[AssetRef(id="full_body", type="image", url="https://example.invalid/full-body.png")],
        metadata={"node_name": "role_multiview_generation"},
    )
    require(multiview_payload["model"] == "gpt-image-multiview", f"Unexpected multiview model: {multiview_payload['model']}")
    require(multiview_payload["size"] == "16:9", f"Unexpected multiview size: {multiview_payload['size']}")
    require(multiview_payload["resolution"] == "4K", f"Unexpected multiview resolution: {multiview_payload['resolution']}")
    require(
        multiview_payload.get("reference_images") == ["https://example.invalid/full-body.png"],
        f"Unexpected multiview refs: {multiview_payload.get('reference_images')}",
    )

    prop_payload = provider.build_payload(
        "prop prompt",
        metadata={"node_name": "prop_generation"},
    )
    require(prop_payload["size"] == "1:1", f"Unexpected prop size: {prop_payload['size']}")
    require(prop_payload["resolution"] == "2K", f"Unexpected prop resolution: {prop_payload['resolution']}")

    layout_payload = provider.build_payload(
        "layout prompt",
        metadata={"node_name": "layout_image_generation"},
    )
    require(layout_payload["size"] == "16:9", f"Unexpected layout size: {layout_payload['size']}")
    require(layout_payload["resolution"] == "4K", f"Unexpected layout resolution: {layout_payload['resolution']}")

    ref_frame_payload = provider.build_payload(
        "ref frame prompt",
        refs=[AssetRef(id="reference", type="image", url="https://example.invalid/reference.png")],
        metadata={"node_name": "ref_frame_generation"},
    )
    require(ref_frame_payload["size"] == "16:9", f"Unexpected ref frame size: {ref_frame_payload['size']}")
    require(ref_frame_payload["resolution"] == "4K", f"Unexpected ref frame resolution: {ref_frame_payload['resolution']}")

    app_settings = load_settings(ROOT_DIR / "config.yaml.example")
    for purpose in ("role", "prop", "layout", "ref_frame"):
        routed_provider = ProviderRouter(app_settings).image(purpose)
        require(
            isinstance(routed_provider, ToAPIImageProvider),
            f"Expected ToAPI image provider for image.{purpose}; got {type(routed_provider)}",
        )

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "toapi_image_payload"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "payloads.json"
    output_path.write_text(
        json.dumps(
            {
                "full_body": full_body_payload,
                "multiview": multiview_payload,
                "prop": prop_payload,
                "layout": layout_payload,
                "ref_frame": ref_frame_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("toapi_image_payload_smoke=ok")
    print(f"full_body={full_body_payload['model']} {full_body_payload['size']} {full_body_payload['resolution']}")
    print(f"multiview={multiview_payload['model']} {multiview_payload['size']} {multiview_payload['resolution']}")
    print(f"prop={prop_payload['size']} {prop_payload['resolution']}")
    print(f"layout={layout_payload['size']} {layout_payload['resolution']}")
    print(f"ref_frame={ref_frame_payload['size']} {ref_frame_payload['resolution']}")
    print(f"payloads_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
