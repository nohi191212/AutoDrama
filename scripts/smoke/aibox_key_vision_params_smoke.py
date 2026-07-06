from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter

settings = load_settings(Path("config.yaml"))
node = settings.nodes["design_key_vision_image"]
if node.model != "aibox:gpt-image-2-guan":
    raise SystemExit(f"nodes.design_key_vision_image.model={node.model!r}, expected 'aibox:gpt-image-2-guan'")

expected_params = {
    "size": "3840x2160",
    "quality": "high",
}
for key, value in expected_params.items():
    actual = node.params.get(key)
    if actual != value:
        raise SystemExit(f"nodes.design_key_vision_image.params.{key}={actual!r}, expected {value!r}")

provider = ProviderRouter(settings).image("key_vision", node_name="design_key_vision_image")
metadata = provider._metadata({"node_name": "design_key_vision_image", "asset_id": "smoke"})
payload = provider.build_payload("smoke prompt", metadata=metadata)
if payload.get("model") != "gpt-image-2-guan":
    raise SystemExit(f"payload.model={payload.get('model')!r}, expected 'gpt-image-2-guan'")
if set(payload) != {"model", "prompt", "params"}:
    raise SystemExit(f"payload keys={sorted(payload)}, expected model/prompt/params only")

params = payload["params"]
if params != expected_params:
    raise SystemExit(f"payload.params={params!r}, expected {expected_params!r}")

forbidden = {"aspect_ratio", "resolution", "n", "response_format", "seed", "style", "background", "user"}
leaked = sorted(forbidden.intersection(params))
if leaked:
    raise SystemExit(f"payload.params leaked unsupported official fields: {leaked}")

print("ok: aibox key vision payload uses official gpt-image-2-guan fields only")
