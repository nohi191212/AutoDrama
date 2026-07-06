from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter

settings = load_settings(Path("config.yaml"))
node = settings.nodes["design_key_vision_image"]
expected = {
    "size": "3840x2160",
    "aspect_ratio": "16:9",
    "resolution": "4K",
    "quality": "high",
}
for key, value in expected.items():
    actual = node.params.get(key)
    if actual != value:
        raise SystemExit(f"nodes.design_key_vision_image.params.{key}={actual!r}, expected {value!r}")

provider = ProviderRouter(settings).image("key_vision", node_name="design_key_vision_image")
metadata = provider._metadata({"node_name": "design_key_vision_image", "asset_id": "smoke"})
payload = provider.build_payload("smoke prompt", metadata=metadata)
params = payload["params"]
for key, value in expected.items():
    actual = params.get(key)
    if actual != value:
        raise SystemExit(f"payload.params.{key}={actual!r}, expected {value!r}")
print("ok: aibox key vision payload includes size/aspect_ratio/resolution/quality")
