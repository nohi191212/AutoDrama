#!/usr/bin/env python
"""End-to-end example: generate a short drama video from a concept."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autodrama.config.loader import ConfigLoader
from autodrama.pipeline.graph import build_pipeline
from autodrama.utils.logger import setup_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoDrama — generate a short drama video")
    parser.add_argument("--concept", "-c", type=str, required=True,
                        help="Drama concept (e.g. 'A detective finds a mysterious letter on a rainy night')")
    parser.add_argument("--config", type=str, default="./config/config.yaml",
                        help="Path to config YAML")
    parser.add_argument("--genre", type=str, default="",
                        help="Genre hint (romance, thriller, comedy, scifi, etc.)")
    parser.add_argument("--style", type=str, default="cinematic",
                        help="Visual style")
    parser.add_argument("--num-scenes", type=int, default=0,
                        help="Target number of scenes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logger(name="autodrama", level=log_level)

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found at {config_path}. Creating default…")
        ConfigLoader.save_default(config_path)

    config = ConfigLoader.load(str(config_path))

    # Build pipeline
    pipeline = build_pipeline(config)

    # Prepare input
    input_state = {
        "concept": args.concept,
        "input_params": {
            "genre": args.genre,
            "style": args.style,
            "num_scenes": args.num_scenes,
        },
        "max_retries": config.pipeline.max_retries or 3,
        "retry_count": 0,
        "errors": [],
        "warnings": [],
    }

    # Run
    print(f"\n{'='*60}")
    print(f"AutoDrama Pipeline")
    print(f"Concept: {args.concept}")
    print(f"{'='*60}\n")

    final_state = pipeline.invoke(input_state)

    # Report
    video_path = final_state.get("final_video_path")
    if video_path:
        print(f"\nDone! Video saved to: {video_path}")
    else:
        print("\nPipeline did not produce a video. Errors:")
        for err in final_state.get("errors", []):
            print(f"  [{err.get('stage')}] {err.get('error')}")


if __name__ == "__main__":
    main()
