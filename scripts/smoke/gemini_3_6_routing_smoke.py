"""Validate Gemini 3.6 bindings and the AI Box native multimodal payload shape."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings, load_settings
from autodrama.core.model_catalog import ModelCatalog, NodeModelSettings
from autodrama.providers.base import AssetRef
from autodrama.providers.google.text.gemini import GeminiTextProvider
from autodrama.providers.router import ProviderRouter


EXPECTED_MODEL = "aibox:gemini-3.6-flash"
MULTIMODAL_NODES = {
    "key_vision_prompt",
    "roleboard_prompt",
    "clip_prompt",
    "clip_storyboard_prompt",
    "clip_storyboard_prompt_audit",
    "clip_to_shots",
    "prop_prompt",
    "layout_prompt",
    "layout_to_background_prompt",
    "shot_keyframe_prompt",
    "role_subject_video_intro_text",
    "image_prompt_safety_rewrite",
    "layout_prop_boundary_review",
    "key_vision_image_audit",
    "roleboard_image_audit",
    "role_subject_frontal_image_audit",
    "prop_image_audit",
    "layout_image_audit",
    "shot_background_image_audit",
    "shot_keyframe_image_audit",
    "shot_video_audit",
    "postgen_source_audit",
    "postgen_edit_plan_generation",
    "postgen_final_audit",
}
CONFIG_FILES = [
    ROOT / "config.yaml",
    ROOT / "saodi.yaml",
    ROOT / "config.chonghui_jiuba.yaml",
    ROOT / "config.saodi_bashinian.yaml",
    ROOT / "config.saodi_bashinian_terra_image2.yaml",
    ROOT / "config.yushou_xianchao.yaml",
]


class SmokeSchema(BaseModel):
    ok: bool


def main() -> None:
    catalog = yaml.safe_load((ROOT / "model_catalog.yaml.example").read_text(encoding="utf-8"))
    if "aibox:gemini-3.6-flash" not in catalog.get("models", {}):
        raise AssertionError("model catalog is missing aibox:gemini-3.6-flash")
    model_catalog = ModelCatalog.from_mapping(catalog)

    for config_path in CONFIG_FILES:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        nodes = config.get("nodes", {}) or {}
        for node_name, raw_node in nodes.items():
            if raw_node.get("model") == EXPECTED_MODEL:
                model_catalog.validate_node_settings(node_name, NodeModelSettings.model_validate(raw_node))
        for node_name in MULTIMODAL_NODES:
            node = nodes.get(node_name)
            if node is not None and node.get("model") != EXPECTED_MODEL:
                raise AssertionError(
                    f"{config_path.name}: nodes.{node_name}.model={node.get('model')!r}; "
                    f"expected {EXPECTED_MODEL!r}"
                )
        aibox_models = ((config.get("providers", {}) or {}).get("aibox", {}) or {}).get("models", {}) or {}
        if aibox_models.get("text") != "gemini-3.6-flash":
            raise AssertionError(f"{config_path.name}: providers.aibox.models.text is not Gemini 3.6")
        routing = ((config.get("routing", {}) or {}).get("text", {}) or {})
        if routing.get("postgen_edit_plan") != "aibox":
            raise AssertionError(f"{config_path.name}: postgen_edit_plan is not routed to aibox")
        if config.get("generation", {}).get("visual_style") is not None:
            settings = load_settings(config_path)
            router = ProviderRouter(settings)
            for node_name in MULTIMODAL_NODES.intersection(settings.nodes):
                provider = router.text("shot", node_name=node_name)
                if getattr(provider, "model", "") != "gemini-3.6-flash":
                    raise AssertionError(
                        f"{config_path.name}: resolved {node_name} model={getattr(provider, 'model', None)!r}"
                    )

    smoke_media = ROOT / ".tmp" / "gemini_3_6_routing_smoke.mp4"
    smoke_media.parent.mkdir(parents=True, exist_ok=True)
    smoke_media.write_bytes(b"smoke video bytes")
    settings = ProviderSettings(
        base_url="https://api.lk888.ai",
        api_key_env="AIBOX_API_KEY",
        models={"text": "gemini-3.6-flash"},
        api_keys={"AIBOX_API_KEY": "smoke-key"},
    )
    provider = GeminiTextProvider(settings, RuntimeSettings(), provider_name="aibox")
    payload = provider.build_payload(
        "审阅视频并返回 JSON。",
        SmokeSchema,
        refs=[AssetRef(id="video", type="video", path=str(smoke_media))],
    )
    assert provider._endpoint() == "https://api.lk888.ai/v1beta/models/gemini-3.6-flash:generateContent"
    assert provider._headers()["Authorization"] == "Bearer smoke-key"
    assert provider._request_params() == {}
    video_part = payload["contents"][0]["parts"][1]["inline_data"]
    assert video_part["mime_type"] == "video/mp4"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    print("gemini_3_6_routing_smoke: ok")


if __name__ == "__main__":
    main()
