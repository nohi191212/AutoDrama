from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_DIR = ROOT / "outputs" / "saodi_0803"
DEFAULT_EPISODE_KEY = "episode_001"


@dataclass(frozen=True)
class PanelSpec:
    label: str
    node_name: str
    collection_key: str
    asset_path_key: str
    conventional_path: str


PANELS = (
    PanelSpec(
        label="BACKGROUND",
        node_name="shot_background_image_generation",
        collection_key="generated_backgrounds",
        asset_path_key="asset_path",
        conventional_path="assets/images/shot_backgrounds/{shot_id}_background.png",
    ),
    PanelSpec(
        label="CONTROL",
        node_name="shot_blocking_control_render",
        collection_key="generated_controls",
        asset_path_key="asset_path",
        conventional_path="assets/images/shot_blocking_controls/{shot_id}.png",
    ),
    PanelSpec(
        label="STAGE",
        node_name="shot_keyframe_stage_generation",
        collection_key="generated_stages",
        asset_path_key="stage_asset_path",
        conventional_path="assets/images/shot_keyframe_stages/{shot_id}.png",
    ),
    PanelSpec(
        label="FINAL",
        node_name="shot_keyframe_image_generation",
        collection_key="generated_keyframes",
        asset_path_key="keyframe_asset_path",
        conventional_path="assets/images/shot_keyframes/{shot_id}.png",
    ),
)


def _resample() -> int:
    return getattr(Image, "Resampling", Image).LANCZOS


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _selected_shot_ids(project_dir: Path, episode_key: str) -> list[str]:
    selection_path = project_dir / "assets" / "json" / "expected_output_selection.json"
    selection = _read_json(selection_path)
    for episode in selection.get("episodes", []):
        if episode.get("episode_key") == episode_key:
            shot_ids = [str(item) for item in episode.get("selected_shot_ids", [])]
            if shot_ids:
                return shot_ids

    plan_path = (
        project_dir
        / "assets"
        / "json"
        / "nodes"
        / "clip_to_shots"
        / f"{episode_key}.json"
    )
    plan = _read_json(plan_path)
    shot_ids: list[str] = []
    for clip in plan.get("clips", []):
        for shot in clip.get("shots", []):
            shot_id = str(shot.get("shot_id") or "").strip()
            if shot_id:
                shot_ids.append(shot_id)
    if not shot_ids:
        raise ValueError(
            f"no selected shots found in {selection_path} or {plan_path} for {episode_key}"
        )
    return shot_ids


def _node_records(
    project_dir: Path, episode_key: str, spec: PanelSpec
) -> tuple[Path, dict[str, dict[str, Any]]]:
    path = (
        project_dir
        / "assets"
        / "json"
        / "nodes"
        / spec.node_name
        / f"{episode_key}.json"
    )
    payload = _read_json(path)
    records: dict[str, dict[str, Any]] = {}
    for raw in payload.get(spec.collection_key, []):
        if not isinstance(raw, dict):
            continue
        shot_id = str(raw.get("shot_id") or "").strip()
        if shot_id:
            records[shot_id] = raw
    return path, records


def _inspect_panel(
    project_dir: Path,
    shot_id: str,
    spec: PanelSpec,
    node_json_path: Path,
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    conventional_relative = spec.conventional_path.format(shot_id=shot_id)
    conventional_file = project_dir / conventional_relative
    info: dict[str, Any] = {
        "label": spec.label.lower(),
        "node_name": spec.node_name,
        "node_json_path": str(node_json_path.relative_to(project_dir)).replace("\\", "/"),
        "node_json_present": node_json_path.is_file(),
        "node_record_present": record is not None,
        "asset_path": None,
        "file_exists": False,
        "status": "missing_node_record",
        "width": None,
        "height": None,
        "mode": None,
        "aspect_ratio": None,
        "conventional_path": conventional_relative,
        "stale_conventional_file": record is None and conventional_file.is_file(),
    }
    if record is None:
        return info

    raw_path = str(record.get(spec.asset_path_key) or "").strip()
    if not raw_path:
        info["status"] = "missing_asset_path"
        return info
    info["asset_path"] = raw_path.replace("\\", "/")
    asset_file = project_dir / raw_path
    if not asset_file.is_file():
        info["status"] = "missing_file"
        return info

    try:
        with Image.open(asset_file) as opened:
            opened.load()
            width, height = opened.size
            info.update(
                {
                    "file_exists": True,
                    "status": "present",
                    "width": width,
                    "height": height,
                    "mode": opened.mode,
                    "aspect_ratio": round(width / height, 6) if height else None,
                }
            )
    except Exception as exc:
        info["status"] = "unreadable_file"
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _fit_image(source: Path, width: int, height: int) -> Image.Image:
    with Image.open(source) as opened:
        panel = opened.convert("RGB")
        panel.thumbnail((width, height), _resample())
    canvas = Image.new("RGB", (width, height), "#11151d")
    canvas.paste(panel, ((width - panel.width) // 2, (height - panel.height) // 2))
    return canvas


def _missing_panel(width: int, height: int, info: dict[str, Any]) -> Image.Image:
    panel = Image.new("RGB", (width, height), "#2b1014")
    draw = ImageDraw.Draw(panel)
    for x in range(-height, width, 32):
        draw.line((x, 0, x + height, height), fill="#4c1d24", width=10)
    title_font = _font(30)
    detail_font = _font(17)
    status = str(info["status"]).replace("_", " ").upper()
    draw.text(
        (width // 2, height // 2 - 28),
        status,
        fill="#ffccd2",
        font=title_font,
        anchor="mm",
    )
    if info.get("stale_conventional_file"):
        detail = "STALE CONVENTIONAL FILE IGNORED"
    elif not info.get("node_json_present"):
        detail = "NODE JSON FILE IS MISSING"
    else:
        detail = "NO CURRENT ASSET IS AUTHORITATIVE"
    draw.text(
        (width // 2, height // 2 + 20),
        detail,
        fill="#f4a4ae",
        font=detail_font,
        anchor="mm",
    )
    return panel


def _render_cell(
    project_dir: Path,
    info: dict[str, Any],
    width: int,
    height: int,
    *,
    include_label: bool,
) -> Image.Image:
    label_height = 42 if include_label else 0
    cell = Image.new("RGB", (width, height + label_height), "#11151d")
    if info["status"] == "present":
        source = project_dir / str(info["asset_path"])
        panel = _fit_image(source, width, height)
    else:
        panel = _missing_panel(width, height, info)
    cell.paste(panel, (0, label_height))
    if include_label:
        draw = ImageDraw.Draw(cell)
        draw.rectangle((0, 0, width, label_height), fill="#171b24")
        draw.text(
            (14, label_height // 2),
            str(info["label"]).upper(),
            fill="white",
            font=_font(20),
            anchor="lm",
        )
        if info["status"] == "present":
            dims = f"{info['width']}x{info['height']}"
            draw.text(
                (width - 14, label_height // 2),
                dims,
                fill="#b8c2d8",
                font=_font(17),
                anchor="rm",
            )
    return cell


def _build_shot_sheet(
    project_dir: Path,
    shot_id: str,
    panel_infos: list[dict[str, Any]],
    output_path: Path,
) -> None:
    panel_width = 640
    panel_height = 360
    title_height = 54
    cell_height = panel_height + 42
    sheet = Image.new(
        "RGB", (panel_width * 2, title_height + cell_height * 2), "#0c0e13"
    )
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (18, title_height // 2),
        shot_id,
        fill="white",
        font=_font(24),
        anchor="lm",
    )
    for index, info in enumerate(panel_infos):
        cell = _render_cell(
            project_dir, info, panel_width, panel_height, include_label=True
        )
        x = (index % 2) * panel_width
        y = title_height + (index // 2) * cell_height
        sheet.paste(cell, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=True)


def _build_overview(
    project_dir: Path,
    shots: list[dict[str, Any]],
    output_path: Path,
) -> None:
    panel_width = 360
    panel_height = 203
    shot_label_width = 300
    header_height = 48
    overview = Image.new(
        "RGB",
        (
            shot_label_width + panel_width * len(PANELS),
            header_height + panel_height * len(shots),
        ),
        "#0c0e13",
    )
    draw = ImageDraw.Draw(overview)
    draw.text(
        (12, header_height // 2),
        "SHOT",
        fill="white",
        font=_font(20),
        anchor="lm",
    )
    for index, spec in enumerate(PANELS):
        x = shot_label_width + index * panel_width
        draw.rectangle((x, 0, x + panel_width, header_height), fill="#171b24")
        draw.text(
            (x + 12, header_height // 2),
            spec.label,
            fill="white",
            font=_font(20),
            anchor="lm",
        )
    for row_index, shot in enumerate(shots):
        y = header_height + row_index * panel_height
        label_fill = "#151a23" if row_index % 2 == 0 else "#11151d"
        draw.rectangle((0, y, shot_label_width, y + panel_height), fill=label_fill)
        draw.text(
            (12, y + panel_height // 2),
            str(shot["shot_id"]),
            fill="#dce3f1",
            font=_font(16),
            anchor="lm",
        )
        for column_index, info in enumerate(shot["panels"]):
            cell = _render_cell(
                project_dir,
                info,
                panel_width,
                panel_height,
                include_label=False,
            )
            overview.paste(cell, (shot_label_width + column_index * panel_width, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overview.save(output_path, format="JPEG", quality=90, optimize=True)


def build_audit(
    project_dir: Path,
    episode_key: str,
    output_dir: Path,
) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    output_dir = output_dir.resolve()
    shot_ids = _selected_shot_ids(project_dir, episode_key)
    records_by_label: dict[str, dict[str, dict[str, Any]]] = {}
    json_paths: dict[str, Path] = {}
    for spec in PANELS:
        json_path, records = _node_records(project_dir, episode_key, spec)
        records_by_label[spec.label] = records
        json_paths[spec.label] = json_path

    shots: list[dict[str, Any]] = []
    for shot_id in shot_ids:
        panels = [
            _inspect_panel(
                project_dir,
                shot_id,
                spec,
                json_paths[spec.label],
                records_by_label[spec.label].get(shot_id),
            )
            for spec in PANELS
        ]
        background_size = None
        if panels[0]["status"] == "present":
            background_size = [panels[0]["width"], panels[0]["height"]]
        for panel in panels:
            actual_size = None
            if panel["status"] == "present":
                actual_size = [panel["width"], panel["height"]]
            panel["matches_background_size"] = (
                actual_size == background_size
                if actual_size is not None and background_size is not None
                else None
            )
        sheet_relative = f"sheets/{shot_id}_four_up.png"
        _build_shot_sheet(project_dir, shot_id, panels, output_dir / sheet_relative)
        shots.append(
            {
                "shot_id": shot_id,
                "four_up_path": sheet_relative,
                "available_panel_count": sum(
                    panel["status"] == "present" for panel in panels
                ),
                "background_size": background_size,
                "panels": panels,
            }
        )

    overview_relative = f"{episode_key}_overview.jpg"
    _build_overview(project_dir, shots, output_dir / overview_relative)

    coverage: dict[str, Any] = {}
    for panel_index, spec in enumerate(PANELS):
        infos = [shot["panels"][panel_index] for shot in shots]
        status_counts: dict[str, int] = {}
        for info in infos:
            status = str(info["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        coverage[spec.label.lower()] = {
            "selected_shot_count": len(shot_ids),
            "node_record_count": sum(info["node_record_present"] for info in infos),
            "existing_file_count": sum(info["file_exists"] for info in infos),
            "stale_conventional_file_count": sum(
                info["stale_conventional_file"] for info in infos
            ),
            "status_counts": status_counts,
            "missing_shot_ids": [
                shot["shot_id"]
                for shot in shots
                if shot["panels"][panel_index]["status"] != "present"
            ],
            "size_mismatch_shot_ids": [
                shot["shot_id"]
                for shot in shots
                if shot["panels"][panel_index]["matches_background_size"] is False
            ],
        }

    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(project_dir),
        "episode_key": episode_key,
        "selected_shot_count": len(shot_ids),
        "overview_path": overview_relative,
        "coverage": coverage,
        "shots": shots,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build background/control/stage/final four-up sheets from authoritative node JSON. "
            "Conventional image files without node records are reported as stale and ignored."
        )
    )
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--episode-key", default=DEFAULT_EPISODE_KEY)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or (
        ROOT
        / ".tmp"
        / "keyframe_comparison_sheet"
        / args.project_dir.name
        / args.episode_key
    )
    report = build_audit(args.project_dir, args.episode_key, output_dir)
    print(
        json.dumps(
            {
                "report_path": str((output_dir / "report.json").resolve()),
                "overview_path": str((output_dir / report["overview_path"]).resolve()),
                "selected_shot_count": report["selected_shot_count"],
                "coverage": report["coverage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
