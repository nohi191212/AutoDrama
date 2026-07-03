from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.base import AssetRef
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


class NumericAnswerOutput(BaseModel):
    answer: int


class MixedImageJSONOutput(BaseModel):
    answer: int
    image_present: bool
    visible_details: list[str] = Field(min_length=2, max_length=5)


def set_stream(provider: RightCodeTextProvider, *, no_stream: bool) -> None:
    if no_stream:
        provider.stream = False


def assert_visual_details(details: list[str]) -> None:
    normalized = " ".join(details).lower()
    expected_groups = [
        ("眼镜", "glasses", "spectacles"),
        ("银发", "白发", "灰发", "silver hair", "white hair", "gray hair", "grey hair"),
        ("动漫", "二次元", "插画", "anime", "illustration", "portrait", "人物", "character"),
    ]
    matched = sum(1 for group in expected_groups if any(keyword in normalized for keyword in group))
    if matched < 2:
        raise AssertionError(f"visible_details do not look image-specific enough: {details!r}")


async def run_smoke(config: Path, *, image_path: Path, no_stream: bool) -> None:
    settings = load_settings(config)
    provider_settings = settings.providers["rightcode"]
    provider = RightCodeTextProvider(provider_settings, settings.runtime, model_key="script")
    set_stream(provider, no_stream=no_stream)

    print(f"provider={provider.name} model={provider.model} stream={provider.stream}")

    text_output = await provider.generate_json(
        '1+1 等于几？只输出 JSON，格式必须是 {"answer": 2}，answer 必须是数字。',
        NumericAnswerOutput,
        temperature=0,
        metadata={
            "node_name": "rightcode_numeric_json_smoke",
            "project_id": "rightcode_json_multimodal_smoke",
        },
    )
    if text_output.answer != 2:
        raise AssertionError(f"unexpected numeric answer: {text_output.answer!r}")

    resolved_image_path = image_path.resolve()
    if not resolved_image_path.exists():
        raise FileNotFoundError(resolved_image_path)

    image_output = await provider.generate_json(
        (
            "文本问题：1+1 等于几？同时观察随附图片。\n"
            "只输出 JSON：answer 必须是数字 2；image_present 表示是否确实收到图片；"
            "visible_details 写 2 到 5 条你从图片中直接看到的具体视觉细节，"
            "例如发色、配饰、画面风格或构图。不要输出 Markdown。"
        ),
        MixedImageJSONOutput,
        temperature=0,
        metadata={
            "node_name": "rightcode_mixed_image_json_smoke",
            "project_id": "rightcode_json_multimodal_smoke",
        },
        refs=[
            AssetRef(
                id="sample_urban_fantasy",
                type="image",
                path=str(resolved_image_path),
            )
        ],
    )
    if image_output.answer != 2:
        raise AssertionError(f"unexpected mixed answer: {image_output.answer!r}")
    if not image_output.image_present:
        raise AssertionError("image_present is false")
    assert_visual_details(image_output.visible_details)

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    result = {
        "text_json": text_output.model_dump(mode="json"),
        "image_text_json": image_output.model_dump(mode="json"),
    }
    (tmp_dir / "rightcode_json_multimodal_smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("rightcode_json_multimodal_smoke: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    parser.add_argument(
        "--image",
        default=".assets/sample_urban_fantasy/17384532767395200.jpg",
    )
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        run_smoke(
            Path(args.config),
            image_path=Path(args.image),
            no_stream=args.no_stream,
        )
    )


if __name__ == "__main__":
    main()
