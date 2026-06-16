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
    StoryboardEpisodeOutput,
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
        "max_reference_images": 3,
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
    layout_path = project_dir / "assets" / "images" / "layouts" / "layout_meeting.png"
    storyboard_panel_path = (
        project_dir
        / "assets"
        / "images"
        / "storyboards"
        / "panels"
        / "episode_001_shot_001_storyboard_panel.png"
    )
    for path in (key_vision_path, roleboard_path, layout_path, storyboard_panel_path):
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
    state.layouts["layout_meeting"] = Layout(
        id="layout_meeting",
        name="会议室",
        desc="无人物冷色会议室，投影屏位于长墙一侧。",
        prompt="Empty cold meeting room, projection screen on one long wall.",
        asset_path="assets/images/layouts/layout_meeting.png",
    )
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_meeting",
        title="合同展示",
        duration_seconds=5,
        role_ids=["role_linz"],
        role_appearance_ids=["role_linz_base"],
        storyboard_panel_asset_id="episode_001_shot_001_storyboard_panel",
        storyboard_panel_asset_path="assets/images/storyboards/panels/episode_001_shot_001_storyboard_panel.png",
        per_second_content="0-2秒 林舟站在投影屏旁边；2-5秒 他克制地展示被调包的合同。",
        video_prompt="林舟站在投影屏旁边，克制地展示被调包的合同。",
    )
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[shot])

    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)

    provider = router.video("shot", node_name="shot_video_generation")
    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
    ref_types = [ref.type for ref in refs]
    asset_types = [ref.metadata.get("asset_type") for ref in refs]
    require(ref_types == ["image", "image", "image"], f"Unexpected ref types: {ref_types}")
    require(asset_types == ["storyboard_panel", "layout", "roleboard"], f"Unexpected asset types: {asset_types}")

    filtered_refs = workflow._shot_video_refs_for_provider(refs, provider=provider)
    filtered_asset_types = [ref.metadata.get("asset_type") for ref in filtered_refs]
    require(filtered_asset_types == ["storyboard_panel", "layout", "roleboard"], f"Unexpected filtered refs: {filtered_asset_types}")
    require(not any(ref.type == "element" for ref in filtered_refs), f"Shot video must not pass element refs: {filtered_refs}")
    plan = workflow._shot_video_reference_plan(filtered_refs, provider=provider)
    require(
        plan["present_static_anchors"] == ["storyboard_panel", "layout", "roleboard"],
        json.dumps(plan, ensure_ascii=False, indent=2),
    )
    prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
    for phrase in (
        "素材：",
        "<<<image_1>>> 是当前镜头故事板",
        "<<<image_2>>> 是当前场景三视图/场景图",
        "<<<image_3>>> 是当前人物三视图/角色身份板",
        "蓝色箭头=摄影机运动",
        "紫色标记=情绪/声音/叙事强调",
        "最终视频禁止生成任何箭头",
        "0-2秒 林舟站在投影屏旁边",
        "林舟站在投影屏旁边",
    ):
        require(phrase in prompt, prompt)
    require("主视觉风格参考" not in prompt, prompt)
    require("<<<element_" not in prompt, prompt)
    require("Kling Omni 素材引用规则" not in prompt, prompt)
    require("主体生成模式" not in prompt, prompt)

    payload = provider.build_payload(prompt, refs=filtered_refs, duration=shot.duration_seconds)
    require(len(payload["image_list"]) == 3, json.dumps(payload, ensure_ascii=False, indent=2))
    require("element_list" not in payload, json.dumps(payload, ensure_ascii=False, indent=2))

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
