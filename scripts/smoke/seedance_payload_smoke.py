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
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Seedance payload without submitting a task.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml.example"))
    parser.add_argument("--ratio")
    parser.add_argument("--duration", type=float, default=6)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    provider = VolcengineSeedanceVideoProvider(settings.providers["volcengine"], settings.runtime)
    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "seedance_payload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_dir / "reference.png"
    image_path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="))
    video_path = tmp_dir / "reference.mp4"
    video_path.write_bytes(b"fake local anchor video")

    metadata = {"ratio": args.ratio} if args.ratio else None
    payload = provider.build_payload(
        "测试 Seedance 2.0 视频生成 payload。镜头缓慢推进，人物保持一致。",
        refs=[
            AssetRef(id="reference", type="image", path=str(image_path)),
            AssetRef(id="anchor_video", type="video", path=str(video_path)),
        ],
        duration=args.duration,
        metadata=metadata,
    )
    output_path = tmp_dir / "payload.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    assert payload["model"] == settings.providers["volcengine"].models["seedance_2"]
    assert payload["ratio"] == (args.ratio or settings.providers["volcengine"].options["video_ratio"])
    assert payload["duration"] == max(4, min(15, round(args.duration)))
    assert payload["content"][0]["type"] == "text"
    assert payload["content"][1]["type"] == "image_url"
    assert payload["content"][1]["role"] == "reference_image"
    assert payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert payload["content"][2]["type"] == "video_url"
    assert payload["content"][2]["role"] == "reference_video"
    assert payload["content"][2]["video_url"]["url"].startswith("data:video/mp4;base64,")

    first_frame_payload = provider.build_payload(
        "测试 Seedance 2.0 首帧模式。以上一镜头尾帧作为本镜头第一帧，从既有姿态继续动作。",
        refs=[
            AssetRef(
                id="previous_last_frame",
                type="image",
                path=str(image_path),
                metadata={"seedance_role": "first_frame"},
            ),
            AssetRef(id="unused_reference", type="image", path=str(image_path)),
        ],
        duration=args.duration,
        metadata=metadata,
    )
    image_items = [item for item in first_frame_payload["content"] if item["type"] == "image_url"]
    assert len(image_items) == 1
    assert image_items[0]["role"] == "first_frame"
    assert image_items[0]["image_url"]["url"].startswith("data:image/png;base64,")

    print("seedance_payload_smoke=ok")
    print(f"payload_path={output_path}")
    print(f"model={payload['model']} ratio={payload['ratio']} duration={payload['duration']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
