from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Layout,
    Role,
    RoleAppearance,
    StoryboardBBox,
    StoryboardEpisodeOutput,
    StoryboardPanelCropItem,
    StoryboardPanelCropOutput,
    StoryboardShot,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


ONE_PIXEL_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(ONE_PIXEL_PNG))


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.providers["volcengine"].options["max_reference_images"] = 3
    repo = ProjectRepository(settings)
    project_id = f"shot_video_static_anchor_refs_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Video Static Anchor Refs Smoke",
        raw_script="Lin Zhou finds the switched contract in a meeting room.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    key_vision_path = project_dir / "assets" / "images" / "key_visions" / "key_vision_original.png"
    roleboard_path = project_dir / "assets" / "images" / "roles" / "role_linz_base_roleboard.png"
    storyboard_panel_path = (
        project_dir
        / "assets"
        / "images"
        / "storyboards"
        / "panels"
        / "episode_001_shot_001_storyboard_panel.png"
    )
    layout_path = project_dir / "assets" / "images" / "layouts" / "layout_room.png"
    for path in (key_vision_path, roleboard_path, storyboard_panel_path, layout_path):
        write_png(path)

    state.metadata["key_vision_asset"] = {
        "asset_id": "key_vision_original",
        "asset_path": "assets/images/key_visions/key_vision_original.png",
        "name": "key vision",
    }
    state.roles["role_linz"] = Role(
        id="role_linz",
        name="Lin Zhou",
        intro="Calm lead character.",
        appearances={
            "base": RoleAppearance(
                id="role_linz_base",
                role_id="role_linz",
                name="base",
                asset_id="role_linz_base_roleboard",
                asset_path="assets/images/roles/role_linz_base_roleboard.png",
            )
        },
    )
    state.layouts["layout_room"] = Layout(
        id="layout_room",
        name="Meeting Room",
        desc="A cold modern meeting room.",
        prompt="Empty modern meeting room.",
        asset_path="assets/images/layouts/layout_room.png",
    )
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_room",
        title="Contract reveal",
        duration_seconds=6,
        role_ids=["role_linz"],
        role_appearance_ids=["role_linz_base"],
        video_prompt="Lin Zhou stands by the projection screen and reveals the switched contract.",
    )
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[shot])

    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)
    crop_output_path = workflow.layout.node_output_path(project_dir, "storyboard_panel_crop")
    crop_output_path.parent.mkdir(parents=True, exist_ok=True)
    crop_output_path.write_text(
        StoryboardPanelCropOutput(
            cropped_panels=[
                StoryboardPanelCropItem(
                    episode_key="episode_001",
                    shot_id="episode_001_shot_001",
                    shot_index=1,
                    source_storyboard_asset_path="assets/images/storyboards/episode_001.png",
                    asset_id="episode_001_shot_001_storyboard_panel",
                    asset_path="assets/images/storyboards/panels/episode_001_shot_001_storyboard_panel.png",
                    bbox_source="bbox_1000",
                    bbox_1000=StoryboardBBox(x_min=0, y_min=0, x_max=1000, y_max=1000),
                )
            ]
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    provider = router.video("shot")
    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
    image_asset_types = [ref.metadata.get("asset_type") for ref in refs if ref.type == "image"]
    require(
        image_asset_types == ["storyboard_panel", "roleboard", "key_vision"],
        f"Unexpected image ref order: {image_asset_types}",
    )

    plan = workflow._shot_video_reference_plan(refs, provider=provider)
    require(
        plan["present_static_anchors"] == ["storyboard_panel", "roleboard", "key_vision"],
        json.dumps(plan, ensure_ascii=False, indent=2),
    )
    require(plan["missing_static_anchors"] == [], json.dumps(plan, ensure_ascii=False, indent=2))

    prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
    for phrase in (
        "静态三锚点策略",
        "故事板单格=图片1",
        "角色身份板=图片2",
        "主视觉原图=图片3",
        "三者职责不可互相覆盖",
    ):
        require(phrase in prompt, prompt)

    payload = provider.build_payload(prompt, refs=refs, duration=shot.duration_seconds)
    image_items = [item for item in payload["content"] if item["type"] == "image_url"]
    require(len(image_items) == 3, json.dumps(payload["content"], ensure_ascii=False, indent=2))
    require(
        all(item["role"] == "reference_image" for item in image_items),
        json.dumps(image_items, ensure_ascii=False, indent=2),
    )

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "shot_video_static_anchor_refs"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reference_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("shot_video_static_anchor_refs_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"image_asset_types={image_asset_types}")
    print(f"reference_plan_path={output_dir / 'reference_plan.json'}")
    print(f"payload_path={output_dir / 'payload.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
