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
    settings.nodes["shot_video_generation"].model = "kling:kling-v3-omni"
    settings.nodes["shot_video_generation"].params = {
        "aspect_ratio": "9:16",
        "mode": "pro",
        "sound": "off",
        "watermark": False,
        "max_reference_images": 2,
        "max_reference_elements": 3,
    }
    repo = ProjectRepository(settings)
    project_id = f"shot_video_kling_subject_refs_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Kling Subject Refs Smoke",
        raw_script="林舟在会议室展示被调包的合同。",
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
    for path in (key_vision_path, roleboard_path, storyboard_panel_path):
        write_png(path)

    state.metadata["key_vision_asset"] = {
        "asset_id": "key_vision_original",
        "asset_path": "assets/images/key_visions/key_vision_original.png",
        "name": "key vision",
    }
    state.roles["role_linz"] = Role(
        id="role_linz",
        name="林舟",
        intro="冷静克制的职场青年。",
        appearances={
            "base": RoleAppearance(
                id="role_linz_base",
                role_id="role_linz",
                name="base",
                asset_id="role_linz_base_roleboard",
                asset_path="assets/images/roles/role_linz_base_roleboard.png",
                subject_element_id="123456789",
                subject_element_reference_type="video_refer",
            )
        },
    )
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_meeting",
        title="合同展示",
        duration_seconds=5,
        role_ids=["role_linz"],
        role_appearance_ids=["role_linz_base"],
        video_prompt="林舟站在投影屏旁边，克制地展示被调包的合同。",
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

    provider = router.video("shot", node_name="shot_video_generation")
    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
    ref_types = [ref.type for ref in refs]
    asset_types = [ref.metadata.get("asset_type") for ref in refs]
    require(ref_types == ["image", "image", "element"], f"Unexpected ref types: {ref_types}")
    require(asset_types == ["storyboard_panel", "key_vision", "role_subject_element"], f"Unexpected asset types: {asset_types}")

    filtered_refs = workflow._shot_video_refs_for_provider(refs, provider=provider)
    plan = workflow._shot_video_reference_plan(filtered_refs, provider=provider)
    require(
        plan["present_static_anchors"] == ["storyboard_panel", "key_vision", "role_subject_element"],
        json.dumps(plan, ensure_ascii=False, indent=2),
    )
    prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
    for phrase in (
        "Kling Omni 素材引用规则",
        "<<<image_1>>> = 当前 shot 故事板单格",
        "<<<image_2>>> = 主视觉原图",
        "<<<element_1>>> = 林舟/base",
        "主体生成模式",
    ):
        require(phrase in prompt, prompt)

    payload = provider.build_payload(prompt, refs=filtered_refs, duration=shot.duration_seconds)
    require(len(payload["image_list"]) == 2, json.dumps(payload, ensure_ascii=False, indent=2))
    require(payload["element_list"] == [{"element_id": 123456789}], json.dumps(payload, ensure_ascii=False, indent=2))
    require("roleboard" not in asset_types, f"Roleboard should not be passed in Kling subject mode: {asset_types}")

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "shot_video_kling_subject_refs"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reference_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    print("shot_video_kling_subject_refs_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"payload_path={output_dir / 'payload.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

