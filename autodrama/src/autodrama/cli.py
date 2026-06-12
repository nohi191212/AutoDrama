from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from autodrama.config import load_settings
from autodrama.logging import get_logger, setup_logging
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.script_service import ScriptService
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.generation import DEFAULT_GENERATION_NODES, GENERATION_NODES, GenerationWorkflow
from autodrama.workflows.pregen import PREGEN_NODES, PREGEN_ONLY_NODES, PregenWorkflow
from autodrama.workflows.selection import (
    parse_episode_keys as parse_episode_keys_value,
    parse_shot_selectors as parse_shot_selectors_value,
)


SCRIPT_IMPORT_BOOTSTRAP_NODES = {"script_detail_expand", "script_outline", "script_novel"}
SCRIPT_IMPORT_INVALIDATE_ON_PRESERVE = {
    "director_prep",
    "design_key_vision_prompt",
    "design_key_vision_image",
    "script_novel_extract",
    *GENERATION_NODES,
}


def parse_episode_keys(value: str | None) -> list[str] | None:
    return parse_episode_keys_value(value)


def parse_shot_selectors(value: str | None) -> list[str] | None:
    return parse_shot_selectors_value(value)


def parse_role_names(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def build_parser() -> argparse.ArgumentParser:
    pregen_only_choices = [*PREGEN_ONLY_NODES]
    if "prop_image_generation" not in pregen_only_choices:
        pregen_only_choices.append("prop_image_generation")

    parser = argparse.ArgumentParser(prog="autodrama")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a project directory")
    init_parser.add_argument("--config", required=True)
    init_parser.add_argument("--title")
    init_parser.add_argument("--script-file")
    init_parser.add_argument("--project-id")

    import_parser = subparsers.add_parser(
        "import-script",
        help="Import a mature screenplay as locked novel_full content without rewriting it as an outline.",
    )
    import_parser.add_argument("--config", required=True)
    import_parser.add_argument("--project", help="Project id. Defaults to project.id from config.yaml.")
    import_parser.add_argument("--title", help="Override project title when creating or refreshing the project.")
    import_parser.add_argument("--script", help="Mature screenplay file. Defaults to project.script_outline_file.")
    import_parser.add_argument("--episode-key", default="episode_001")
    import_parser.add_argument(
        "--detail-expand",
        action="store_true",
        help="Run the conservative script_detail_expand pass before importing novel_full.",
    )
    import_parser.add_argument(
        "--expanded-script-out",
        help="Optional path to also save the detail-expanded screenplay as a human-reviewable markdown file.",
    )
    import_parser.add_argument(
        "--max-expand-ratio",
        type=float,
        default=1.35,
        help="Maximum allowed expanded/source character ratio for --detail-expand. Default: 1.35.",
    )
    import_parser.add_argument("--provider", choices=["fake", "configured"], default="configured")
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Reinitialize the target project state before importing. Existing generated files may remain on disk.",
    )
    import_parser.add_argument(
        "--preserve-assets",
        action="store_true",
        help=(
            "Allow importing into a project with downstream assets. Role/prop/layout/media state is preserved; "
            "director_prep, script_novel_extract, and dynamic generation nodes are marked stale."
        ),
    )

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
            "Deferred prop/layout/BGM nodes are available only through --only."
        ),
    )
    pregen_parser.add_argument(
        "--episodes",
        "--episode",
        dest="episodes",
        help=(
            "Supported with pregen --only roleboard_prompt, roleboard_generation, storyboard_prompt, "
            "storyboard_generation, storyboard_bbox_detection, storyboard_panel_crop, shot_manifest_generation, "
            "role_voice_select, "
            "prop_design, prop_generation, or layout_image_generation "
            "(legacy alias: prop_image_generation). "
            "Prop/layout episode-scoped nodes are deferred from the default pregen chain."
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
    manifest = service.load_or_bootstrap_manifest(provider, force_bootstrap=args.force_manifest)
    sample_emotions = _parse_sample_emotions(args.sample_emotions)
    manifest = service.with_sample_emotions(manifest, sample_emotions)
    catalog_repo.save_manifest(manifest)
    voice_types = set(args.voice_types or [])
    if args.limit is not None and args.limit > 0:
        limited = {voice.voice_type for voice in manifest.voices[: args.limit]}
        voice_types = voice_types.intersection(limited) if voice_types else limited
    voice_types_arg = voice_types or None
    samples_built = False
    profiles_built = False
    if args.force_samples:
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


def cmd_voice_catalog_inspect(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    provider = _catalog_speech_provider(settings, args.provider)
    catalog_repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(catalog_repo)
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
        script_file=args.script_file,
        project_id=args.project_id,
    )
    logger = setup_logging(project_dir)
    state = repo.load_state(project_dir)
    logger.info("project initialized project_id=%s project_dir=%s", state.project_id, project_dir)
    print(json.dumps({"project_id": state.project_id, "project_dir": str(project_dir)}, ensure_ascii=False, indent=2))
    return 0


def _resolve_import_script_path(settings, script_arg: str | None) -> Path:
    script_path = Path(script_arg).expanduser() if script_arg else settings.project.script_outline_file
    if script_path is None:
        raise ValueError("Missing script file. Set project.script_outline_file in config.yaml or pass --script.")
    if not script_path.is_absolute():
        script_path = script_path.resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")
    return script_path


def _resolve_import_project_id(settings, project_arg: str | None) -> str:
    project_id = project_arg or settings.project.id
    if not project_id:
        raise ValueError("Missing project id. Set project.id in config.yaml or pass --project.")
    return project_id


def _create_or_load_import_project(
    repo: ProjectRepository,
    *,
    title: str | None,
    script_path: Path,
    project_id: str,
    force: bool,
) -> Path:
    if force:
        return repo.create_project_from_config(title=title, script_file=script_path, project_id=project_id)

    project_dir = repo.resolve_project_dir(project_id)
    if not (project_dir / "state.json").exists():
        return repo.create_project_from_config(title=title, script_file=script_path, project_id=project_id)
    return project_dir


def _downstream_completed_nodes(completed_nodes: list[str]) -> list[str]:
    return [node for node in completed_nodes if node not in SCRIPT_IMPORT_BOOTSTRAP_NODES]


def _invalidate_preserved_script_dependents(state) -> list[str]:
    invalidated: list[str] = []
    kept_completed_nodes: list[str] = []
    for node_name in state.completed_nodes:
        if node_name in SCRIPT_IMPORT_INVALIDATE_ON_PRESERVE:
            invalidated.append(node_name)
        else:
            kept_completed_nodes.append(node_name)
    state.completed_nodes = kept_completed_nodes
    if state.current_node in SCRIPT_IMPORT_INVALIDATE_ON_PRESERVE:
        state.current_node = "script_novel"
    return invalidated


def _mark_generation_checklist_for_regen(repo: ProjectRepository, project_dir: Path, episode_keys: list[str]) -> bool:
    checklist_path = project_dir / "generation_checklist.json"
    if not checklist_path.exists():
        return False
    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    if not isinstance(checklist, dict):
        return False
    target = set(episode_keys)
    changed = False
    for item in checklist.get("episodes", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("episode_key")) not in target:
            continue
        item["generate"] = True
        item["generation_status"] = "pending"
        item["notes"] = str(item.get("notes") or "").strip()
        changed = True
    if changed:
        repo.write_json(checklist_path, checklist)
    return changed


async def cmd_import_script(args: argparse.Namespace) -> int:
    if args.force and args.preserve_assets:
        raise ValueError("--force and --preserve-assets cannot be used together")
    if args.max_expand_ratio < 1.0:
        raise ValueError("--max-expand-ratio must be at least 1.0")

    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    script_path = _resolve_import_script_path(settings, args.script)
    project_id = _resolve_import_project_id(settings, args.project)
    project_dir = _create_or_load_import_project(
        repo,
        title=args.title,
        script_path=script_path,
        project_id=project_id,
        force=args.force,
    )
    logger = setup_logging(project_dir)
    state = repo.load_state(project_dir)
    script_service = ScriptService(PromptStore())
    episode_key = str(args.episode_key or "episode_001").strip()
    expected_episode_keys = script_service.episode_keys(script_service.episode_count(state))
    if expected_episode_keys != [episode_key]:
        raise ValueError(
            "import-script currently imports one mature screenplay as one episode; "
            f"expected episode keys {expected_episode_keys}, got {episode_key}. "
            "Set project.episode_count to 1 or split/import episodes explicitly after extending this command."
        )

    downstream = _downstream_completed_nodes(state.completed_nodes)
    if state.roles or state.props or state.layouts or state.bgms:
        downstream.append("state_assets")
    if downstream and not args.force and not args.preserve_assets:
        raise ValueError(
            "Project already has downstream generated state: "
            + ", ".join(downstream)
            + ". Re-run import-script with --preserve-assets to update only script state, "
            + "or --force to reinitialize project state before importing."
        )

    source_script = script_path.read_text(encoding="utf-8").strip()
    if not source_script:
        raise ValueError(f"Script file is empty: {script_path}")

    imported_script = source_script
    detail_expand_output = None
    provider_name = None
    provider_model = None
    if args.detail_expand:
        provider_override = "fake" if args.provider == "fake" else None
        router = ProviderRouter(settings, provider_override=provider_override)
        provider = router.text("script")
        provider_name = getattr(provider, "name", "unknown")
        provider_model = getattr(provider, "model", None)
        logger.info(
            "node=script_detail_expand provider=%s model=%s episode_key=%s max_expand_ratio=%s",
            provider_name,
            provider_model or "-",
            episode_key,
            args.max_expand_ratio,
        )
        detail_expand_output = await script_service.script_detail_expand(
            state,
            provider,
            episode_key=episode_key,
            raw_script=source_script,
            max_expand_ratio=args.max_expand_ratio,
        )
        if detail_expand_output.episode_key != episode_key:
            raise ValueError(
                f"script_detail_expand episode_key must be {episode_key}; got {detail_expand_output.episode_key}"
            )
        imported_script = detail_expand_output.expanded_script.strip()
        if not imported_script:
            raise ValueError("script_detail_expand returned empty expanded_script")
        max_allowed_chars = max(
            int(len(source_script) * args.max_expand_ratio * 1.05),
            len(source_script) + 400,
        )
        if len(imported_script) > max_allowed_chars:
            raise ValueError(
                "script_detail_expand exceeded the allowed expansion size: "
                f"source={len(source_script)} expanded={len(imported_script)} max_allowed={max_allowed_chars}"
            )
        repo.save_node_output(project_dir, "script_detail_expand", detail_expand_output)
        state.budget.used_text_calls += 1

    if args.expanded_script_out:
        output_path = Path(args.expanded_script_out).expanduser()
        if not output_path.is_absolute():
            output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(imported_script + "\n", encoding="utf-8")

    script_contents = ScriptContentRepository(repo, repo.layout)
    state.title = args.title or state.title
    state.raw_script = source_script
    state.script.raw_script = source_script
    state.script.outline = "成熟剧本导入：以 locked novel_full 分场剧本文本作为后续资产抽取的唯一剧情依据。"
    state.script.episode_outlines = {
        episode_key: script_contents.write_content(
            project_dir,
            "outlines",
            episode_key,
            node_name="script_import",
            content=(
                "成熟剧本导入，不进行大纲重写。后续剧情、角色、道具、场景均以 novel_full 为准。\n\n"
                + imported_script[:1200]
            ),
        )
    }
    state.script.novel_full = {
        episode_key: script_contents.write_content(
            project_dir,
            "novel_full",
            episode_key,
            node_name="script_detail_expand" if args.detail_expand else "script_import",
            content=imported_script,
            dependency_field="source_script_file",
            dependency_path=str(script_path),
        )
    }
    state.script.novel_extract = {episode_key: False}
    invalidated_nodes: list[str] = []
    checklist_marked_for_regen = False
    if args.preserve_assets:
        invalidated_nodes = _invalidate_preserved_script_dependents(state)
        checklist_marked_for_regen = _mark_generation_checklist_for_regen(repo, project_dir, expected_episode_keys)
    state.metadata.update(
        {
            "script_mode": "mature_script",
            "mature_script_source_file": str(script_path),
            "mature_script_imported_episode_key": episode_key,
            "mature_script_detail_expanded": bool(args.detail_expand),
            "mature_script_max_expand_ratio": args.max_expand_ratio,
            "mature_script_preserved_assets": bool(args.preserve_assets),
            "mature_script_invalidated_nodes": invalidated_nodes,
            "script_novel_full_episode_paths": dict(state.script.novel_full),
        }
    )
    if provider_name:
        state.metadata["script_detail_expand_provider"] = provider_name
    if provider_model:
        state.metadata["script_detail_expand_model"] = provider_model

    repo.save_node_output(
        project_dir,
        "script_outline",
        {
            "logline": state.title,
            "outline": state.script.outline,
            "episode_count": script_service.episode_count(state),
            "target_duration_seconds": script_service.episode_duration_seconds(state),
            "episode_outlines": state.script.episode_outlines,
            "imported_mature_script": True,
        },
    )
    repo.save_node_output(
        project_dir,
        "script_novel",
        {
            "novel_full": state.script.novel_full,
            "imported_mature_script": True,
            "detail_expanded": bool(args.detail_expand),
        },
    )
    if detail_expand_output is not None:
        state.mark_completed("script_detail_expand")
    state.mark_completed("script_outline")
    state.mark_completed("script_novel")
    repo.save_state(project_dir, state)
    logger.info(
        "mature script imported project_id=%s episode_key=%s project_dir=%s detail_expanded=%s",
        state.project_id,
        episode_key,
        project_dir,
        bool(args.detail_expand),
    )
    print(
        json.dumps(
            {
                "project_id": state.project_id,
                "project_dir": str(project_dir),
                "episode_key": episode_key,
                "detail_expanded": bool(args.detail_expand),
                "preserve_assets": bool(args.preserve_assets),
                "invalidated_nodes": invalidated_nodes,
                "generation_checklist_marked_for_regen": checklist_marked_for_regen,
                "novel_full": state.script.novel_full,
                "completed_nodes": state.completed_nodes,
                "next_script_extract_command": (
                    f"run\\start.cmd --config {args.config} --project {state.project_id} "
                    "--until script_novel_extract"
                ),
                "next_review_command": (
                    f"D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli inspect nodes "
                    f"--config {args.config} --project {state.project_id}"
                ),
                "next_pregen_command": f"run\\start.cmd --config {args.config} --project {state.project_id}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
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
        role_names=parse_role_names(args.roles),
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
                "shots": args.shots or None,
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
    if argv and argv[0] in {"pregen", "generation"}:
        argv = ["run", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "import-script":
        return asyncio.run(cmd_import_script(args))
    if args.command == "run" and args.workflow == "pregen":
        return asyncio.run(cmd_run_pregen(args))
    if args.command == "run" and args.workflow == "generation":
        return asyncio.run(cmd_run_generation(args))
    if args.command == "voice-catalog" and args.action == "build":
        return asyncio.run(cmd_voice_catalog_build(args))
    if args.command == "voice-catalog" and args.action == "inspect":
        return cmd_voice_catalog_inspect(args)
    if args.command == "inspect" and args.target == "state":
        return cmd_inspect_state(args)
    if args.command == "inspect" and args.target == "nodes":
        return cmd_inspect_nodes(args)

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
