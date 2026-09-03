from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def resolve_item_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a randomized blind roleboard contact sheet")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--answer-key", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--columns", type=int)
    parser.add_argument("--cell-width", type=int, default=640)
    parser.add_argument("--cell-height", type=int, default=420)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Contact-sheet manifest requires a non-empty items list")

    seed = args.seed if args.seed is not None else int(manifest.get("seed", 20260807))
    columns = args.columns or int(manifest.get("columns", 3))
    if columns <= 0 or args.cell_width <= 0 or args.cell_height <= 0:
        raise ValueError("Columns and cell dimensions must be positive")

    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict) or not raw.get("path"):
            raise ValueError(f"Invalid item {index}: {raw!r}")
        path = resolve_item_path(manifest_path, str(raw["path"]))
        items.append(
            {
                "source_index": index,
                "path": path,
                "candidate_id": str(raw.get("candidate_id") or ""),
                "sample_id": str(raw.get("sample_id") or path.stem),
                "public_label": str(raw.get("public_label") or "").strip(),
                "image_sha256": sha256(path),
            }
        )

    random.Random(seed).shuffle(items)
    for index, item in enumerate(items, start=1):
        item["blind_id"] = f"B{index:03d}"

    title = str(manifest.get("title") or "Blind roleboard review")
    title_height = 72
    label_height = 48
    padding = 18
    rows = math.ceil(len(items) / columns)
    width = columns * args.cell_width + (columns + 1) * padding
    height = title_height + rows * (args.cell_height + label_height) + (rows + 1) * padding
    canvas = Image.new("RGB", (width, height), "#171b20")
    draw = ImageDraw.Draw(canvas)
    title_font = load_font(28)
    label_font = load_font(22)
    draw.text((padding, 20), title, fill="#f3f5f7", font=title_font)

    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        left = padding + column * (args.cell_width + padding)
        top = title_height + padding + row * (args.cell_height + label_height + padding)
        with Image.open(item["path"]) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            fitted = ImageOps.contain(source, (args.cell_width, args.cell_height), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (args.cell_width, args.cell_height), "#e8e8e5")
        paste_x = (args.cell_width - fitted.width) // 2
        paste_y = (args.cell_height - fitted.height) // 2
        tile.paste(fitted, (paste_x, paste_y))
        canvas.paste(tile, (left, top))
        label = item["blind_id"]
        if item["public_label"]:
            label += f" · {item['public_label']}"
        draw.text((left + 8, top + args.cell_height + 10), label, fill="#f3f5f7", font=label_font)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)

    key_path = Path(args.answer_key).resolve()
    answer_key = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "seed": seed,
        "columns": columns,
        "cell_width": args.cell_width,
        "cell_height": args.cell_height,
        "sheet_path": str(output_path),
        "sheet_sha256": sha256(output_path),
        "items": [
            {
                "blind_id": item["blind_id"],
                "candidate_id": item["candidate_id"],
                "sample_id": item["sample_id"],
                "public_label": item["public_label"],
                "source_path": str(item["path"]),
                "image_sha256": item["image_sha256"],
            }
            for item in items
        ],
    }
    write_json(key_path, answer_key)
    print(str(output_path))
    print(str(key_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
