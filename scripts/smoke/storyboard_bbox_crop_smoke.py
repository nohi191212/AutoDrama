from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"storyboard_bbox_crop_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Storyboard BBox Crop Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    router = ProviderRouter(settings, provider_override="fake")
    state = await PregenWorkflow(repo=repo, router=router).run(
        project_dir,
        until="storyboard_panel_crop",
        force=True,
    )
    require("storyboard_bbox_detection" in state.completed_nodes, "bbox detection node not completed")
    require("storyboard_panel_crop" in state.completed_nodes, "panel crop node not completed")

    bbox_path = project_dir / "assets" / "json" / "nodes" / "storyboard_bbox_detection.json"
    crop_path = project_dir / "assets" / "json" / "nodes" / "storyboard_panel_crop.json"
    require(bbox_path.exists(), "storyboard bbox JSON missing")
    require(crop_path.exists(), "storyboard crop JSON missing")

    bbox_payload = json.loads(bbox_path.read_text(encoding="utf-8"))
    crop_payload = json.loads(crop_path.read_text(encoding="utf-8"))
    expected_panel_count = 2
    require(len(bbox_payload.get("episodes", [])) == 1, bbox_payload)
    require(bbox_payload["episodes"][0].get("panel_count") == expected_panel_count, bbox_payload)
    require(len(bbox_payload["episodes"][0].get("panels", [])) == expected_panel_count, bbox_payload)
    require(len(crop_payload.get("cropped_panels", [])) == expected_panel_count, crop_payload)

    for item in crop_payload["cropped_panels"]:
        panel_path = project_dir / item["asset_path"]
        require(panel_path.exists(), f"Missing cropped panel: {panel_path}")
        with Image.open(panel_path) as image:
            width, height = image.size
        require(width > 10 and height > 10, f"Cropped panel too small: {panel_path} {width}x{height}")
        require(item["bbox_source"] == "content_bbox_1000", f"Unexpected bbox source: {item}")

    print("storyboard_bbox_crop_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"bbox_path={bbox_path}")
    print(f"crop_count={len(crop_payload['cropped_panels'])}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
