from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from autodrama.workflows.shot_blocking_control import (
    BlockingControlBinding,
    render_shot_blocking_control,
)


ROOT = Path(__file__).resolve().parents[2]
SMOKE_ROOT = ROOT / ".tmp" / "shot_blocking_control_smoke"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _expect_value_error(callback) -> None:
    try:
        callback()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def main() -> None:
    size = (640, 360)
    canvas_color = (11, 13, 19, 255)
    background_binding = BlockingControlBinding(
        binding_id="叶凡 Image 2",
        color=(224, 72, 92),
        bbox_norm=(0.18, 0.16, 0.50, 0.72),
        footpoint_norm=(0.43, 0.88),
        facing_vector=(1.0, 0.0),
        gaze_target_norm=(0.72, 0.34),
        depth=0,
    )
    foreground_binding = BlockingControlBinding(
        binding_id="李德海 Image 3",
        color="#42D5A3",
        bbox_norm=(0.40, 0.28, 0.36, 0.60),
        footpoint_norm=(0.58, 0.88),
        facing_vector=(-0.8, -0.2),
        gaze_target_norm=(0.31, 0.30),
        depth=5,
    )

    control = render_shot_blocking_control(
        size,
        [foreground_binding, background_binding],
        canvas_color=canvas_color,
    )
    assert control.mode == "RGBA"
    assert control.size == size
    assert control.info == {}
    assert control.getpixel((0, 0)) == canvas_color

    reordered = render_shot_blocking_control(
        size,
        [background_binding, foreground_binding],
        canvas_color=canvas_color,
    )
    assert control.tobytes() == reordered.tobytes(), "input order must not affect output"

    front_left = int(round(foreground_binding.bbox_norm[0] * size[0]))
    front_middle_y = int(
        round(
            (foreground_binding.bbox_norm[1] + foreground_binding.bbox_norm[3] * 0.48)
            * size[1]
        )
    )
    assert control.getpixel((front_left, front_middle_y))[:3] == (66, 213, 163)

    no_directions = render_shot_blocking_control(
        size,
        [
            BlockingControlBinding(
                binding_id=foreground_binding.binding_id,
                color=foreground_binding.color,
                bbox_norm=foreground_binding.bbox_norm,
                footpoint_norm=foreground_binding.footpoint_norm,
                depth=foreground_binding.depth,
            )
        ],
        canvas_color=canvas_color,
    )
    with_directions = render_shot_blocking_control(
        size,
        [foreground_binding],
        canvas_color=canvas_color,
    )
    assert no_directions.tobytes() != with_directions.tobytes()

    png = _png_bytes(control)
    assert "叶凡".encode("utf-8") not in png
    assert "李德海".encode("utf-8") not in png
    assert b"Image 2" not in png
    assert b"Image 3" not in png

    _expect_value_error(
        lambda: BlockingControlBinding(
            binding_id="outside",
            color=(255, 255, 255),
            bbox_norm=(0.9, 0.1, 0.2, 0.4),
            footpoint_norm=(0.9, 0.5),
        )
    )
    _expect_value_error(
        lambda: render_shot_blocking_control(
            size,
            [background_binding, background_binding],
            canvas_color=canvas_color,
        )
    )

    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = SMOKE_ROOT / "control.png"
    output_path.write_bytes(png)
    with Image.open(output_path) as reopened:
        reopened.load()
        assert reopened.size == size
        assert reopened.mode == "RGBA"

    print(
        json.dumps(
            {
                "output_path": str(output_path),
                "size": list(size),
                "binding_count": 2,
                "pure_control_canvas": True,
                "deterministic_depth_order": True,
                "text_free": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
