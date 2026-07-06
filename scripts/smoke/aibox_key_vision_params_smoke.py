import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter

settings = load_settings(ROOT / "config.yaml")
node = settings.nodes["key_vision_image_generation"]
if node.model != "aibox:gpt-image-2-guan":
    raise SystemExit(f"nodes.key_vision_image_generation.model={node.model!r}, expected 'aibox:gpt-image-2-guan'")

expected_params = {
    "size": "3840x2160",
    "quality": "high",
}
for key, value in expected_params.items():
    actual = node.params.get(key)
    if actual != value:
        raise SystemExit(f"nodes.key_vision_image_generation.params.{key}={actual!r}, expected {value!r}")

provider = ProviderRouter(settings).image("key_vision", node_name="key_vision_image_generation")
metadata = provider._metadata({"node_name": "key_vision_image_generation", "asset_id": "smoke"})
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
