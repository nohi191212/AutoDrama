from __future__ import annotations

import base64
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Role,
    RoleAppearance,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.utils.video_prompts import sanitize_video_prompt_text  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


ONE_PIXEL_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(ONE_PIXEL_PNG))


def main() -> int:
    settings = load_settings(ROOT_DIR / "huyao.yaml")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_dir = repo.create_project(
        title="Kling Video Prompt Smoke",
        raw_script="江未晞在殿宇中醒来，九韶以画外音提示绑定完成。",
        project_id="kling_video_prompt_smoke",
        episode_count=1,
        episode_duration_seconds=30,
    )
    state: ProjectState = repo.load_state(project_dir)
    state.metadata["key_vision_asset"] = {
        "asset_path": "assets/images/key_visions/key_vision_original.png",
        "asset_url": "https://example.invalid/key-vision.png",
    }
    write_png(project_dir / "assets" / "images" / "key_visions" / "key_vision_original.png")

    roleboard_path = project_dir / "assets" / "images" / "roles" / "role_jiangweixi_base_roleboard.png"
    layout_path = project_dir / "assets" / "images" / "layouts" / "layout_temple.png"
    write_png(roleboard_path)
    write_png(layout_path)

    appearance = RoleAppearance(
        id="role_jiangweixi_appearance_base",
        role_id="role_jiangweixi",
        name="base",
        asset_id="role_jiangweixi_base_roleboard",
        asset_path="assets/images/roles/role_jiangweixi_base_roleboard.png",
        subject_element_id="313475623505155",
        subject_element_reference_type="video_refer",
    )
    state.roles["role_jiangweixi"] = Role(
        id="role_jiangweixi",
        name="江未晞",
        intro="体弱但意志强的继任者。",
        appearances={appearance.id: appearance},
    )
    state.layouts["layout_temple"] = Layout(
        id="layout_temple",
        name="殿宇",
        desc="空旷古殿，中央石台有银色能量纹路。",
        prompt="Empty ancient temple hall, stone platform, silver energy lines.",
        asset_path="assets/images/layouts/layout_temple.png",
    )

    panel_path = project_dir / "assets" / "images" / "storyboards" / "panels" / "episode_001_shot_001_storyboard_panel.png"
    write_png(panel_path)
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_temple",
        title="灵魂绑定开场",
        sound_design="低频系统启动声，空旷殿宇回声，九韶冷静VO先于画面响起。",
        camera_shooting_angle="平视略俯，三分之二侧脸角度",
        camera_movement="轻微推近",
        duration_seconds=3,
        dialogue=["九韶：检测到适配宿主，灵魂绑定已完成。"],
        role_ids=["role_jiangweixi"],
        role_appearance_ids=[appearance.id],
        storyboard_panel_asset_id="episode_001_shot_001_storyboard_panel",
        storyboard_panel_asset_path="assets/images/storyboards/panels/episode_001_shot_001_storyboard_panel.png",
        per_second_content=(
            "0-1秒 江未晞在殿宇石台上睁眼，低频系统启动声响起；"
            "1-3秒 她保持侧脸角度听见九韶画外VO，身体虚弱但没有张嘴。"
        ),
        video_prompt=(
            "9:16竖版，<<<element_1>>> 正对镜头突然转头说话，江未晞睁眼，"
            "九韶以画外VO说出“检测到适配宿主，灵魂绑定已完成。”，画面禁止字幕、水印、logo。"
        ),
    )
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[shot])
    router = ProviderRouter(settings)
    provider = router.video("shot", node_name="shot_video_generation")
    workflow = GenerationWorkflow(repo=repo, router=router)

    cleaned = sanitize_video_prompt_text(shot.video_prompt)
    require("9:16" not in cleaned, cleaned)
    require("竖版" not in cleaned, cleaned)
    require("<<<element_1>>>" not in cleaned, cleaned)

    final_prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "kling_video_prompt"
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "final_prompt.txt"
    prompt_path.write_text(final_prompt, encoding="utf-8")

    require("<<<image_1>>>" in final_prompt, final_prompt)
    require("<<<image_2>>>" in final_prompt, final_prompt)
    require("<<<image_3>>>" in final_prompt, final_prompt)
    require("<<<element_1>>>" not in final_prompt, final_prompt)
    require(final_prompt.startswith("素材："), final_prompt)
    require("<<<image_1>>> 是当前镜头故事板" in final_prompt, final_prompt)
    require("<<<image_2>>> 是当前场景三视图/场景图" in final_prompt, final_prompt)
    require("<<<image_3>>> 是当前人物三视图/角色身份板" in final_prompt, final_prompt)
    require("蓝色箭头=摄影机运动" in final_prompt, final_prompt)
    require("紫色标记=情绪/声音/叙事强调" in final_prompt, final_prompt)
    require("最终视频禁止生成任何箭头" in final_prompt, final_prompt)
    require("主视觉风格参考" not in final_prompt, final_prompt)
    require("9:16" not in final_prompt, final_prompt)
    require("竖版" not in final_prompt, final_prompt)
    require("江未晞在殿宇石台上睁眼" in final_prompt, final_prompt)
    require("低频系统启动声" in final_prompt, final_prompt)
    require("九韶画外VO" in final_prompt, final_prompt)
    require("江未晞不张嘴" in final_prompt, final_prompt)
    require("侧角" in final_prompt and "不直视镜头" in final_prompt, final_prompt)

    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
    refs = workflow._shot_video_refs_for_provider(refs, provider=provider)
    ref_asset_types = [ref.metadata.get("asset_type") for ref in refs]
    require(ref_asset_types == ["storyboard_panel", "layout", "roleboard"], str(ref_asset_types))
    require(not any(ref.type == "element" for ref in refs), [ref.model_dump() for ref in refs])
    payload = provider.build_payload(final_prompt, refs=refs, duration=shot.duration_seconds)
    require(payload["sound"] == "on", str(payload))
    require(len(payload["image_list"]) == 3, str(payload))
    require("element_list" not in payload, str(payload))
    require("<<<element_1>>>" not in payload["prompt"], payload["prompt"])
    require("9:16" not in payload["prompt"], payload["prompt"])

    normal_dialogue_shot = StoryboardShot(
        shot_id="episode_001_shot_002",
        index=2,
        layout_id="layout_temple",
        title="江未晞发问",
        sound_design="银色电流、石台低鸣、急促呼吸。",
        camera_shooting_angle="低机位斜仰，侧脸角度",
        camera_movement="轻微推近",
        duration_seconds=3,
        dialogue=["江未晞：灵魂绑定系统？我这是在哪儿？你又是谁？"],
        role_ids=["role_jiangweixi"],
        role_appearance_ids=[appearance.id],
        storyboard_panel_asset_id="episode_001_shot_001_storyboard_panel",
        storyboard_panel_asset_path="assets/images/storyboards/panels/episode_001_shot_001_storyboard_panel.png",
        video_prompt=(
            "低机位斜仰，石台上银白光点旋聚成银发黑袍九韶；"
            "左后方江未晞惊退护身，侧脸望向九韶，口型匹配说出"
            "“灵魂绑定系统？我这是在哪儿？你又是谁？”。"
        ),
    )
    normal_prompt = workflow._shot_video_prompt(
        state,
        StoryboardEpisodeOutput(episode_key="episode_001", shots=[normal_dialogue_shot]),
        normal_dialogue_shot,
        provider=provider,
        project_dir=project_dir,
    )
    require("江未晞说“灵魂绑定系统？我这是在哪儿？你又是谁？”" in normal_prompt, normal_prompt)
    require("画外音：“灵魂绑定系统？我这是在哪儿？你又是谁？”" not in normal_prompt, normal_prompt)

    print("kling_video_prompt_smoke=ok")
    print(f"prompt_path={prompt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
