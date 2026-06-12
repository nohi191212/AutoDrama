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
from autodrama.core.schemas import Layout, Role, RoleAudio, StoryboardEpisodeOutput, StoryboardShot  # noqa: E402
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


class NoAudioReferenceVideoProvider:
    max_reference_images = 4
    max_reference_audio = 1
    max_reference_videos = 0
    supports_audio_references = False


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"shot_video_ref_url_audio_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Video Ref URL Audio Smoke",
        raw_script="林舟在会议室发言。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    layout_image_path = project_dir / "assets" / "images" / "layouts" / "layout_room.png"
    role_audio_path = project_dir / "assets" / "audios" / "role_voices" / "role_linz_normal.mp3"
    write_png(layout_image_path)
    role_audio_path.parent.mkdir(parents=True, exist_ok=True)
    role_audio_path.write_bytes(b"fake role voice audio")

    state.layouts["layout_room"] = Layout(
        id="layout_room",
        name="会议室",
        desc="无人物冷色会议室。",
        prompt="无人物会议室空场景。",
        asset_path="assets/images/layouts/layout_room.png",
        asset_url="https://example.invalid/layout-room.png",
    )
    state.roles["role_linz"] = Role(
        id="role_linz",
        name="林舟",
        intro="年轻律师，声音冷静克制。",
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
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_room",
        title="林舟发言",
        duration_seconds=6,
        role_ids=["role_linz"],
        role_audio_ids=["role_linz_audio_normal"],
        video_prompt="林舟平静发言，镜头缓慢推进。",
    )

    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)
    provider = router.video("shot")
    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider)
    image_refs = [ref for ref in refs if ref.type == "image"]
    audio_refs = [ref for ref in refs if ref.type == "audio"]
    require(len(image_refs) == 1, f"Expected one empty layout image ref, got {len(image_refs)}")
    require(image_refs[0].metadata.get("asset_type") == "layout", image_refs[0].model_dump())
    require(
        image_refs[0].url == "https://example.invalid/layout-room.png",
        f"Shot video layout ref did not prefer saved image URL: {image_refs[0].model_dump()}",
    )
    require(len(audio_refs) == 1, f"Expected one role audio ref, got {len(audio_refs)}")
    require(audio_refs[0].metadata.get("role_id") == "role_linz", "Role audio ref missing role metadata")

    episode = StoryboardEpisodeOutput(episode_key="episode_001", shots=[shot])
    final_prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
    require("图片1" in final_prompt and "场景图，作为空间锚点" in final_prompt, final_prompt)
    require("本段视频参考图，作为本段空间参考锚点" not in final_prompt, final_prompt)
    require("音频1" in final_prompt and "角色说话声音锚点" in final_prompt, final_prompt)
    require("当前 shot 主体视频描述:" in final_prompt, final_prompt)

    no_audio_provider = NoAudioReferenceVideoProvider()
    no_audio_refs = workflow._shot_video_refs_for_provider(refs, provider=no_audio_provider)
    require(not any(ref.type == "audio" for ref in no_audio_refs), [ref.model_dump() for ref in no_audio_refs])
    no_audio_prompt = workflow._shot_video_prompt(
        state,
        episode,
        shot,
        provider=no_audio_provider,
        project_dir=project_dir,
    )
    require("音频1" not in no_audio_prompt, no_audio_prompt)
    require("音频参考" not in no_audio_prompt, no_audio_prompt)

    payload = provider.build_payload("测试短视频生成。", refs=refs, duration=6)
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "shot_video_ref_url_audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "payload.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    image_items = [item for item in payload["content"] if item["type"] == "image_url"]
    audio_items = [item for item in payload["content"] if item["type"] == "audio_url"]
    require(image_items[0]["image_url"]["url"] == "https://example.invalid/layout-room.png", payload["content"])
    require(audio_items[0]["audio_url"]["url"].startswith("data:audio/mpeg;base64,"), payload["content"])

    print("shot_video_ref_url_audio_smoke=ok")
    print(f"payload_path={output_path}")
    print(f"image_url={image_items[0]['image_url']['url']}")
    print(f"audio_refs={len(audio_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
