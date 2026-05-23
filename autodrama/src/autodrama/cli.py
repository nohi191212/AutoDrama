from __future__ import annotations

import argparse
import asyncio
import json

from autodrama.config import load_settings
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.generation import DEFAULT_GENERATION_NODES, GENERATION_NODES, GenerationWorkflow
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow
from autodrama.workflows.selection import (
    parse_episode_keys as parse_episode_keys_value,
    parse_shot_selectors as parse_shot_selectors_value,
)


def parse_episode_keys(value: str | None) -> list[str] | None:
    return parse_episode_keys_value(value)


def parse_shot_selectors(value: str | None) -> list[str] | None:
    return parse_shot_selectors_value(value)


def build_parser() -> argparse.ArgumentParser:
    pregen_only_choices = [*PREGEN_NODES]
    if "prop_image_generation" not in pregen_only_choices:
        pregen_only_choices.append("prop_image_generation")

    parser = argparse.ArgumentParser(prog="autodrama")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a project directory")
    init_parser.add_argument("--config", required=True)
    init_parser.add_argument("--title")
    init_parser.add_argument("--script-file")
    init_parser.add_argument("--project-id")

    run_parser = subparsers.add_parser("run", help="Run a workflow")
    run_subparsers = run_parser.add_subparsers(dest="workflow", required=True)
    pregen_parser = run_subparsers.add_parser("pregen", help="Run pre-generation nodes")
    pregen_parser.add_argument("--config", required=True)
    pregen_parser.add_argument("--project")
    pregen_parser.add_argument("--until", choices=PREGEN_NODES, default=PREGEN_NODES[-1])
    pregen_parser.add_argument(
        "--only",
        "--node",
        dest="only",
        choices=pregen_only_choices,
        help="Run exactly one pre-generation node, even if it is already completed.",
    )
    pregen_parser.add_argument(
        "--episodes",
        "--episode",
        dest="episodes",
        help=(
            "Supported with pregen --only role_design, role_voice_generation, role_full_body_generation, "
            "role_multiview_generation, role_intro_video_generation, prop_design, or prop_generation "
            "(legacy alias: prop_image_generation)."
        ),
    )
    pregen_parser.add_argument("--provider", choices=["fake", "configured"], default="configured")
    pregen_parser.add_argument("--force", action="store_true")

    generation_parser = run_subparsers.add_parser("generation", help="Run dynamic shot-level asset generation")
    generation_parser.add_argument("--config", required=True)
    generation_parser.add_argument("--project")
    generation_parser.add_argument("--until", choices=GENERATION_NODES, default=DEFAULT_GENERATION_NODES[-1])
    generation_parser.add_argument(
        "--only",
        "--node",
        dest="only",
        choices=GENERATION_NODES,
        help="Run exactly one dynamic generation node.",
    )
    generation_parser.add_argument(
        "--episodes",
        help="Comma-separated episode keys or numbers to generate, overriding generation_checklist.json.",
    )
    generation_parser.add_argument(
        "--shots",
        help="Comma-separated shot indexes or ids to generate inside selected episodes, for example 1-3 or episode_001_shot_1.",
    )
    generation_parser.add_argument("--provider", choices=["fake", "configured"], default="configured")
    generation_parser.add_argument("--force", action="store_true")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a project")
    inspect_subparsers = inspect_parser.add_subparsers(dest="target", required=True)
    state_parser = inspect_subparsers.add_parser("state", help="Print state summary")
    state_parser.add_argument("--config", required=True)
    state_parser.add_argument("--project")
    nodes_parser = inspect_subparsers.add_parser("nodes", help="List node JSON outputs")
    nodes_parser.add_argument("--config", required=True)
    nodes_parser.add_argument("--project")

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config(
        title=args.title,
        script_file=args.script_file,
        project_id=args.project_id,
    )
    logger = setup_logging(project_dir)
    state = repo.load_state(project_dir)
    logger.info("project initialized project_id=%s project_dir=%s", state.project_id, project_dir)
    print(json.dumps({"project_id": state.project_id, "project_dir": str(project_dir)}, ensure_ascii=False, indent=2))
    return 0


async def cmd_run_pregen(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    try:
        project_dir = repo.resolve_active_project_dir(args.project)
    except ValueError:
        project_dir = repo.create_project_from_config(project_id=args.project)

    if not (project_dir / "state.json").exists():
        project_dir = repo.create_project_from_config(project_id=args.project)

    provider_override = "fake" if args.provider == "fake" else None
    router = ProviderRouter(settings, provider_override=provider_override)
    workflow = PregenWorkflow(repo=repo, router=router)
    state = await workflow.run(
        project_dir,
        until=args.until,
        force=args.force,
        only=args.only,
        episode_keys=parse_episode_keys(args.episodes),
    )
    get_logger().info(
        "run summary project_id=%s current_node=%s role_count=%d project_dir=%s",
        state.project_id,
        state.current_node,
        len(state.roles),
        project_dir,
    )
    print(
        json.dumps(
            {
                "project_id": state.project_id,
                "current_node": state.current_node,
                "completed_nodes": state.completed_nodes,
                "role_count": len(state.roles),
                "project_dir": str(project_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def cmd_run_generation(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.resolve_active_project_dir(args.project)
    episode_keys = parse_episode_keys(args.episodes)

    provider_override = "fake" if args.provider == "fake" else None
    router = ProviderRouter(settings, provider_override=provider_override)
    workflow = GenerationWorkflow(repo=repo, router=router)
    state = await workflow.run(
        project_dir,
        until=args.until,
        force=args.force,
        episode_keys=episode_keys,
        only=args.only,
        shot_selectors=parse_shot_selectors(args.shots),
    )
    get_logger().info(
        "run summary workflow=generation project_id=%s current_node=%s project_dir=%s",
        state.project_id,
        state.current_node,
        project_dir,
    )
    print(
        json.dumps(
            {
                "project_id": state.project_id,
                "current_node": state.current_node,
                "completed_nodes": state.completed_nodes,
                "project_dir": str(project_dir),
                "generation_checklist": str(project_dir / "generation_checklist.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_inspect_state(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.resolve_active_project_dir(args.project)
    state = repo.load_state(project_dir)
    print(
        json.dumps(
            {
                "project_id": state.project_id,
                "title": state.title,
                "current_node": state.current_node,
                "completed_nodes": state.completed_nodes,
                "roles": {
                    role_id: {
                        "name": role.name,
                        "audio_emotions": list(role.audio.keys()),
                        "audio_assets": {
                            emotion: {
                                "voice": audio.asset_id,
                                "path": audio.asset_path,
                            }
                            for emotion, audio in role.audio.items()
                        },
                    }
                    for role_id, role in state.roles.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_inspect_nodes(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.resolve_active_project_dir(args.project)
    node_dir = project_dir / "assets" / "json" / "nodes"
    outputs = sorted(str(path.relative_to(project_dir)) for path in node_dir.glob("*.json"))
    state = repo.load_state(project_dir)
    print(json.dumps({"project_id": state.project_id, "node_outputs": outputs}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "run" and args.workflow == "pregen":
        return asyncio.run(cmd_run_pregen(args))
    if args.command == "run" and args.workflow == "generation":
        return asyncio.run(cmd_run_generation(args))
    if args.command == "inspect" and args.target == "state":
        return cmd_inspect_state(args)
    if args.command == "inspect" and args.target == "nodes":
        return cmd_inspect_nodes(args)

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
