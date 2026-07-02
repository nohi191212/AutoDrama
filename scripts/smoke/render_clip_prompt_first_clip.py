from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.nodes.storyboard_asset_nodes import ClipPromptNode
from autodrama.workflows.pregen import PregenWorkflow


def default_config_path() -> Path:
    huyao = ROOT / "huyao.yaml"
    if huyao.exists():
        return huyao
    return ROOT / "config.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--project", default="")
    parser.add_argument("--episode", default="")
    parser.add_argument("--clip-key", default="1")
    parser.add_argument(
        "--output",
        default=str(ROOT / ".tmp" / "clip_prompt" / "episode_001_clip_001_rendered_prompt.md"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings)
    workflow = PregenWorkflow(repo=repo, router=router)
    node = ClipPromptNode(
        workflow=workflow,
        repo=repo,
        layout=workflow.layout,
        router=workflow.router,
        script_service=workflow.script_service,
        asset_service=workflow.asset_service,
        script_contents=workflow.script_contents,
        prop_designs=workflow.prop_designs,
        media_store=workflow.media_store,
        logger=workflow.runner.logger,
    )

    project_dir = repo.resolve_active_project_dir(args.project or None)
    state = repo.load_state(project_dir)
    workflow._hydrate_roles_from_design_files(project_dir, state)
    clip_segments_by_episode = node.clip_segments_by_episode(project_dir)
    all_episode_keys = node.expected_episode_keys(state)
    episode_key = args.episode or (all_episode_keys[0] if all_episode_keys else "episode_001")
    source_clips = dict(clip_segments_by_episode.get(episode_key) or {})
    if not source_clips:
        raise FileNotFoundError(f"clip_segment output has no clips for {episode_key}")
    source_key = str(args.clip_key)
    if source_key not in source_clips:
        source_key = node._sorted_clip_segment_keys(source_clips)[0]
    prompt, refs, context = node.render_clip_prompt_request(
        project_dir=project_dir,
        state=state,
        episode_key=episode_key,
        source_key=source_key,
        source_clip=source_clips[source_key],
        source_clips=source_clips,
    )
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    print(f"render_clip_prompt_first_clip: ok")
    print(f"project_dir={project_dir}")
    print(f"episode_key={episode_key}")
    print(f"source_clip_key={source_key}")
    print(f"clip_id={context['clip_id']}")
    print(f"reference_images={len(refs)}")
    print(f"output={output_path}")


if __name__ == "__main__":
    main()
