from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import parse_episode_keys  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardEpisodeOutput, StoryboardShot  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview the final shot video_prompt that would be passed to the video provider. "
            "This script never submits a real video generation task."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml.example"))
    parser.add_argument(
        "--project",
        default=None,
        help="Existing project id or project directory. If omitted, a temporary fake project is created.",
    )
    parser.add_argument("--episode", default="1", help="Episode number/key, for example 1 or episode_001.")
    parser.add_argument("--shot", default="1", help="Shot index/id, for example 1 or episode_001_shot_001.")
    parser.add_argument("--output-dir", default=None, help="Directory for prompt/context preview files.")
    return parser


def episode_key(value: str) -> str:
    parsed = parse_episode_keys(value)
    if not parsed:
        raise ValueError(f"Invalid episode selector: {value}")
    if len(parsed) != 1:
        raise ValueError(f"Expected one episode selector, got: {value}")
    return parsed[0]


def default_output_dir(project_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".tmp" / "smoke" / "video_prompt_preview" / f"{project_id}_{stamp}"


def load_episode(project_dir: Path, key: str) -> StoryboardEpisodeOutput:
    shot_path = project_dir / "shots" / f"{key}.json"
    if not shot_path.exists():
        raise FileNotFoundError(f"Storyboard shot not found: {shot_path}")
    return StoryboardEpisodeOutput.model_validate_json(shot_path.read_text(encoding="utf-8"))


def shot_keys(episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> set[str]:
    shot_id = str(shot.shot_id).lower().replace("-", "_")
    return {
        shot_id,
        str(shot.index),
        f"{shot.index:03d}",
        f"shot_{shot.index}",
        f"shot_{shot.index:03d}",
        f"{episode.episode_key}_shot_{shot.index}",
        f"{episode.episode_key}_shot_{shot.index:03d}",
    }


def select_shot(episode: StoryboardEpisodeOutput, selector: str) -> StoryboardShot:
    normalized = str(selector).strip().lower().replace("-", "_")
    for shot in episode.shots:
        if normalized in shot_keys(episode, shot):
            return shot
    available = ", ".join(f"{shot.index}:{shot.shot_id}" for shot in episode.shots)
    raise ValueError(f"No shot matched selector '{selector}' in {episode.episode_key}. Available: {available}")


async def create_fake_project(settings, repo: ProjectRepository, key: str) -> Path:
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = max(1, int(key.rsplit("_", 1)[-1]))
    project_id = f"video_prompt_preview_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Video Prompt Preview Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。要求画面紧凑，情绪压迫感强。",
        project_id=project_id,
        episode_count=settings.project.episode_count,
        episode_duration_seconds=30,
    )
    router = ProviderRouter(settings, provider_override="fake")
    await PregenWorkflow(repo=repo, router=router).run(project_dir, until="bgm_generation", force=True)
    await GenerationWorkflow(repo=repo, router=router).run(
        project_dir,
        until="storyboard_generation",
        only="storyboard_generation",
        episode_keys=[key],
    )
    return project_dir


def write_preview(
    output_dir: Path,
    *,
    project_dir: Path,
    episode: StoryboardEpisodeOutput,
    shot: StoryboardShot,
    final_prompt: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "final_video_prompt.txt"
    context_path = output_dir / "context.json"
    prompt_path.write_text(final_prompt, encoding="utf-8")
    context: dict[str, Any] = {
        "project_dir": str(project_dir),
        "episode_key": episode.episode_key,
        "shot_id": shot.shot_id,
        "shot_index": shot.index,
        "title": shot.title,
        "duration_seconds": shot.duration_seconds,
        "start_frame_source": shot.start_frame_source,
        "start_frame_inheritance_reason": shot.start_frame_inheritance_reason,
        "layout_id": shot.layout_id,
        "role_ids": shot.role_ids,
        "role_appearance_ids": shot.role_appearance_ids,
        "prop_ids": shot.prop_ids,
        "dialogue": shot.dialogue,
        "raw_shot_video_prompt": shot.video_prompt,
        "final_video_prompt_path": str(prompt_path),
    }
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return prompt_path, context_path


async def main_async(args: argparse.Namespace) -> int:
    key = episode_key(args.episode)
    settings = load_settings(Path(args.config))
    repo = ProjectRepository(settings)

    if args.project:
        project_dir = repo.resolve_active_project_dir(args.project)
        state = repo.load_state(project_dir)
        router = ProviderRouter(settings, provider_override="fake")
    else:
        project_dir = await create_fake_project(settings, repo, key)
        state = repo.load_state(project_dir)
        router = ProviderRouter(settings, provider_override="fake")

    workflow = GenerationWorkflow(repo=repo, router=router)
    workflow._apply_script_plan_settings(state)
    episode = load_episode(project_dir, key)
    shot = select_shot(episode, args.shot)
    provider = router.video("shot")
    final_prompt = workflow._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(state.project_id)
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()
    prompt_path, context_path = write_preview(
        output_dir,
        project_dir=project_dir,
        episode=episode,
        shot=shot,
        final_prompt=final_prompt,
    )

    print("video_prompt_preview_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"episode={episode.episode_key}")
    print(f"shot={shot.shot_id}")
    print(f"prompt_path={prompt_path}")
    print(f"context_path={context_path}")
    print("----- final_video_prompt -----")
    print(final_prompt)
    print("----- end_final_video_prompt -----")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
