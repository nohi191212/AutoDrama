from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

try:
    from moviepy import VideoFileClip
except ImportError:  # pragma: no cover
    from moviepy.editor import VideoFileClip  # type: ignore


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mp4_audio_level.py PATH")
    path = Path(sys.argv[1])
    with VideoFileClip(str(path)) as clip:
        if clip.audio is None:
            print("has_audio=false")
            return
        sample_rate = 16000
        duration = float(clip.audio.duration or clip.duration or 0)
        if duration <= 0:
            print("has_audio=true")
            print("duration=0")
            return
        values = []
        for start in np.arange(0, duration, 1.0):
            end = min(float(start + 1.0), duration)
            samples = clip.audio.subclipped(float(start), end).to_soundarray(fps=sample_rate)
            if samples.size:
                values.append(samples.astype("float32"))
        if not values:
            print("has_audio=true")
            print("samples=0")
            return
        audio = np.concatenate(values, axis=0)
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(np.square(audio))))
        print("has_audio=true")
        print(f"duration={duration:.3f}")
        print(f"peak={peak:.8f}")
        print(f"rms={rms:.8f}")


if __name__ == "__main__":
    main()
