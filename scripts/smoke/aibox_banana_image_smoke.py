from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.aibox.image.banana import AiboxBananaImageProvider  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def main() -> int:
    settings = load_settings(ROOT / "saodi.yaml")
    provider = AiboxBananaImageProvider(
        settings.providers["aibox"],
        settings.runtime,
        reference_uploader_settings=settings.providers.get("toapi"),
    )
    payload = provider.build_payload(
        "Preserve the supplied world-anchor image and clean only rendering artifacts.",
        refs=[AssetRef(id="baseline", type="image", url="https://example.invalid/baseline.png")],
        metadata={
            "model": "gemini-3-pro-image-preview",
            "aspectRatio": "16:9",
            "imageSize": "4K",
        },
    )
    assert payload["model"] == "gemini-3-pro-image-preview"
    assert payload["params"]["aspectRatio"] == "16:9"
    assert payload["params"]["imageSize"] == "4K"
    assert payload["params"]["images"] == ["https://example.invalid/baseline.png"]
    assert provider.generation_endpoint == "https://api.lk888.ai/v1/media/generate"
    assert provider.status_endpoint == "https://api.lk888.ai/v1/media/status"

    router = ProviderRouter(settings)
    bound_provider = router.image("shot", node_name="shot_keyframe_image_generation")
    assert bound_provider.model == "gemini-3-pro-image-preview"
    assert isinstance(bound_provider._provider, AiboxBananaImageProvider)
    catalog = settings.model_catalog.require("aibox:gemini-3-pro-image-preview")
    assert catalog.family == "gemini-image"
    assert catalog.capability == "image"
    print("aibox banana image smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
