from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, TypeAlias

from PIL import Image, ImageColor, ImageDraw


NormalizedPoint: TypeAlias = tuple[float, float]
NormalizedBox: TypeAlias = tuple[float, float, float, float]
ColorValue: TypeAlias = str | tuple[int, int, int] | tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class BlockingControlBinding:
    """One anonymous subject placement on a pure spatial-control canvas.

    ``bbox_norm`` uses ``(x, y, width, height)`` in normalized canvas space.
    ``facing_vector`` is a screen-space direction vector. ``gaze_target_norm``
    is an absolute normalized canvas point. A larger ``depth`` value is drawn
    later and therefore represents a subject closer to the camera.

    ``binding_id`` is used only for deterministic ordering. It is never drawn.
    """

    binding_id: str
    color: ColorValue
    bbox_norm: NormalizedBox
    footpoint_norm: NormalizedPoint
    facing_vector: NormalizedPoint | None = None
    gaze_target_norm: NormalizedPoint | None = None
    depth: int = 0

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("binding_id must not be empty")
        _rgba(self.color)
        _validate_box(self.bbox_norm)
        _validate_point("footpoint_norm", self.footpoint_norm)
        if self.facing_vector is not None:
            _validate_vector("facing_vector", self.facing_vector)
        if self.gaze_target_norm is not None:
            _validate_point("gaze_target_norm", self.gaze_target_norm)
        if isinstance(self.depth, bool) or not isinstance(self.depth, int):
            raise ValueError("depth must be an integer")


def render_shot_blocking_control(
    background_size: tuple[int, int],
    bindings: Iterable[BlockingControlBinding],
    *,
    canvas_color: ColorValue = (16, 18, 24, 255),
) -> Image.Image:
    """Render anonymous blocking geometry without reading a background image.

    The returned RGBA image contains only a flat canvas, colored placement
    shapes, footpoints, facing arrows, and gaze arrows. It never renders text,
    binding IDs, role names, image numbers, or pixels from a scene image.
    """

    width, height = _validate_size(background_size)
    rows = tuple(bindings)
    binding_ids = [row.binding_id for row in rows]
    if len(binding_ids) != len(set(binding_ids)):
        raise ValueError("binding_id values must be unique")

    image = Image.new("RGBA", (width, height), _rgba(canvas_color))
    scale = max(1, int(round(min(width, height) / 360)))
    for binding in sorted(rows, key=lambda row: (row.depth, row.binding_id)):
        _draw_binding(image, binding, scale=scale)
    return image


def _draw_binding(
    image: Image.Image,
    binding: BlockingControlBinding,
    *,
    scale: int,
) -> None:
    width, height = image.size
    x, y, box_width, box_height = binding.bbox_norm
    left = int(round(x * width))
    top = int(round(y * height))
    right = int(round((x + box_width) * width))
    bottom = int(round((y + box_height) * height))
    right = max(left + 1, min(width - 1, right))
    bottom = max(top + 1, min(height - 1, bottom))

    color = _rgba(binding.color)
    opaque = (color[0], color[1], color[2], 255)
    translucent = (color[0], color[1], color[2], min(76, color[3]))
    outline_width = max(2, 3 * scale)
    box_pixel_width = max(1, right - left)
    box_pixel_height = max(1, bottom - top)
    corner_radius = max(2, min(box_pixel_width, box_pixel_height) // 20)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=corner_radius,
        fill=translucent,
    )
    image.alpha_composite(overlay)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=corner_radius,
        outline=opaque,
        width=outline_width,
    )

    center_x = (left + right) / 2
    head_y = top + box_pixel_height * 0.14
    head_radius = max(3 * scale, min(box_pixel_width * 0.16, box_pixel_height * 0.09))
    draw.ellipse(
        (
            int(round(center_x - head_radius)),
            int(round(head_y - head_radius)),
            int(round(center_x + head_radius)),
            int(round(head_y + head_radius)),
        ),
        fill=opaque,
    )

    shoulder_y = top + box_pixel_height * 0.30
    hip_y = top + box_pixel_height * 0.64
    half_shoulder = box_pixel_width * 0.28
    half_hip = box_pixel_width * 0.18
    draw.polygon(
        (
            (int(round(center_x - half_shoulder)), int(round(shoulder_y))),
            (int(round(center_x + half_shoulder)), int(round(shoulder_y))),
            (int(round(center_x + half_hip)), int(round(hip_y))),
            (int(round(center_x - half_hip)), int(round(hip_y))),
        ),
        fill=opaque,
    )

    footpoint = _pixel_point(binding.footpoint_norm, image.size)
    joint_width = max(2, 2 * scale)
    draw.line(
        (
            (int(round(center_x - half_hip * 0.55)), int(round(hip_y))),
            (footpoint[0] - max(2, box_pixel_width // 12), footpoint[1]),
        ),
        fill=opaque,
        width=joint_width,
    )
    draw.line(
        (
            (int(round(center_x + half_hip * 0.55)), int(round(hip_y))),
            (footpoint[0] + max(2, box_pixel_width // 12), footpoint[1]),
        ),
        fill=opaque,
        width=joint_width,
    )
    foot_radius = max(3, 5 * scale)
    draw.ellipse(
        (
            footpoint[0] - foot_radius,
            footpoint[1] - foot_radius,
            footpoint[0] + foot_radius,
            footpoint[1] + foot_radius,
        ),
        outline=opaque,
        width=joint_width,
    )

    if binding.facing_vector is not None:
        facing_start = (int(round(center_x)), int(round(top + box_pixel_height * 0.52)))
        direction_x, direction_y = _unit(binding.facing_vector)
        arrow_length = max(18 * scale, int(round(min(box_pixel_width, box_pixel_height) * 0.32)))
        facing_end = (
            int(round(facing_start[0] + direction_x * arrow_length)),
            int(round(facing_start[1] + direction_y * arrow_length)),
        )
        _draw_arrow(draw, facing_start, facing_end, fill=opaque, width=max(2, 3 * scale))

    if binding.gaze_target_norm is not None:
        gaze_start = (int(round(center_x)), int(round(head_y)))
        gaze_end = _pixel_point(binding.gaze_target_norm, image.size)
        _draw_dashed_arrow(
            draw,
            gaze_start,
            gaze_end,
            fill=opaque,
            width=max(1, 2 * scale),
            dash=max(4, 7 * scale),
            gap=max(3, 5 * scale),
        )


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    draw.line((start, end), fill=fill, width=width)
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length = math.hypot(delta_x, delta_y)
    if length < 1:
        return
    unit_x = delta_x / length
    unit_y = delta_y / length
    head_length = max(7, width * 4)
    wing = max(4, width * 2)
    base_x = end[0] - unit_x * head_length
    base_y = end[1] - unit_y * head_length
    perpendicular_x = -unit_y
    perpendicular_y = unit_x
    draw.polygon(
        (
            end,
            (
                int(round(base_x + perpendicular_x * wing)),
                int(round(base_y + perpendicular_y * wing)),
            ),
            (
                int(round(base_x - perpendicular_x * wing)),
                int(round(base_y - perpendicular_y * wing)),
            ),
        ),
        fill=fill,
    )


def _draw_dashed_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: tuple[int, int, int, int],
    width: int,
    dash: int,
    gap: int,
) -> None:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length = math.hypot(delta_x, delta_y)
    if length < 1:
        return
    unit_x = delta_x / length
    unit_y = delta_y / length
    cursor = 0.0
    while cursor < length:
        segment_end = min(length, cursor + dash)
        draw.line(
            (
                (
                    int(round(start[0] + unit_x * cursor)),
                    int(round(start[1] + unit_y * cursor)),
                ),
                (
                    int(round(start[0] + unit_x * segment_end)),
                    int(round(start[1] + unit_y * segment_end)),
                ),
            ),
            fill=fill,
            width=width,
        )
        cursor += dash + gap
    _draw_arrow(draw, (
        int(round(end[0] - unit_x * max(8, width * 5))),
        int(round(end[1] - unit_y * max(8, width * 5))),
    ), end, fill=fill, width=width)


def _validate_size(value: tuple[int, int]) -> tuple[int, int]:
    if len(value) != 2:
        raise ValueError("background_size must contain width and height")
    width, height = value
    if isinstance(width, bool) or isinstance(height, bool):
        raise ValueError("background_size must contain positive integers")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("background_size must contain positive integers")
    return width, height


def _validate_box(value: NormalizedBox) -> None:
    if len(value) != 4:
        raise ValueError("bbox_norm must contain x, y, width, and height")
    x, y, width, height = value
    if not all(math.isfinite(component) for component in value):
        raise ValueError("bbox_norm values must be finite")
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("bbox_norm must be a positive box inside normalized canvas space")


def _validate_point(name: str, value: NormalizedPoint) -> None:
    if len(value) != 2 or not all(math.isfinite(component) for component in value):
        raise ValueError(f"{name} must contain two finite values")
    if not all(0 <= component <= 1 for component in value):
        raise ValueError(f"{name} must be inside normalized canvas space")


def _validate_vector(name: str, value: NormalizedPoint) -> None:
    if len(value) != 2 or not all(math.isfinite(component) for component in value):
        raise ValueError(f"{name} must contain two finite values")
    if not all(-1 <= component <= 1 for component in value):
        raise ValueError(f"{name} components must be between -1 and 1")
    if math.hypot(*value) < 1e-9:
        raise ValueError(f"{name} must not be a zero vector")


def _unit(value: NormalizedPoint) -> tuple[float, float]:
    length = math.hypot(*value)
    return value[0] / length, value[1] / length


def _pixel_point(point: NormalizedPoint, size: tuple[int, int]) -> tuple[int, int]:
    return (
        min(size[0] - 1, int(round(point[0] * (size[0] - 1)))),
        min(size[1] - 1, int(round(point[1] * (size[1] - 1)))),
    )


def _rgba(value: ColorValue) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        return ImageColor.getcolor(value, "RGBA")
    if len(value) not in {3, 4}:
        raise ValueError("colors must contain RGB or RGBA channels")
    if any(isinstance(channel, bool) or not isinstance(channel, int) for channel in value):
        raise ValueError("color channels must be integers")
    if any(channel < 0 or channel > 255 for channel in value):
        raise ValueError("color channels must be between 0 and 255")
    if len(value) == 3:
        return value[0], value[1], value[2], 255
    return value


__all__ = ["BlockingControlBinding", "render_shot_blocking_control"]
