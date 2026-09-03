from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.workflows.scene_multiview import crop_scene_multiview_board  # noqa: E402


COLORS = (
    (220, 30, 45),
    (25, 180, 80),
    (35, 90, 220),
    (235, 190, 25),
)


def _write_board(
    path: Path,
    *,
    width: int,
    height: int,
    marker: bool = False,
    seam: bool = False,
) -> None:
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    split_x = width // 2
    split_y = height // 2
    boxes = (
        (0, 0, split_x - 1, split_y - 1),
        (split_x, 0, width - 1, split_y - 1),
        (0, split_y, split_x - 1, height - 1),
        (split_x, split_y, width - 1, height - 1),
    )
    for color, box in zip(COLORS, boxes):
        draw.rectangle(box, fill=color)
    if seam:
        draw.rectangle((split_x - 2, 0, split_x + 1, height - 1), fill=(0, 0, 0))
        draw.rectangle((0, split_y - 2, width - 1, split_y + 1), fill=(0, 0, 0))
    if marker:
        image.putpixel((10, 10), (255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _assert_view_colors(result: object) -> None:
    views = getattr(result, "views")
    for expected, view in zip(COLORS, views):
        with Image.open(view.asset_path) as image:
            center = image.getpixel((image.width // 2, image.height // 2))
            assert center == expected, (view.view_index, center, expected)


def run(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    exact_board = output_dir / "inputs" / "exact_2x2.png"
    _write_board(exact_board, width=1280, height=720, seam=True)
    exact = crop_scene_multiview_board(exact_board, output_dir / "exact", output_stem="exact")
    assert len(exact.views) == 4
    assert (exact.split_x, exact.split_y) == (640, 360)
    assert exact.seam_guard == 2
    assert (exact.view_width, exact.view_height) == (624, 351)
    assert all(view.width * 9 == view.height * 16 for view in exact.views)
    assert [view.crop_box for view in exact.views] == [
        (7, 3, 631, 354),
        (649, 3, 1273, 354),
        (7, 365, 631, 716),
        (649, 365, 1273, 716),
    ]
    _assert_view_colors(exact)
    for view in exact.views:
        with Image.open(view.asset_path) as image:
            assert (0, 0, 0) not in set(image.getdata())

    repeated = crop_scene_multiview_board(
        exact_board,
        output_dir / "exact_repeat",
        output_stem="same_content_different_paths",
    )
    assert repeated.content_fingerprint == exact.content_fingerprint
    assert [view.content_fingerprint for view in repeated.views] == [
        view.content_fingerprint for view in exact.views
    ]

    approximate_board = output_dir / "inputs" / "approximate_2x2.png"
    _write_board(approximate_board, width=1100, height=612)
    approximate = crop_scene_multiview_board(
        approximate_board,
        output_dir / "approximate",
        output_stem="approximate",
        seam_guard=0,
    )
    assert (approximate.split_x, approximate.split_y) == (550, 306)
    assert (approximate.view_width, approximate.view_height) == (544, 306)
    assert all(view.width * 9 == view.height * 16 for view in approximate.views)
    _assert_view_colors(approximate)

    changed_board = output_dir / "inputs" / "changed_2x2.png"
    _write_board(changed_board, width=1280, height=720, marker=True, seam=True)
    changed = crop_scene_multiview_board(changed_board, output_dir / "changed", output_stem="changed")
    assert changed.content_fingerprint != exact.content_fingerprint
    assert changed.views[0].content_fingerprint != exact.views[0].content_fingerprint
    assert [view.content_fingerprint for view in changed.views[1:]] == [
        view.content_fingerprint for view in exact.views[1:]
    ]

    invalid_board = output_dir / "inputs" / "invalid_square.png"
    _write_board(invalid_board, width=800, height=800)
    try:
        crop_scene_multiview_board(invalid_board, output_dir / "invalid", output_stem="invalid")
    except ValueError as exc:
        assert "too far from 16:9" in str(exc)
    else:
        raise AssertionError("Square 2x2 board should fail the trim-fraction boundary")

    summary = {
        "status": "passed",
        "exact": {
            "source_size": [exact.source_width, exact.source_height],
            "view_size": [exact.view_width, exact.view_height],
            "content_fingerprint": exact.content_fingerprint,
            "paths": [str(view.asset_path) for view in exact.views],
        },
        "approximate": {
            "source_size": [approximate.source_width, approximate.source_height],
            "view_size": [approximate.view_width, approximate.view_height],
            "content_fingerprint": approximate.content_fingerprint,
            "crop_boxes": [list(view.crop_box) for view in approximate.views],
        },
        "checks": [
            "exact 2x2 boundaries",
            "configurable cross-seam exclusion",
            "largest common exact-16:9 crop from actual pixels",
            "no quadrant crossing or overlap",
            "path-independent deterministic content fingerprint",
            "single-view content change propagation",
            "invalid board aspect rejection",
        ],
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["report_path"] = str(report_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check deterministic 2x2 scene multiview cropping")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "scene-multiview-crop-smoke",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    tmp_root = (ROOT / ".tmp").resolve()
    if output_dir != tmp_root and tmp_root not in output_dir.parents:
        raise ValueError(f"output-dir must stay under {tmp_root}: {output_dir}")
    print(json.dumps(run(output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
