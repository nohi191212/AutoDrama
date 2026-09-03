from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


SCENE_MULTIVIEW_CROP_VERSION = 2
SCENE_MULTIVIEW_COLUMNS = 2
SCENE_MULTIVIEW_ROWS = 2
SCENE_MULTIVIEW_ASPECT_WIDTH = 16
SCENE_MULTIVIEW_ASPECT_HEIGHT = 9
SCENE_MULTIVIEW_DEFAULT_SEAM_GUARD = 2


@dataclass(frozen=True, slots=True)
class SceneMultiviewCrop:
    view_index: int
    row: int
    column: int
    asset_path: Path
    crop_box: tuple[int, int, int, int]
    width: int
    height: int
    content_fingerprint: str


@dataclass(frozen=True, slots=True)
class SceneMultiviewCropResult:
    source_path: Path
    source_width: int
    source_height: int
    split_x: int
    split_y: int
    seam_guard: int
    view_width: int
    view_height: int
    content_fingerprint: str
    views: tuple[SceneMultiviewCrop, ...]


def crop_scene_multiview_board(
    source_path: Path | str,
    output_dir: Path | str,
    *,
    output_stem: str | None = None,
    min_view_width: int = 320,
    min_view_height: int = 180,
    max_trim_fraction: float = 0.05,
    seam_guard: int = SCENE_MULTIVIEW_DEFAULT_SEAM_GUARD,
) -> SceneMultiviewCropResult:
    """Crop a seamless 2x2 scene board into four uniform, exact 16:9 PNG views.

    The split is derived from the decoded image dimensions.  If the source is
    only approximately 16:9, the largest common exact-16:9 rectangle is centered
    inside every quadrant.  ``seam_guard`` excludes pixels on both sides of the
    internal cross so separator artifacts cannot enter any crop.  No resampling
    is performed.
    """

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Scene multiview board is missing: {source}")
    destination = Path(output_dir).expanduser().resolve()
    stem = output_stem or source.stem
    if not stem or Path(stem).name != stem or stem in {".", ".."}:
        raise ValueError(f"output_stem must be one safe filename component: {stem!r}")
    if min_view_width < SCENE_MULTIVIEW_ASPECT_WIDTH:
        raise ValueError("min_view_width must be at least 16")
    if min_view_height < SCENE_MULTIVIEW_ASPECT_HEIGHT:
        raise ValueError("min_view_height must be at least 9")
    if not 0 <= max_trim_fraction < 1:
        raise ValueError("max_trim_fraction must be in [0, 1)")
    if isinstance(seam_guard, bool) or not isinstance(seam_guard, int) or seam_guard < 0:
        raise ValueError("seam_guard must be a non-negative integer pixel count")

    with Image.open(source) as opened:
        opened.load()
        transposed = ImageOps.exif_transpose(opened)
        image = transposed.convert("RGBA" if "A" in transposed.getbands() else "RGB")

    width, height = image.size
    split_x = width // SCENE_MULTIVIEW_COLUMNS
    split_y = height // SCENE_MULTIVIEW_ROWS
    quadrants = (
        (0, 0, split_x, split_y),
        (split_x, 0, width, split_y),
        (0, split_y, split_x, height),
        (split_x, split_y, width, height),
    )
    if split_x <= 0 or split_y <= 0:
        raise ValueError(f"Scene multiview board is too small to split 2x2: {width}x{height}")
    safe_quadrants = (
        (0, 0, split_x - seam_guard, split_y - seam_guard),
        (split_x + seam_guard, 0, width, split_y - seam_guard),
        (0, split_y + seam_guard, split_x - seam_guard, height),
        (split_x + seam_guard, split_y + seam_guard, width, height),
    )
    if any(left >= right or top >= bottom for left, top, right, bottom in safe_quadrants):
        raise ValueError(
            "seam_guard leaves no usable pixels in one or more scene multiview quadrants: "
            f"source={width}x{height}, seam_guard={seam_guard}"
        )

    units = min(
        min((right - left) // SCENE_MULTIVIEW_ASPECT_WIDTH, (bottom - top) // SCENE_MULTIVIEW_ASPECT_HEIGHT)
        for left, top, right, bottom in safe_quadrants
    )
    view_width = units * SCENE_MULTIVIEW_ASPECT_WIDTH
    view_height = units * SCENE_MULTIVIEW_ASPECT_HEIGHT
    if view_width < min_view_width or view_height < min_view_height:
        raise ValueError(
            "Scene multiview board cannot provide four sufficiently large 16:9 views: "
            f"source={width}x{height}, crop={view_width}x{view_height}, "
            f"minimum={min_view_width}x{min_view_height}"
        )

    crop_boxes: list[tuple[int, int, int, int]] = []
    for quadrant, safe_quadrant in zip(quadrants, safe_quadrants):
        quadrant_left, quadrant_top, quadrant_right, quadrant_bottom = quadrant
        left, top, right, bottom = safe_quadrant
        safe_width = right - left
        safe_height = bottom - top
        retained_fraction = (view_width * view_height) / (
            (quadrant_right - quadrant_left) * (quadrant_bottom - quadrant_top)
        )
        trim_fraction = 1 - retained_fraction
        if trim_fraction > max_trim_fraction:
            raise ValueError(
                "Scene multiview board quadrants are too far from 16:9 for lossless-style cropping: "
                f"source={width}x{height}, "
                f"quadrant={quadrant_right - quadrant_left}x{quadrant_bottom - quadrant_top}, "
                f"seam_guard={seam_guard}, "
                f"trim_fraction={trim_fraction:.6f}, maximum={max_trim_fraction:.6f}"
            )
        crop_left = left + (safe_width - view_width) // 2
        crop_top = top + (safe_height - view_height) // 2
        crop_boxes.append((crop_left, crop_top, crop_left + view_width, crop_top + view_height))

    _validate_crop_boxes(
        crop_boxes,
        quadrants=safe_quadrants,
        source_width=width,
        source_height=height,
        view_width=view_width,
        view_height=view_height,
    )

    destination.mkdir(parents=True, exist_ok=True)
    crops: list[SceneMultiviewCrop] = []
    for index, crop_box in enumerate(crop_boxes, start=1):
        tile = image.crop(crop_box)
        if tile.size != (view_width, view_height):
            raise ValueError(
                f"Unexpected crop size for scene view {index}: {tile.size} != {(view_width, view_height)}"
            )
        fingerprint = _image_content_fingerprint(tile)
        asset_path = destination / f"{stem}_view_{index:02d}.png"
        _atomic_save_png(tile, asset_path)
        crops.append(
            SceneMultiviewCrop(
                view_index=index,
                row=(index - 1) // SCENE_MULTIVIEW_COLUMNS,
                column=(index - 1) % SCENE_MULTIVIEW_COLUMNS,
                asset_path=asset_path,
                crop_box=crop_box,
                width=view_width,
                height=view_height,
                content_fingerprint=fingerprint,
            )
        )

    combined_payload = {
        "version": SCENE_MULTIVIEW_CROP_VERSION,
        "source_size": [width, height],
        "split": [split_x, split_y],
        "seam_guard": seam_guard,
        "views": [
            {
                "index": item.view_index,
                "crop_box": list(item.crop_box),
                "size": [item.width, item.height],
                "content_fingerprint": item.content_fingerprint,
            }
            for item in crops
        ],
    }
    content_fingerprint = hashlib.sha256(
        json.dumps(combined_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return SceneMultiviewCropResult(
        source_path=source,
        source_width=width,
        source_height=height,
        split_x=split_x,
        split_y=split_y,
        seam_guard=seam_guard,
        view_width=view_width,
        view_height=view_height,
        content_fingerprint=content_fingerprint,
        views=tuple(crops),
    )


def _validate_crop_boxes(
    crop_boxes: list[tuple[int, int, int, int]],
    *,
    quadrants: tuple[tuple[int, int, int, int], ...],
    source_width: int,
    source_height: int,
    view_width: int,
    view_height: int,
) -> None:
    if len(crop_boxes) != SCENE_MULTIVIEW_COLUMNS * SCENE_MULTIVIEW_ROWS:
        raise ValueError(f"Expected four crop boxes, got {len(crop_boxes)}")
    if view_width * SCENE_MULTIVIEW_ASPECT_HEIGHT != view_height * SCENE_MULTIVIEW_ASPECT_WIDTH:
        raise ValueError(f"Scene view crop is not exactly 16:9: {view_width}x{view_height}")
    for index, (crop_box, quadrant) in enumerate(zip(crop_boxes, quadrants), start=1):
        left, top, right, bottom = crop_box
        quadrant_left, quadrant_top, quadrant_right, quadrant_bottom = quadrant
        if not (0 <= left < right <= source_width and 0 <= top < bottom <= source_height):
            raise ValueError(f"Scene view {index} crop is outside source bounds: {crop_box}")
        if not (
            quadrant_left <= left < right <= quadrant_right
            and quadrant_top <= top < bottom <= quadrant_bottom
        ):
            raise ValueError(f"Scene view {index} crosses its quadrant boundary: {crop_box}")
    for index, first in enumerate(crop_boxes):
        for second in crop_boxes[index + 1 :]:
            if _boxes_overlap(first, second):
                raise ValueError(f"Scene view crop boxes overlap: {first} and {second}")


def _boxes_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _image_content_fingerprint(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(image.width).encode("ascii"))
    digest.update(b"x")
    digest.update(str(image.height).encode("ascii"))
    digest.update(b"\0")
    digest.update(image.tobytes())
    return digest.hexdigest()


def _atomic_save_png(image: Image.Image, path: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        image.save(temporary_path, format="PNG")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
