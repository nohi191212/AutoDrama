from __future__ import annotations

import argparse
import asyncio
import json
import sys

from autodrama.config import load_settings
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.workflows.generation import DEFAULT_GENERATION_NODES, GENERATION_NODES, GenerationWorkflow
from autodrama.workflows.pregen import PREGEN_NODE_GROUPS, PREGEN_NODES, PREGEN_ONLY_NODES, PregenWorkflow
from autodrama.workflows.postgen import POSTGEN_NODES, PostgenWorkflow
from autodrama.workflows.selection import (
    parse_clip_selectors as parse_clip_selectors_value,
    parse_episode_keys as parse_episode_keys_value,
    parse_shot_selectors as parse_shot_selectors_value,
)


def parse_episode_keys(value: str | None) -> list[str] | None:
    return parse_episode_keys_value(value)


def parse_shot_selectors(value: str | None) -> list[str] | None:
    return parse_shot_selectors_value(value)


def parse_clip_selectors(value: str | None) -> list[str] | None:
    return parse_clip_selectors_value(value)


def parse_role_names(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def parse_asset_ids(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def build_parser() -> argparse.ArgumentParser:
    pregen_only_choices = [*PREGEN_ONLY_NODES]

    parser = argparse.ArgumentParser(prog="autodrama")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a project directory")
    init_parser.add_argument("--config", required=True)
    init_parser.add_argument("--title")
    init_parser.add_argument("--chapters-dir")
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
        help=(
            "Run exactly one pre-generation node, even if it is already completed. "
            "Optional ambient entity, role subject, voice, and BGM nodes are available through --only."
        ),
    )
    pregen_parser.add_argument(
        "--node_group",
        "--node-group",
        dest="node_group",
        choices=sorted(PREGEN_NODE_GROUPS),
        help=(
            "Run a predefined pre-generation node group, such as key_vision, role_extract, "
            "prop_layout_extract, roleboard_gen, prop_gen, or layout_gen."
        ),
    )
    pregen_parser.add_argument(
        "--episodes",
        "--episode",
        dest="episodes",
        help=(
            "Supported with pregen --only clip_segment, roleboard_prompt, roleboard_image_generation, "
            "clip_to_shots, scene_multiview_plan, scene_multiview_image_generation, "
            "layout_to_background_prompt, shot_background_image_generation, shot_blocking_plan, "
            "shot_blocking_control_render, shot_keyframe_prompt, shot_keyframe_stage_generation, "
            "shot_keyframe_image_generation, shot_manifest_generation, "
            "role_subject_frontal_image_generation, role_kling_voice_generation, "
            "role_subject_video_generation, role_subject_element_generation, role_voice_select, "
            "prop_prompt, prop_image_generation, or layout_image_generation."
        ),
    )
    pregen_parser.add_argument(
        "--roles",
        "--role",
        dest="roles",
        help=(
            "Comma-separated role names or role ids to regenerate with pregen --only role_voice_select, "
            "for example 九韶 or role_jiushao."
        ),
    )
    pregen_parser.add_argument(
        "--clips",
        "--clip",
        dest="clips",
        help=(
            "Comma-separated clip indexes or ids to regenerate with pregen --only "
            "clip_to_shots. "
            "Supports ranges such as 2,5-7 or ids such as episode_001_clip_005."
        ),
    )
    pregen_parser.add_argument(
        "--shots",
        help="Comma-separated shot indexes or ids for shot-scoped spatial, background, blocking, keyframe, or manifest pregen nodes.",
    )
    pregen_parser.add_argument(
        "--assets",
        help=(
            "Comma-separated image asset ids for a precise --only image-generation, key_vision_edit, or image-audit repair run. "
            "Selected assets are regenerated without rerunning unrelated assets."
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
        "--episode",
        dest="episodes",
        help="Comma-separated episode keys or numbers to generate, overriding generation_checklist.json.",
    )
    generation_parser.add_argument(
        "--shots",
        help=(
            "Comma-separated shot indexes or ids to generate inside selected episodes. "
            "Supported by shot_video_generation and dynamic_asset_solidification, "
            "for example 1-3 or episode_001_shot_1."
        ),
    )
    generation_parser.add_argument("--provider", choices=["fake", "configured"], default="configured")
    generation_parser.add_argument("--force", action="store_true")

    postgen_parser = run_subparsers.add_parser("postgen", help="Run post-generation editing/composition")
    postgen_parser.add_argument("--config", required=True)
    postgen_parser.add_argument("--project")
    postgen_parser.add_argument("--until", choices=POSTGEN_NODES, default=POSTGEN_NODES[-1])
    postgen_parser.add_argument(
        "--only",
        "--node",
        dest="only",
        choices=POSTGEN_NODES,
        help="Run exactly one post-generation node.",
    )
    postgen_parser.add_argument(
        "--episodes",
        "--episode",
        dest="episodes",
        help="Comma-separated episode keys or numbers to post-process.",
    )
    postgen_parser.add_argument(
        "--shots",
        help="Comma-separated shot indexes or ids to include in the postgen source clip set.",
    )
    postgen_parser.add_argument("--provider", choices=["fake", "configured"], default="configured")
    postgen_parser.add_argument("--force", action="store_true")

    catalog_parser = subparsers.add_parser("voice-catalog", help="Build or inspect reusable provider voice catalogs")
    catalog_subparsers = catalog_parser.add_subparsers(dest="action", required=True)
    catalog_build_parser = catalog_subparsers.add_parser("build", help="Create or refresh a voice catalog manifest")
    catalog_build_parser.add_argument("--config", required=True)
    catalog_build_parser.add_argument("--provider", default="configured")
    catalog_build_parser.add_argument("--force-manifest", action="store_true")
    catalog_build_parser.add_argument("--force-samples", action="store_true")
    profile_group = catalog_build_parser.add_mutually_exclusive_group()
    profile_group.add_argument(
        "--force-profiles",
        action="store_true",
        help="Regenerate voice profiles for selected voices even when matching profiles exist.",
    )
    profile_group.add_argument(
        "--miss-profiles",
        action="store_true",
        help="Build only missing or stale voice profiles; existing matching profiles are reused.",
    )
    catalog_build_parser.add_argument(
        "--judge-provider",
        help=(
            "Override the audio judge provider for --force-profiles/--miss-profiles. "
            "Defaults to routing.judge.voice_catalog_profile."
        ),
    )
    catalog_build_parser.add_argument("--voice-type", action="append", dest="voice_types")
    catalog_build_parser.add_argument(
        "--sample-emotion",
        action="append",
        dest="sample_emotions",
        help=(
            "Sample emotion(s) to build. Repeat or comma-separate values. "
            "Default is normal. Use 'all' for normal,angry,sad,happy,low."
        ),
    )
    catalog_build_parser.add_argument("--limit", type=int, help="Limit build work to the first N voices in the manifest")
    catalog_inspect_parser = catalog_subparsers.add_parser("inspect", help="Print voice catalog summary")
    catalog_inspect_parser.add_argument("--config", required=True)
    catalog_inspect_parser.add_argument("--provider", default="configured")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a project")
    inspect_subparsers = inspect_parser.add_subparsers(dest="target", required=True)
    state_parser = inspect_subparsers.add_parser("state", help="Print state summary")
    state_parser.add_argument("--config", required=True)
    state_parser.add_argument("--project")
    nodes_parser = inspect_subparsers.add_parser("nodes", help="List node JSON outputs")
    nodes_parser.add_argument("--config", required=True)
    nodes_parser.add_argument("--project")

    return parser


def _catalog_speech_provider(settings, provider_name: str):
    if provider_name in {"kling", "kling_omni", "kling_video"}:
        router = ProviderRouter(settings, provider_override="kling")
        return router.video("shot")
    override = None if provider_name == "configured" else provider_name
    router = ProviderRouter(settings, provider_override=override)
    return router.audio("speech")


def _catalog_judge_provider(settings, *, speech_provider_name: str, judge_provider_name: str | None):
    if judge_provider_name:
        override = None if judge_provider_name == "configured" else judge_provider_name
    elif speech_provider_name == "fake":
        override = "fake"
    else:
        override = None
    router = ProviderRouter(settings, provider_override=override)
    return router.judge("voice_catalog_profile")


def _parse_sample_emotions(values: list[str] | None) -> list[str]:
    if not values:
        return list(VoiceCatalogRepository.DEFAULT_SAMPLE_EMOTIONS)

    emotions: list[str] = []
    for raw_value in values:
        for item in str(raw_value or "").split(","):
            emotion = item.strip().lower()
            if not emotion:
                continue
            if emotion == "all":
                for full_emotion in VoiceCatalogRepository.FULL_SAMPLE_EMOTIONS:
                    if full_emotion not in emotions:
                        emotions.append(full_emotion)
                continue
            if emotion not in emotions:
                emotions.append(emotion)
    return emotions or list(VoiceCatalogRepository.DEFAULT_SAMPLE_EMOTIONS)


async def cmd_voice_catalog_build(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    provider = _catalog_speech_provider(settings, args.provider)
    catalog_repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(catalog_repo)
    is_official_preset_catalog = callable(getattr(provider, "list_preset_voices", None))
    if is_official_preset_catalog:
        manifest = await service.sync_official_preset_catalog(
            provider,
            refresh_manifest=args.force_manifest,
            force_samples=args.force_samples,
            download_trials=True,
        )
    else:
        manifest = service.load_or_bootstrap_manifest(provider, force_bootstrap=args.force_manifest)
    sample_emotions = ["normal"] if is_official_preset_catalog else _parse_sample_emotions(args.sample_emotions)
    if not is_official_preset_catalog:
        manifest = service.with_sample_emotions(manifest, sample_emotions)
        catalog_repo.save_manifest(manifest)
    voice_types = set(args.voice_types or [])
    if args.limit is not None and args.limit > 0:
        limited = {voice.voice_type for voice in manifest.voices[: args.limit]}
        voice_types = voice_types.intersection(limited) if voice_types else limited
    voice_types_arg = voice_types or None
    samples_built = is_official_preset_catalog and any(voice.samples for voice in manifest.voices)
    profiles_built = False
    if args.force_samples and not is_official_preset_catalog:
        manifest = await service.build_samples(
            provider,
            manifest,
            force_samples=True,
            voice_types=voice_types_arg,
            sample_emotions=sample_emotions,
        )
        samples_built = True
    if args.force_profiles or args.miss_profiles:
        judge = _catalog_judge_provider(
            settings,
            speech_provider_name=args.provider,
            judge_provider_name=args.judge_provider,
        )
        manifest = await service.build_profiles(
            judge,
            manifest,
            force_profiles=args.force_profiles,
            voice_types=voice_types_arg,
        )
        profiles_built = True
    manifest_path = catalog_repo.manifest_path(manifest.provider, manifest.model)
    print(
        json.dumps(
            {
                "provider": manifest.provider,
                "model": manifest.model,
                "catalog_version": manifest.catalog_version,
                "voice_count": len(manifest.voices),
                "sample_emotions": manifest.sample_emotions,
                "manifest_path": str(manifest_path),
                "samples_built": samples_built,
                "profiles_built": profiles_built,
                "profiles_mode": (
                    "force"
                    if args.force_profiles
                    else ("missing" if args.miss_profiles else None)
                ),
                "selected_voice_types": sorted(voice_types) if voice_types else [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def cmd_voice_catalog_inspect(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    provider = _catalog_speech_provider(settings, args.provider)
    catalog_repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(catalog_repo)
    if callable(getattr(provider, "list_preset_voices", None)):
        manifest = await service.sync_official_preset_catalog(provider, download_trials=False)
    else:
        manifest = service.load_or_bootstrap_manifest(provider)
    duplicates = catalog_repo.duplicate_labels(manifest)
    print(
        json.dumps(
            {
                "provider": manifest.provider,
                "model": manifest.model,
                "catalog_version": manifest.catalog_version,
                "voice_count": len(manifest.voices),
                "sample_emotions": manifest.sample_emotions,
                "duplicate_voice_labels": duplicates,
                "manifest_path": str(catalog_repo.manifest_path(manifest.provider, manifest.model)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config(
        title=args.title,
        chapters_dir=args.chapters_dir,
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
        node_group=args.node_group,
        episode_keys=parse_episode_keys(args.episodes),
        role_names=parse_role_names(args.roles),
        clip_selectors=parse_clip_selectors(args.clips),
        shot_selectors=parse_shot_selectors(args.shots),
        asset_ids=parse_asset_ids(args.assets),
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
                "node_group": args.node_group,
                "clips": args.clips or None,
                "shots": args.shots or None,
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
                "shots": args.shots or None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def cmd_run_postgen(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    project_dir = repo.resolve_active_project_dir(args.project)
    episode_keys = parse_episode_keys(args.episodes)

    provider_override = "fake" if args.provider == "fake" else None
    router = ProviderRouter(settings, provider_override=provider_override)
    workflow = PostgenWorkflow(repo=repo, router=router)
    state = await workflow.run(
        project_dir,
        until=args.until,
        force=args.force,
        episode_keys=episode_keys,
        only=args.only,
        shot_selectors=parse_shot_selectors(args.shots),
    )
    get_logger().info(
        "run summary workflow=postgen project_id=%s current_node=%s project_dir=%s",
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
                "shots": args.shots or None,
                "postgen_outputs": str(project_dir / "outputs" / "videos"),
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
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {"pregen", "generation", "postgen"}:
        argv = ["run", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "run" and args.workflow == "pregen":
        return asyncio.run(cmd_run_pregen(args))
    if args.command == "run" and args.workflow == "generation":
        return asyncio.run(cmd_run_generation(args))
    if args.command == "run" and args.workflow == "postgen":
        return asyncio.run(cmd_run_postgen(args))
    if args.command == "voice-catalog" and args.action == "build":
        return asyncio.run(cmd_voice_catalog_build(args))
    if args.command == "voice-catalog" and args.action == "inspect":
        return asyncio.run(cmd_voice_catalog_inspect(args))
    if args.command == "inspect" and args.target == "state":
        return cmd_inspect_state(args)
    if args.command == "inspect" and args.target == "nodes":
        return cmd_inspect_nodes(args)

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
