from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    project_id = f"dynamic_assets_fake_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Dynamic Assets Fake Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=2,
        episode_duration_seconds=30,
    )
    if project_dir != ROOT_DIR / ".tmp" / "smoke" / project_id:
        raise AssertionError(f"Unexpected project_dir: {project_dir}")

    router = ProviderRouter(settings, provider_override="fake")
    pregen_workflow = PregenWorkflow(repo=repo, router=router)
    state = await pregen_workflow.run(project_dir, until="role_voice_generation", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    state = await generation_workflow.run(project_dir, until="storyboard_generation", only="storyboard_generation")
    checklist_path = project_dir / "generation_checklist.json"
    if not checklist_path.exists():
        raise AssertionError("generation_checklist.json was not created after storyboard generation")

    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    for episode in checklist["episodes"]:
        episode["generate"] = episode["episode_key"] == "episode_001"
    checklist_path.write_text(json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")

    state = await generation_workflow.run(project_dir, until="dynamic_asset_solidification")
    episode_001_shot_path = project_dir / "shots" / "episode_001.json"
    episode_002_shot_path = project_dir / "shots" / "episode_002.json"
    episode_001_shot_text = episode_001_shot_path.read_text(encoding="utf-8")
    episode_002_shot_text = episode_002_shot_path.read_text(encoding="utf-8")

    expected_nodes = {
        "storyboard_generation",
        "ref_frame_generation",
        "shot_video_generation",
        "dynamic_asset_solidification",
    }
    missing_nodes = expected_nodes.difference(state.completed_nodes)
    if missing_nodes:
        raise AssertionError(f"Missing completed nodes: {sorted(missing_nodes)}")
    if '"ref_frame_asset_path"' not in episode_001_shot_text:
        raise AssertionError("episode_001 shot missing ref_frame_asset_path")
    if '"video_asset_path"' not in episode_001_shot_text:
        raise AssertionError("episode_001 shot missing video_asset_path")
    if '"assets/audios/shot_dialogues/' in episode_001_shot_text:
        raise AssertionError("episode_001 generated dialogue audio in the default generation flow")
    if '"assets/images/ref_frames/' in episode_002_shot_text:
        raise AssertionError("episode_002 was generated despite generate=false")
    if '"assets/videos/shots/' in episode_002_shot_text:
        raise AssertionError("episode_002 video was generated despite generate=false")

    required_paths = [
        project_dir / "assets" / "json" / "nodes" / "ref_frame_generation.json",
        project_dir / "assets" / "json" / "nodes" / "shot_video_generation.json",
        project_dir / "assets" / "json" / "nodes" / "dynamic_asset_solidification.json",
        project_dir / "assets" / "json" / "assets" / "dynamic_assets.json",
    ]
    for path in required_paths:
        if not path.exists():
            raise AssertionError(f"Expected output missing: {path}")

    state_payload = json.loads((project_dir / "state.json").read_text(encoding="utf-8"))
    metadata = state_payload.get("metadata", {})
    if "dynamic_assets" in metadata:
        raise AssertionError("state.json metadata should not contain dynamic_assets")

    dynamic_asset_index_path = project_dir / "assets" / "json" / "assets" / "dynamic_assets.json"
    dynamic_asset_index = json.loads(dynamic_asset_index_path.read_text(encoding="utf-8"))
    if dynamic_asset_index.get("schema_version") != 1:
        raise AssertionError("dynamic asset index schema_version should be 1")
    indexed_assets = dynamic_asset_index.get("assets", [])
    if not indexed_assets:
        raise AssertionError("dynamic asset index should contain generated assets")
    if {item.get("episode_key") for item in indexed_assets} != {"episode_001"}:
        raise AssertionError("dynamic asset index should only contain selected episode assets")

    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    for episode in checklist["episodes"]:
        episode["generate"] = episode["episode_key"] == "episode_001"
    checklist_path.write_text(json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")
    await generation_workflow.run(project_dir, until="dynamic_asset_solidification", only="dynamic_asset_solidification")
    rerun_index = json.loads(dynamic_asset_index_path.read_text(encoding="utf-8"))
    rerun_assets = rerun_index.get("assets", [])
    if len(rerun_assets) != len(indexed_assets):
        raise AssertionError("rerunning dynamic_asset_solidification should replace episode assets, not duplicate them")
    if sorted(item.get("asset_id") for item in rerun_assets) != sorted(item.get("asset_id") for item in indexed_assets):
        raise AssertionError("rerunning dynamic_asset_solidification changed the indexed asset set unexpectedly")

    updated_checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    items = {item["episode_key"]: item for item in updated_checklist["episodes"]}
    if items["episode_001"]["generate"]:
        raise AssertionError("episode_001 generate should be reset to false after successful generation")
    if items["episode_001"]["generation_status"] != "completed":
        raise AssertionError("episode_001 checklist status should be completed")
    if "shot_bgm" in items["episode_001"]["node_status"]:
        raise AssertionError("checklist should not track shot_bgm after removing shot_bgm_generation")
    if items["episode_002"]["generate"]:
        raise AssertionError("episode_002 generate should remain false")

    ref_frames = list((project_dir / "assets" / "images" / "ref_frames").glob("*.png"))
    role_images = list((project_dir / "assets" / "images" / "roles").glob("*.png"))
    shot_videos = list((project_dir / "assets" / "videos" / "shots").glob("*.mp4"))
    shot_audios = list((project_dir / "assets" / "audios" / "shot_dialogues").glob("*.*"))
    if not ref_frames:
        raise AssertionError("No ref frame generated")
    if not role_images:
        raise AssertionError("No roleboard image generated")
    if not shot_videos:
        raise AssertionError("No shot video generated")
    if shot_audios:
        raise AssertionError("Shot dialogue audio should not be generated by default")

    print("dynamic_assets_fake_smoke=ok")
    print(f"project_dir={project_dir}")
    print(
        f"role_images={len(role_images)} ref_frames={len(ref_frames)} shot_videos={len(shot_videos)} "
        f"shot_audios={len(shot_audios)}"
    )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
