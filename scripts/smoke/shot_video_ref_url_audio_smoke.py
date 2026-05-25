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
from autodrama.core.schemas import Role, RoleAudio, StoryboardShot  # noqa: E402
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
    project_id = f"shot_video_ref_url_audio_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Video Ref URL Audio Smoke",
        raw_script="林舟在会议室发言。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    ref_frame_path = project_dir / "assets" / "images" / "ref_frames" / "episode_001_shot_001_ref_frame.png"
    role_audio_path = project_dir / "assets" / "audios" / "role_voices" / "role_linz_normal.mp3"
    write_png(ref_frame_path)
    role_audio_path.parent.mkdir(parents=True, exist_ok=True)
    role_audio_path.write_bytes(b"fake role voice audio")

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
        ref_frame_prompt="林舟站在会议室桌边。",
        video_prompt="林舟平静发言，镜头缓慢推进。",
        ref_frame_asset_id="episode_001_shot_001_ref_frame",
        ref_frame_asset_path="assets/images/ref_frames/episode_001_shot_001_ref_frame.png",
        ref_frame_asset_url="https://example.invalid/generated-ref-frame.png",
    )

    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)
    provider = router.video("shot")
    refs = workflow._shot_video_refs(project_dir, state, shot, provider=provider)
    image_refs = [ref for ref in refs if ref.type == "image"]
    audio_refs = [ref for ref in refs if ref.type == "audio"]
    require(len(image_refs) == 1, f"Expected one ref-frame image ref, got {len(image_refs)}")
    require(
        image_refs[0].url == "https://example.invalid/generated-ref-frame.png",
        f"Shot video ref did not prefer saved image URL: {image_refs[0].model_dump()}",
    )
    require(len(audio_refs) == 1, f"Expected one role audio ref, got {len(audio_refs)}")
    require(audio_refs[0].metadata.get("role_id") == "role_linz", "Role audio ref missing role metadata")

    payload = provider.build_payload("测试短视频生成。", refs=refs, duration=6)
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "shot_video_ref_url_audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "payload.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    image_items = [item for item in payload["content"] if item["type"] == "image_url"]
    audio_items = [item for item in payload["content"] if item["type"] == "audio_url"]
    require(image_items[0]["image_url"]["url"] == "https://example.invalid/generated-ref-frame.png", payload["content"])
    require(audio_items[0]["audio_url"]["url"].startswith("data:audio/mpeg;base64,"), payload["content"])

    print("shot_video_ref_url_audio_smoke=ok")
    print(f"payload_path={output_path}")
    print(f"image_url={image_items[0]['image_url']['url']}")
    print(f"audio_refs={len(audio_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
