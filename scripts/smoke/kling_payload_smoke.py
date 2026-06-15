from __future__ import annotations

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
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402


ONE_PIXEL_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    provider = KlingOmniVideoProvider(settings.providers["kling"], settings.runtime)
    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "kling_payload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    storyboard_path = tmp_dir / "storyboard_panel.png"
    storyboard_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG))

    refs = [
        AssetRef(
            id="storyboard_panel",
            type="image",
            path=str(storyboard_path),
            metadata={"asset_type": "storyboard_panel"},
        ),
        AssetRef(
            id="key_vision",
            type="image",
            url="https://example.invalid/key-vision.png",
            metadata={"asset_type": "key_vision"},
        ),
        AssetRef(
            id="role_linz_subject",
            type="element",
            metadata={
                "asset_type": "role_subject_element",
                "role_id": "role_linz",
                "role_name": "林舟",
                "element_id": "123456789",
            },
        ),
    ]
    prompt = (
        "让 <<<element_1>>> 按照 <<<image_1>>> 的构图和动作完成当前镜头，"
        "整体写实质感参考 <<<image_2>>>。"
    )
    payload = provider.build_payload(prompt, refs=refs, duration=5)
    payload_path = tmp_dir / "omni_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    require(payload["model_name"] == "kling-v3-omni", json.dumps(payload, ensure_ascii=False))
    require(payload["mode"] == "pro", json.dumps(payload, ensure_ascii=False))
    require(payload["aspect_ratio"] == "9:16", json.dumps(payload, ensure_ascii=False))
    require(payload["duration"] == "5", json.dumps(payload, ensure_ascii=False))
    require(payload["sound"] == "off", json.dumps(payload, ensure_ascii=False))
    require(payload["watermark_info"] == {"enabled": False}, json.dumps(payload, ensure_ascii=False))
    require(len(payload["image_list"]) == 2, json.dumps(payload, ensure_ascii=False))
    require(payload["image_list"][0]["image_url"] == ONE_PIXEL_PNG, json.dumps(payload, ensure_ascii=False))
    require(payload["image_list"][1]["image_url"] == "https://example.invalid/key-vision.png", json.dumps(payload, ensure_ascii=False))
    require(payload["element_list"] == [{"element_id": 123456789}], json.dumps(payload, ensure_ascii=False))
    require("<<<element_1>>>" in payload["prompt"], payload["prompt"])
    require("<<<image_1>>>" in payload["prompt"], payload["prompt"])
    require("<<<image_2>>>" in payload["prompt"], payload["prompt"])
    voiced_payload = provider.build_payload(
        "角色面向镜头说出一句自我介绍。",
        refs=refs[:1],
        duration=5,
        metadata={"sound": "off", "parameters": {"sound": "on"}},
    )
    require(voiced_payload["sound"] == "on", json.dumps(voiced_payload, ensure_ascii=False))

    subject_payload = provider.build_subject_element_payload(
        element_name="林舟",
        element_description="写实人形男主，短发，深灰职场衬衫，疲惫但克制。",
        reference_type="video_refer",
        video_url="https://example.invalid/linzhou-subject.mp4",
        metadata={"external_task_id": "demo_role_linz_subject"},
    )
    subject_path = tmp_dir / "subject_payload.json"
    subject_path.write_text(json.dumps(subject_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    require(subject_payload["reference_type"] == "video_refer", json.dumps(subject_payload, ensure_ascii=False))
    require(
        subject_payload["element_video_list"]["refer_videos"][0]["video_url"]
        == "https://example.invalid/linzhou-subject.mp4",
        json.dumps(subject_payload, ensure_ascii=False),
    )
    require(subject_payload["tag_list"] == [{"tag_id": "o_102"}], json.dumps(subject_payload, ensure_ascii=False))
    nested_video_result = KlingOmniVideoProvider._video_result(
        {
            "code": 0,
            "data": {
                "task_id": "task_nested_video",
                "task_status": "succeed",
                "task_result": {
                    "videos": [
                        {
                            "id": "video_001",
                            "url": "https://example.invalid/generated.mp4",
                            "duration": "5.041",
                        }
                    ]
                },
            },
        },
        fallback_model="kling-v3-omni",
    )
    require(nested_video_result.video_url == "https://example.invalid/generated.mp4", str(nested_video_result))

    print("kling_payload_smoke=ok")
    print(f"omni_payload_path={payload_path}")
    print(f"subject_payload_path={subject_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
