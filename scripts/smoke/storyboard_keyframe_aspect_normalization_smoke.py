from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardKeyframeGenerationNode  # noqa: E402


def main() -> None:
    project_dir = ROOT / ".tmp" / "storyboard_keyframe_aspect_normalization_smoke"
    image_path = project_dir / "assets" / "images" / "storyboard_keyframes" / "square_keyframe.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), (32, 64, 96)).save(image_path)

    provider = SimpleNamespace(
        model_binding=SimpleNamespace(params={"size": "9:16", "resolution": "2K"}),
        _normalize_size=lambda size, resolution: "1088x1920",
    )
    node = object.__new__(StoryboardKeyframeGenerationNode)
    node.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)

    metadata = node._normalize_keyframe_image_file(
        project_dir,
        "assets/images/storyboard_keyframes/square_keyframe.png",
        provider=provider,
    )
    if not metadata.get("keyframe_aspect_normalization", {}).get("applied"):
        raise AssertionError(f"keyframe aspect normalization was not applied: {metadata}")
    with Image.open(image_path) as image:
        if image.size != (1088, 1920):
            raise AssertionError(f"normalized keyframe size mismatch: {image.size}")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_keyframe_aspect_normalization_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_keyframe_aspect_normalization_smoke: ok")


if __name__ == "__main__":
    main()
