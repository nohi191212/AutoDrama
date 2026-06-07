from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


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
    previous_ref_frame_path = (
        project_dir / "assets" / "images" / "ref_frames" / "episode_001_shot_001_ref_frame.png"
    )
    role_image_path = project_dir / "assets" / "images" / "roles" / "role_linz_appearance_base.png"
    role_full_body_path = project_dir / "assets" / "images" / "roles" / "role_linz_appearance_base_full_body.png"
    prop_image_path = project_dir / "assets" / "images" / "props" / "prop_contract.png"
    layout_image_path = project_dir / "assets" / "images" / "layouts" / "layout_room.png"
    role_audio_path = project_dir / "assets" / "audios" / "role_voices" / "role_linz_normal.mp3"
    zhao_audio_path = project_dir / "assets" / "audios" / "role_voices" / "role_zhao_normal.mp3"
    role_intro_video_path = project_dir / "assets" / "videos" / "roles" / "role_linz_appearance_base_intro.mp4"
    zhao_intro_video_path = project_dir / "assets" / "videos" / "roles" / "role_zhao_appearance_base_intro.mp4"
    previous_video_path = project_dir / "assets" / "videos" / "shots" / "episode_001_shot_001.mp4"
    for path in (
        ref_frame_path,
        previous_ref_frame_path,
        role_image_path,
        role_full_body_path,
        prop_image_path,
        layout_image_path,
    ):
        write_png(path)
    role_audio_path.parent.mkdir(parents=True, exist_ok=True)
    role_audio_path.write_bytes(b"fake role voice audio")
    zhao_audio_path.write_bytes(b"fake zhao voice audio")
    role_intro_video_path.parent.mkdir(parents=True, exist_ok=True)
    role_intro_video_path.write_bytes(b"fake role intro video")
    zhao_intro_video_path.write_bytes(b"fake zhao intro video")
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
                full_body_image_asset_id="role_linz_appearance_base_full_body",
                full_body_image_asset_path="assets/images/roles/role_linz_appearance_base_full_body.png",
                full_body_image_asset_url="https://example.invalid/role-linz-full-body.png",
                intro_video_asset_id="role_linz_appearance_base_intro",
                intro_video_asset_path="assets/videos/roles/role_linz_appearance_base_intro.mp4",
                intro_video_asset_url="https://example.invalid/role-linz-intro.mp4",
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
    state.roles["role_zhao"] = Role(
        id="role_zhao",
        name="赵启",
        intro="强势主管，声音压迫感强。",
        appearances={
            "base": RoleAppearance(
                id="role_zhao_appearance_base",
                role_id="role_zhao",
                name="base",
                asset_path="assets/images/roles/role_zhao_appearance_base.png",
                asset_url="https://example.invalid/role-zhao.png",
                intro_video_asset_id="role_zhao_appearance_base_intro",
                intro_video_asset_path="assets/videos/roles/role_zhao_appearance_base_intro.mp4",
                intro_video_asset_url="https://example.invalid/role-zhao-intro.mp4",
            )
        },
        audio={
            "normal": RoleAudio(
                id="role_zhao_audio_normal",
                role_id="role_zhao",
                emotion="normal",
                asset_id="role_zhao_audio_normal",
                asset_path="assets/audios/role_voices/role_zhao_normal.mp3",
                voice_type="zh_male_m191_uranus_bigtts",
                voice_name="Dummy Zhao",
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
        ref_frame_asset_id="episode_001_shot_001_ref_frame",
        ref_frame_asset_path="assets/images/ref_frames/episode_001_shot_001_ref_frame.png",
        ref_frame_asset_url="https://example.invalid/previous-ref-frame.png",
        video_asset_id="video_episode_001_shot_001",
        video_asset_path="assets/videos/shots/episode_001_shot_001.mp4",
        video_raw_response={
            "content": {
                "video_url": "https://example.invalid/episode_001_shot_001.mp4",
            },
        },
    )
    current_shot = StoryboardShot(
        shot_id="episode_001_shot_002",
        index=2,
        layout_id="layout_room",
        title="展示证据",
        duration_seconds=6,
        role_ids=["role_linz", "role_zhao"],
        role_appearance_ids=["role_linz_appearance_base"],
        role_audio_ids=["role_zhao_audio_normal"],
        prop_ids=["prop_contract"],
        dialogue=["赵启（VO）：这份证据没有意义。"],
        ref_frame_prompt="林舟把合同推向镜头。",
        video_prompt="林舟平静展示合同，镜头从桌面推向他的脸。赵启的画外音说：“这份证据没有意义。”",
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
        asset_types == ["ref_frame", "previous_shot_last_ref_frame", "role_audio"],
        asset_types,
    )
    require(refs[0].url == "https://example.invalid/ref-frame.png", "Ref frame URL should be preferred")
    require(refs[0].metadata.get("reference_source") == "seedream_url", "Ref frame source mismatch")
    require(refs[1].url == "https://example.invalid/previous-ref-frame.png", "Previous ref frame URL should be preferred")
    require(
        refs[1].metadata.get("reference_source") == "previous_shot_ref_frame_url",
        "Previous ref frame source mismatch",
    )
    require(refs[1].metadata.get("seedance_role") == "reference_image", "Previous ref frame should be reference image")
    require(refs[1].metadata.get("previous_shot_id") == "episode_001_shot_001", "Previous shot id mismatch")
    require(refs[2].metadata.get("reference_source") == "storyboard_role_audio_ids", "Role audio source mismatch")
    require(refs[2].metadata.get("role_id") == "role_zhao", "Voiceover speaker audio should be selected")
    require(all(item != "previous_shot_video" for item in asset_types), f"Previous video should not be sent: {asset_types}")
    require(all(item != "role_intro_video" for item in asset_types), f"Role intro video should not be sent: {asset_types}")
    require(all(item != "role_appearance" for item in asset_types), f"Old role design image should not be sent: {asset_types}")
    require(all(item != "prop" for item in asset_types), f"Prop image should not be sent: {asset_types}")
    require(
        all(ref.url != "https://example.invalid/role-zhao-intro.mp4" for ref in refs),
        "Voiceover speaker intro video should not be selected when another role is the visual subject",
    )

    final_prompt = workflow._shot_video_prompt(state, episode, current_shot, provider=provider, project_dir=project_dir)
    require("Seedance ref_frame_only 参考图处理要求" in final_prompt, final_prompt)
    require("图片1（episode_001_shot_002_ref_frame）" in final_prompt, final_prompt)
    require("本段视频参考图，作为本段空间参考锚点" in final_prompt, final_prompt)
    require("图片2（episode_001_shot_001 最后参考帧）" in final_prompt, final_prompt)
    require("上一 shot 最后一帧提示图，由上一 shot 的 ref_frame 提供" in final_prompt, final_prompt)
    require("上一 shot 最后参考帧只提示硬切前状态，不作为当前片段首帧" in final_prompt, final_prompt)
    require("上一 shot 最后参考帧只保留连续性提示" in final_prompt, final_prompt)
    require("上一 shot 镜头视频，作为逻辑连贯性锚点" not in final_prompt, final_prompt)
    require("上一镜预滚与硬切结构" not in final_prompt, final_prompt)
    first_prompt = workflow._shot_video_prompt(state, episode, previous_shot, provider=provider, project_dir=project_dir)
    require("上一镜预滚与硬切结构" not in first_prompt, first_prompt)
    require("本片段按硬切进入当前画面" in first_prompt, first_prompt)
    require("音频1（赵启）" in final_prompt and "角色说话声音锚点" in final_prompt, final_prompt)
    require("说话人音频绑定: 赵启 的音频只绑定当前 dialogue/video_prompt 中的说话人" in final_prompt, final_prompt)
    require("人物朝向约束" in final_prompt and "不要呈现证件照式、完全正对镜头" in final_prompt, final_prompt)
    require("对白空间约束" in final_prompt, final_prompt)
    require("赵启的台词“这份证据没有意义。”是画外音/VO" in final_prompt, final_prompt)
    require("不要让林舟张嘴、对口型或用赵启的声音说这句台词" in final_prompt, final_prompt)
    require("人物设计图，作为角色静态参考锚点" not in final_prompt, final_prompt)
    require("道具设计图，作为道具静态参考锚点" not in final_prompt, final_prompt)

    full_reference_provider = SimpleNamespace(
        settings=SimpleNamespace(options={"video_reference_mode": "full"}),
        reference_video_requires_web_url=True,
        max_reference_images=9,
        max_reference_audio=3,
        max_reference_videos=3,
    )
    full_refs = workflow._shot_video_refs(
        project_dir,
        state,
        current_shot,
        provider=full_reference_provider,
        episode=episode,
    )
    full_asset_types = [str(ref.metadata.get("asset_type") or "") for ref in full_refs]
    require(
        full_asset_types == ["role_full_body", "layout", "role_intro_video", "role_audio"],
        full_asset_types,
    )
    full_body_ref = next(ref for ref in full_refs if ref.metadata.get("asset_type") == "role_full_body")
    require(full_body_ref.url == "https://example.invalid/role-linz-full-body.png", full_body_ref)
    role_intro_ref = next(ref for ref in full_refs if ref.metadata.get("asset_type") == "role_intro_video")
    require(role_intro_ref.url == "https://example.invalid/role-linz-intro.mp4", role_intro_ref)
    require(
        all(ref.url != "https://example.invalid/role-zhao-intro.mp4" for ref in full_refs),
        "Full refs should still select the visual subject intro, not the VO speaker intro",
    )
    full_prompt = workflow._shot_video_prompt(
        state,
        episode,
        current_shot,
        provider=full_reference_provider,
        project_dir=project_dir,
    )
    require("图片1（林舟）" in full_prompt and "画面中央人物全身图" in full_prompt, full_prompt)
    require("图片2（会议室）" in full_prompt and "场景图，作为空间锚点" in full_prompt, full_prompt)
    require("本段视频参考图，作为本段空间参考锚点" not in full_prompt, full_prompt)
    require("人物设计图，作为角色静态参考锚点" not in full_prompt, full_prompt)
    require("道具设计图，作为道具静态参考锚点" not in full_prompt, full_prompt)
    require("视频1（林舟）" in full_prompt and "画面中央人物 intro video" in full_prompt, full_prompt)
    require("音频1（赵启）" in full_prompt and "角色说话声音锚点" in full_prompt, full_prompt)
    require("上一镜预滚与硬切结构" not in full_prompt, full_prompt)

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
        "https://example.invalid/previous-ref-frame.png",
    ], payload["content"])
    require([item["role"] for item in image_items] == ["reference_image", "reference_image"], payload["content"])
    require(len(audio_items) == 1, payload["content"])
    require(audio_items[0]["audio_url"]["url"].startswith("data:audio/mpeg;base64,"), payload["content"])
    require(len(video_items) == 0, payload["content"])

    intro_output_path = project_dir / "assets" / "json" / "nodes" / "role_intro_video_generation.json"
    intro_output_path.parent.mkdir(parents=True, exist_ok=True)
    intro_output_path.write_text(
        json.dumps(
            {
                "generated_assets": [
                    {
                        "asset_id": "role_linz_appearance_base_intro",
                        "raw_response": {"duration": 8},
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    previous_shot.video_raw_response["duration"] = 8
    capped_refs = workflow._shot_video_refs(project_dir, state, current_shot, provider=provider, episode=episode)
    capped_asset_types = [str(ref.metadata.get("asset_type") or "") for ref in capped_refs]
    require(
        capped_asset_types == ["ref_frame", "previous_shot_last_ref_frame", "role_audio"],
        capped_asset_types,
    )
    capped_prompt = workflow._shot_video_prompt(state, episode, current_shot, provider=provider, project_dir=project_dir)
    require("本段视频参考图，作为本段空间参考锚点" in capped_prompt, capped_prompt)
    require("上一 shot 最后一帧提示图，由上一 shot 的 ref_frame 提供" in capped_prompt, capped_prompt)
    require("上一 shot 镜头视频，作为逻辑连贯性锚点" not in capped_prompt, capped_prompt)
    require("上一镜预滚与硬切结构" not in capped_prompt, capped_prompt)

    print("shot_video_context_refs_smoke=ok")
    print(f"payload_path={output_path}")
    print("refs=ref_frame,previous_shot_last_ref_frame,role_audio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
