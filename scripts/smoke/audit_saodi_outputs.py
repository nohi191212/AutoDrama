from __future__ import annotations

import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "outputs" / "saodi_bashinian_terra_image2"
TMP = ROOT / ".tmp" / "audit_saodi_bashinian_terra_image2"
REPORT = TMP / "audit_metrics.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(proc.stdout)


def image_metrics(path: Path) -> dict:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        sample = rgb.copy()
        sample.thumbnail((320, 320))
        arr = np.asarray(sample, dtype=np.float32)
        gray = arr.mean(axis=2)
        dx = np.diff(gray, axis=1)
        dy = np.diff(gray, axis=0)
        edge = float((np.mean(np.abs(dx)) + np.mean(np.abs(dy))) / 2)
        brightness = float(gray.mean())
        contrast = float(gray.std())
        hist = np.histogram(gray, bins=64, range=(0, 255), density=True)[0]
        hist = hist / max(float(hist.sum()), 1e-9)
        entropy = float(-np.sum(hist[hist > 0] * np.log2(hist[hist > 0])))
        try:
            display_path = path.relative_to(PROJECT).as_posix()
        except ValueError:
            display_path = path.relative_to(ROOT).as_posix()
        return {
            "path": display_path,
            "width": rgb.width,
            "height": rgb.height,
            "aspect": round(rgb.width / rgb.height, 4),
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "edge": round(edge, 2),
            "entropy": round(entropy, 3),
            "bytes": path.stat().st_size,
        }


def font(size: int):
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def make_contact_sheet(items: list[tuple[Path, str]], destination: Path, columns: int = 4) -> None:
    if not items:
        return
    thumb_w, thumb_h, label_h = 270, 480, 58
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), "#14171f")
    draw = ImageDraw.Draw(sheet)
    label_font = font(18)
    for idx, (path, label) in enumerate(items):
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((thumb_w - 10, thumb_h - 10))
            x = (idx % columns) * thumb_w + (thumb_w - image.width) // 2
            y = (idx // columns) * (thumb_h + label_h) + (thumb_h - image.height) // 2
            sheet.paste(image, (x, y))
        tx = (idx % columns) * thumb_w + 8
        ty = (idx // columns) * (thumb_h + label_h) + thumb_h + 4
        draw.text((tx, ty), label[:30], fill="#f3f4f6", font=label_font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=92)


def extract_video_frames(video: Path, shot_id: str, duration: float) -> list[Path]:
    frame_dir = TMP / "video_frames" / shot_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for idx, fraction in enumerate((0.08, 0.5, 0.9), start=1):
        destination = frame_dir / f"{idx}.jpg"
        timestamp = max(0.0, min(duration - 0.08, duration * fraction))
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(destination),
            ],
            check=True,
        )
        result.append(destination)
    return result


def audio_silence_ratio(video: Path, duration: float) -> float | None:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-af",
            "silencedetect=noise=-42dB:d=0.25",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if "Audio:" not in proc.stderr:
        return None
    starts = []
    total = 0.0
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            try:
                starts.append(float(line.rsplit("silence_start:", 1)[1].strip()))
            except ValueError:
                pass
        if "silence_end:" in line and "| silence_duration:" in line:
            try:
                total += float(line.rsplit("| silence_duration:", 1)[1].strip())
                if starts:
                    starts.pop()
            except ValueError:
                pass
    for start in starts:
        total += max(0.0, duration - start)
    return round(min(1.0, total / duration), 4) if duration > 0 else None


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    state = load_json(PROJECT / "state.json")
    shots_doc = load_json(PROJECT / "shots" / "episode_001.json")
    shots = shots_doc["shots"]

    nodes = []
    node_dir = PROJECT / "assets" / "json" / "nodes"
    node_files = {p.stem: p for p in node_dir.glob("*.json")}
    for name in state.get("completed_nodes", []):
        path = node_files.get(name)
        nodes.append(
            {
                "name": name,
                "has_summary_json": bool(path),
                "summary_bytes": path.stat().st_size if path else 0,
            }
        )

    image_categories = {
        "key_vision": PROJECT / "assets" / "images" / "key_vision",
        "roles": PROJECT / "assets" / "images" / "roles",
        "props": PROJECT / "assets" / "images" / "props",
        "layouts": PROJECT / "assets" / "images" / "layouts",
        "shot_backgrounds": PROJECT / "assets" / "images" / "shot_backgrounds",
        "shot_keyframes": PROJECT / "assets" / "images" / "shot_keyframes",
    }
    images: dict[str, list[dict]] = {}
    for category, directory in image_categories.items():
        paths = sorted(
            [*directory.glob("*.png"), *directory.glob("*.jpg"), *directory.glob("*.jpeg")]
        ) if directory.exists() else []
        images[category] = [image_metrics(path) for path in paths]
        if paths:
            make_contact_sheet(
                [(p, p.stem.replace("episode_001_clip_", "c")) for p in paths],
                TMP / "contact_sheets" / f"{category}.jpg",
            )

    shot_rows = []
    frame_items = []
    role_shot_counts = Counter()
    clip_counts = Counter()
    for shot in shots:
        shot_id = shot["shot_id"]
        video = PROJECT / shot["video_asset_path"]
        probe = ffprobe(video)
        duration = float(probe.get("format", {}).get("duration") or shot["duration_seconds"])
        streams = probe.get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        dialogue = "\n".join(shot.get("dialogue") or [])
        dialogue_chars = len(dialogue.replace(" ", "").replace("\n", ""))
        roles = shot.get("role_ids") or []
        role_shot_counts.update(roles)
        clip_counts.update([shot.get("clip_id")])
        frames = extract_video_frames(video, shot_id, duration)
        frame_items.extend((p, f"{shot['index']:02d}-{suffix}") for p, suffix in zip(frames, ("首", "中", "尾")))
        first_frame = image_metrics(frames[0])
        mid_frame = image_metrics(frames[1])
        last_frame = image_metrics(frames[2])
        shot_rows.append(
            {
                "index": shot["index"],
                "shot_id": shot_id,
                "clip_id": shot.get("clip_id"),
                "title": shot.get("title"),
                "planned_duration": shot.get("duration_seconds"),
                "actual_duration": round(duration, 3),
                "dialogue": shot.get("dialogue") or [],
                "dialogue_chars": dialogue_chars,
                "chars_per_second": round(dialogue_chars / duration, 2) if duration else None,
                "roles": roles,
                "role_count": len(roles),
                "prop_ids": shot.get("prop_ids") or [],
                "ref_count": len(shot.get("video_inputs") or []),
                "has_audio": has_audio,
                "silence_ratio": audio_silence_ratio(video, duration) if has_audio else None,
                "frame_edge": [first_frame["edge"], mid_frame["edge"], last_frame["edge"]],
                "frame_brightness": [
                    first_frame["brightness"],
                    mid_frame["brightness"],
                    last_frame["brightness"],
                ],
            }
        )

    for start in range(0, len(frame_items), 36):
        make_contact_sheet(
            frame_items[start : start + 36],
            TMP / "contact_sheets" / f"video_frames_{start // 36 + 1}.jpg",
            columns=6,
        )

    final_video = PROJECT / "outputs" / "videos" / "episode_001_postgen_all33.mp4"
    final_probe = ffprobe(final_video)
    edit_plan_path = PROJECT / "assets" / "json" / "postgen" / "edit_plans" / "episode_001.all33.validated.json"
    edit_plan = load_json(edit_plan_path)

    data = {
        "project": {
            "id": state["project_id"],
            "completed_node_count": len(state.get("completed_nodes", [])),
            "shot_count": len(shots),
            "planned_shot_duration_total": round(sum(float(s["duration_seconds"]) for s in shots), 3),
            "actual_shot_duration_total": round(sum(r["actual_duration"] for r in shot_rows), 3),
            "final_video_probe": final_probe,
        },
        "nodes": nodes,
        "images": images,
        "shots": shot_rows,
        "role_shot_counts": dict(role_shot_counts),
        "clip_shot_counts": dict(clip_counts),
        "edit_plan_top_keys": sorted(edit_plan.keys()) if isinstance(edit_plan, dict) else [],
        "contact_sheets": sorted(
            p.relative_to(ROOT).as_posix() for p in (TMP / "contact_sheets").glob("*.jpg")
        ),
    }
    REPORT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
