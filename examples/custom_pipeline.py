#!/usr/bin/env python
"""Example: build a custom LangGraph pipeline with a subset of AutoDrama nodes.

This demonstrates how to compose your own graph using individual nodes
without running the full default pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.graph import END, StateGraph

from autodrama.config.loader import ConfigLoader
from autodrama.pipeline.nodes import ScriptNode, SceneNode
from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import setup_logger


def main() -> None:
    setup_logger("autodrama", "INFO")

    config = ConfigLoader.load("./config/config.yaml")

    # Build a minimal pipeline: just generate a script + plan scenes
    script_node = ScriptNode(config)
    scene_node = SceneNode(config)

    builder = StateGraph(DramaState)
    builder.add_node("generate_script", script_node.execute)
    builder.add_node("plan_scenes", scene_node.execute)

    builder.set_entry_point("generate_script")
    builder.add_edge("generate_script", "plan_scenes")
    builder.add_edge("plan_scenes", END)

    pipeline = builder.compile()

    result = pipeline.invoke({
        "concept": "A robot discovers emotions for the first time in a futuristic city",
        "max_retries": 3,
        "retry_count": 0,
        "errors": [],
        "warnings": [],
    })

    # Print results
    script = result.get("script", {})
    print(f"\nScript: {script.get('title', 'Untitled')}")
    print(f"Genre: {script.get('genre', 'N/A')}")
    print(f"Scenes: {len(script.get('scenes', []))}")

    plans = result.get("scene_plans") or []
    for plan_dict in plans:
        sn = plan_dict.get("scene_number")
        shots = plan_dict.get("shots", [])
        print(f"  Scene {sn}: {len(shots)} shots")
        for shot in shots:
            prompt = shot.get("image_prompt", "")[:80]
            print(f"    Shot {shot['shot_index']}: {shot['frame_type']} — {prompt}…")


if __name__ == "__main__":
    main()
