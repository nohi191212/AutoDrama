from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ClipPromptEpisode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split legacy clip_prompt.json into per-episode files.")
    parser.add_argument("--project-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = Path(args.project_dir).resolve()
    legacy_path = project_dir / "assets" / "json" / "nodes" / "clip_prompt.json"
    if not legacy_path.exists():
        raise FileNotFoundError(legacy_path)
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    episodes = payload.get("clip_prompts")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError(f"Invalid legacy clip_prompt output: {legacy_path}")

    output_dir = legacy_path.parent / "clip_prompt"
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("clip_prompts entries must be JSON objects")
        episode_key = str(episode.get("episode_key") or "").strip()
        if not episode_key:
            raise ValueError("clip_prompt episode is missing episode_key")
        output_path = output_dir / f"{episode_key}.json"
        output_path.write_text(
            json.dumps(episode, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(output_path)

    for output_path in written:
        migrated = json.loads(output_path.read_text(encoding="utf-8"))
        if migrated.get("episode_key") != output_path.stem:
            raise AssertionError(f"Migration verification failed: {output_path}")
        ClipPromptEpisode.model_validate(migrated)
        print(f"migrated={output_path}")
    print(f"migrate_clip_prompt_episode_files: ok episodes={len(written)}")


if __name__ == "__main__":
    main()
