from __future__ import annotations

import json
from pathlib import Path

from autodrama.core.schemas import ShotVideoStoryboardContentOutput
from autodrama.providers.base import AssetRef
from autodrama.workflows.generation import GenerationWorkflow


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "outputs" / "huyao"
STORYBOARD_PATH = PROJECT_DIR / "assets" / "json" / "nodes" / "storyboard_generation.json"


def _load_shot_prompt(shot_id: str) -> str:
    payload = json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    for item in payload.get("generated_storyboards", []):
        if isinstance(item, dict) and item.get("shot_id") == shot_id:
            return str(item.get("prompt") or "")
    raise AssertionError(f"missing prompt for {shot_id}")


def _extract_content(prompt: str) -> str:
    marker = "当前 shot 视频提示词："
    if marker not in prompt:
        raise AssertionError("missing shot prompt marker")
    return prompt.split(marker, 1)[1].strip()


def main() -> None:
    prompt = _load_shot_prompt("episode_001_shot_001")
    content = _extract_content(prompt)
    assert "0-1秒" in content
    assert "14-15秒" in content
    assert "当前 shot 视频提示词" not in content

    refs = [
        AssetRef(id="episode_001_shot_001_storyboard_panel", type="image", path=str(PROJECT_DIR / "assets" / "images" / "storyboards" / "episode_001_shot_001_storyboard_panel.png"), metadata={"asset_type": "storyboard_panel"}),
        AssetRef(id="layout_古老殿宇", type="image", path=str(PROJECT_DIR / "assets" / "images" / "layouts" / "layout_古老殿宇.png"), metadata={"asset_type": "layout"}),
        AssetRef(id="role_江未晞_appearance_base_roleboard", type="image", path=str(PROJECT_DIR / "assets" / "images" / "roles" / "role_江未晞_appearance_base_roleboard.png"), metadata={"asset_type": "roleboard"}),
    ]
    workflow = GenerationWorkflow.__new__(GenerationWorkflow)
    workflow.layout = None  # unused in this smoke
    slot_plan = GenerationWorkflow._shot_video_reference_plan(workflow, refs, provider=None)
    modal_refs = slot_plan["modal_refs"]["image"]
    assert modal_refs[0]["slot"] == "image_1"
    assert modal_refs[0]["asset_type"] == "storyboard_panel"
    assert modal_refs[1]["slot"] == "image_2"
    assert modal_refs[1]["asset_type"] == "layout"
    assert modal_refs[2]["slot"] == "image_3"
    assert modal_refs[2]["asset_type"] == "roleboard"

    print("shot_video_storyboard_content_extract_smoke: ok")


if __name__ == "__main__":
    main()
