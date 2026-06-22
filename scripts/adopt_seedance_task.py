from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.ids import normalize_id  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.generation_tasks import (  # noqa: E402
    load_generation_tasks,
    now_iso,
    save_generation_tasks,
    upsert_generation_task,
)


def episode_key(value: str) -> str:
    text = str(value).strip()
    if text.isdigit():
        return f"episode_{int(text):03d}"
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Adopt an existing Seedance video task into generation_tasks.json "
            "without submitting a new generation task."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--project", help="Project id or project directory. Defaults to current_project.json.")
    parser.add_argument("--episode", required=True, help="Episode key or number, e.g. 1 or episode_001.")
    parser.add_argument("--shot", required=True, help="Shot index or shot id, e.g. 1 or episode_001_shot_1.")
    parser.add_argument("--task-id", required=True, help="Existing Seedance task id, e.g. cgt-...")
    return parser


def find_shot(episode, selector: str):
    text = str(selector).strip().lower().replace("-", "_")
    for shot in episode.shots:
        keys = {
            str(shot.index),
            f"{shot.index:03d}",
            str(shot.shot_id).lower().replace("-", "_"),
            f"shot_{shot.index}",
            f"shot_{shot.index:03d}",
            f"{episode.episode_key}_shot_{shot.index}",
            f"{episode.episode_key}_shot_{shot.index:03d}",
        }
        if text in keys:
            return shot
    raise ValueError(f"No shot matched {selector!r} in {episode.episode_key}")


async def main_async() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.resolve_project_dir(args.project) if args.project else repo.resolve_active_project_dir()
    state = repo.load_state(project_dir)
    router = ProviderRouter(settings)
    workflow = GenerationWorkflow(repo=repo, router=router)
    provider = router.video("shot", node_name="shot_video_generation")

    resolved_episode_key = episode_key(args.episode)
    episode = workflow._load_storyboard_episode(project_dir, resolved_episode_key)
    shot = find_shot(episode, args.shot)
    asset_id = normalize_id(f"{shot.shot_id}", "video")
    prompt = str(shot.final_video_prompt or "").strip()
    if not prompt:
        raise ValueError(f"{shot.shot_id} missing final_video_prompt; rerun shot_manifest_generation")
    shot_video_inputs = workflow._shot_video_inputs(project_dir, shot, provider=provider)
    task_key = workflow._shot_video_task_key(episode.episode_key, shot.shot_id)
    planned_asset_path = workflow._project_relative(
        project_dir,
        workflow._video_asset_path(project_dir, "shots", asset_id),
    )

    result = await provider.query_video_task(args.task_id)
    status = (result.task_status or "").strip().lower()
    registry = load_generation_tasks(project_dir, project_id=state.project_id)
    item = workflow._shot_video_task_item(
        task_key=task_key,
        state=state,
        episode_key=episode.episode_key,
        shot=shot,
        asset_id=asset_id,
        asset_path=planned_asset_path,
        prompt=prompt,
        result=result,
        provider=provider,
    )
    item["adopted_at"] = now_iso()
    item["task_id"] = args.task_id
    item["shot_video_inputs"] = shot_video_inputs

    success_statuses = workflow._video_success_statuses(provider)
    asset_path = None
    last_frame_asset_path = None
    if status in success_statuses:
        asset_path = await workflow._write_generated_video(
            project_dir,
            workflow._video_asset_path(project_dir, "shots", asset_id),
            result,
        )
        if asset_path:
            item["asset_path"] = asset_path
            item["completed_at"] = now_iso()
        last_frame_asset_path = await workflow._write_video_last_frame(project_dir, asset_id, result)
        if last_frame_asset_path:
            item["last_frame_asset_path"] = last_frame_asset_path

    upsert_generation_task(registry, item)
    save_generation_tasks(repo, project_dir, registry)
    workflow._apply_shot_video_result(
        shot,
        asset_id=asset_id,
        result=result,
        provider=provider,
        asset_path=asset_path,
        last_frame_asset_path=last_frame_asset_path,
    )
    workflow._save_storyboard_episode(project_dir, episode)

    print(f"project_dir={project_dir}")
    print(f"episode_key={episode.episode_key}")
    print(f"shot_id={shot.shot_id}")
    print(f"task_id={args.task_id}")
    print(f"task_status={result.task_status or '-'}")
    print(f"generation_tasks={project_dir / 'generation_tasks.json'}")
    if asset_path:
        print(f"video_saved={asset_path}")
    else:
        print("video_saved=-")
    if last_frame_asset_path:
        print(f"last_frame_saved={last_frame_asset_path}")
    else:
        print("last_frame_saved=-")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
