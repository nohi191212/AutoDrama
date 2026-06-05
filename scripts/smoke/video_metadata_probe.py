from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from moviepy import VideoFileClip
except ImportError:  # pragma: no cover
    from moviepy.editor import VideoFileClip  # type: ignore


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: video_metadata_probe.py <video_path>")
    path = Path(sys.argv[1])
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    with VideoFileClip(str(path)) as clip:
        payload = {
            "path": str(path.resolve()),
            "width": int(clip.w),
            "height": int(clip.h),
            "duration": float(clip.duration or 0),
            "fps": float(clip.fps or 0),
            "has_audio": clip.audio is not None,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
