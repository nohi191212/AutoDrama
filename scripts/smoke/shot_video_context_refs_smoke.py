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
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
        )
    )


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"shot_video_context_refs_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Video Context Refs Smoke",
        raw_script="林舟在会议室展示证据。赵启站在对面，合同放在桌上。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    ref_frame_path = project_dir / "assets" / "images" / "ref_frames" / "episode_001_shot_002_ref_frame.png"
    role_image_path = project_dir / "assets" / "images" / "roles" / "role_linz_appearance_base.png"
    prop_image_path = project_dir / "assets" / "images" / "props" / "prop_contract.png"
    layout_image_path = project_dir / "assets" / "images" / "layouts" / "layout_room.png"
    role_audio_path = project_dir / "assets" / "audios" / "role_voices" / "role_linz_normal.mp3"
    previous_video_path = project_dir / "assets" / "videos" / "shots" / "episode_001_shot_001.mp4"
    for path in (ref_frame_path, role_image_path, prop_image_path, layout_image_path):
        write_png(path)
    role_audio_path.parent.mkdir(parents=True, exist_ok=True)
    role_audio_path.write_bytes(b"fake role voice audio")
    previous_video_path.parent.mkdir(parents=True, exist_ok=True)
    previous_video_path.write_bytes(b"fake previous shot video")

    state.layouts["layout_room"] = Layout(
        id="layout_room",
        name="会议室",
        desc="冷色会议室。",
        prompt="会议室空场景。",
        asset_path="assets/images/layouts/layout_room.png",
        asset_url="https://example.invalid/layout.png",
    )
    state.roles["role_linz"] = Role(
        id="role_linz",
        name="林舟",
        intro="年轻律师，声音冷静克制。",
        appearances={
            "base": RoleAppearance(
                id="role_linz_appearance_base",
                role_id="role_linz",
                name="base",
                asset_path="assets/images/roles/role_linz_appearance_base.png",
                asset_url="https://example.invalid/role-linz.png",
            )
        },
        audio={
            "normal": RoleAudio(
                id="role_linz_audio_normal",
                role_id="role_linz",
                emotion="normal",
                asset_id="role_linz_audio_normal",
                asset_path="assets/audios/role_voices/role_linz_normal.mp3",
                voice_type="zh_male_m191_uranus_bigtts",
                voice_name="Dummy Male",
            )
        },
    )
    state.props["prop_contract"] = Prop(
        id="prop_contract",
        name="合同",
        desc="带红色骑缝章的合同。",
        asset_path="assets/images/props/prop_contract.png",
        asset_url="https://example.invalid/contract.png",
    )

    previous_shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_room",
        title="上一片段",
        duration_seconds=6,
        role_ids=["role_linz"],
        ref_frame_prompt="林舟站在桌边。",
        video_prompt="林舟看向桌面合同。",
        physical_space_key="layout_room::main_table",
        video_asset_id="video_episode_001_shot_001",
        video_asset_path="assets/videos/shots/episode_001_shot_001.mp4",
    )
    current_shot = StoryboardShot(
        shot_id="episode_001_shot_002",
        index=2,
        layout_id="layout_room",
        title="展示证据",
        duration_seconds=6,
        role_ids=["role_linz"],
        role_audio_ids=["role_linz_audio_normal"],
        prop_ids=["prop_contract"],
        ref_frame_prompt="林舟把合同推向镜头。",
        video_prompt="林舟平静展示合同，镜头从桌面推向他的脸。",
        ref_frame_asset_id="episode_001_shot_002_ref_frame",
        ref_frame_asset_path="assets/images/ref_frames/episode_001_shot_002_ref_frame.png",
        ref_frame_asset_url="https://example.invalid/ref-frame.png",
        physical_space_key="layout_room::main_table",
    )
    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[previous_shot, current_shot])

    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)
    provider = router.video("shot")
    refs = workflow._shot_video_refs(project_dir, state, current_shot, provider=provider, episode=episode)
    asset_types = [str(ref.metadata.get("asset_type") or "") for ref in refs]

    require(
        asset_types == ["ref_frame", "role_appearance", "prop", "role_audio", "reference_and_previous_shot_video"],
        asset_types,
    )
    require(all(item != "layout" for item in asset_types), f"Layout should not be sent in context mode: {asset_types}")
    require(refs[0].url == "https://example.invalid/ref-frame.png", "Ref frame URL should be preferred")
    require(refs[1].url == "https://example.invalid/role-linz.png", "Role image URL should be preferred")
    require(refs[2].url == "https://example.invalid/contract.png", "Prop image URL should be preferred")
    require(refs[3].metadata.get("reference_source") == "storyboard_role_audio_ids", "Role audio source mismatch")
    require(
        refs[4].metadata.get("reference_source") == "nearest_same_scene_is_previous_shot",
        "Shared reference/previous video source mismatch",
    )
    require(refs[4].metadata.get("reference_roles") == ["scene_consistency", "previous_shot_continuity"], refs[4].metadata)

    payload = provider.build_payload("测试视频参考组合。", refs=refs, duration=6)
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "shot_video_context_refs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "payload.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    image_items = [item for item in payload["content"] if item["type"] == "image_url"]
    audio_items = [item for item in payload["content"] if item["type"] == "audio_url"]
    video_items = [item for item in payload["content"] if item["type"] == "video_url"]
    require([item["image_url"]["url"] for item in image_items] == [
        "https://example.invalid/ref-frame.png",
        "https://example.invalid/role-linz.png",
        "https://example.invalid/contract.png",
    ], payload["content"])
    require(len(audio_items) == 1, payload["content"])
    require(audio_items[0]["audio_url"]["url"].startswith("data:audio/mpeg;base64,"), payload["content"])
    require(len(video_items) == 1, payload["content"])
    require(video_items[0]["video_url"]["url"].startswith("data:video/mp4;base64,"), payload["content"])

    print("shot_video_context_refs_smoke=ok")
    print(f"payload_path={output_path}")
    print("refs=ref_frame,role_appearance,prop,role_audio,reference_and_previous_shot_video")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
