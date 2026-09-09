"""Isolated, sequential comparisons through the actual key-vision node runners."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "autodrama" / "src"))

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.pregen import PregenWorkflow


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    source = Path(args.project).resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        raise ValueError("Experiment outputs must stay under repository .tmp/")
    source_state = repo.load_state(source)
    source_hash = hashlib.sha256((source / "state.json").read_bytes()).hexdigest()
    brief = (HERE / "brief.md").read_text(encoding="utf-8").strip()
    failures = 0
    for candidate in args.candidate:
        template_path = (PromptStore().template_path("key_vision_prompt") if candidate == "baseline"
                         else HERE / "templates" / candidate / "key_vision_prompt" / "default.md")
        template = template_path.read_text(encoding="utf-8")
        state = source_state.model_copy(deep=True)
        state.metadata["key_vision_director_brief"] = brief
        state.metadata["key_vision_continuity_contract"] = "One continuous location, exactly two adults, one camera; preserve the creative brief's near/middle/far spatial arrangement."
        # Old output/audit history is not an input to a fresh, controlled comparison.
        for key in list(state.metadata):
            if key.startswith("key_vision") and key not in {
                "key_vision_director_brief", "key_vision_continuity_contract"
            }:
                state.metadata.pop(key)
        identity = {
            "source_state_sha256": source_hash,
            "template": template,
            "brief": brief,
            "nodes": {name: settings.nodes[name].model_dump(mode="json") for name in
                      ("key_vision_prompt", "key_vision_image_generation")},
            "config_sha256": hashlib.sha256(Path(args.config).read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        destination = output / f"{candidate}-{args.repeat}"
        manifest = destination / "manifest.json"
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if previous["fingerprint"] != fingerprint:
                raise ValueError(f"Inputs changed for {destination}; choose a new --output or --repeat")
            if previous["status"] == "complete":
                image_path = destination / previous["image"]
                if image_path.is_file():
                    print(f"CACHED {candidate}: {image_path}", flush=True)
                    continue
                raise FileNotFoundError(image_path)
        prompt_root = destination / "templates"
        frozen_template = prompt_root / "key_vision_prompt" / "default.md"
        frozen_template.parent.mkdir(parents=True, exist_ok=True)
        frozen_template.write_text(template, encoding="utf-8")
        router = ProviderRouter(settings)
        router.set_prompt_audit_project_dir(destination)
        workflow = PregenWorkflow(repo=repo, router=router, prompts=PromptStore(prompt_root))
        service = workflow.director_service
        canvas = workflow._director_node_runner("key_vision_prompt").key_vision_image_canvas(
            workflow.router.image("key_vision", node_name="key_vision_image_generation"))
        rendered = workflow.prompts.render("key_vision_prompt",
            script_type=service.key_vision_script_type(state),
            global_visual_style=service.visual_style_prompt(state),
            director_brief=service.key_vision_director_brief(state),
            render_contract=service.key_vision_render_contract(state, canvas),
            continuity_contract=service.key_vision_continuity_contract(state),
            audit_feedback=service.key_vision_audit_feedback(state))
        (destination / "compiler-input.md").write_text(rendered, encoding="utf-8")
        record = {"fingerprint": fingerprint, "candidate": candidate, "status": "prepared",
                  "identity": identity, "canvas": canvas, "reference_images": []}
        save(manifest, record)
        if args.dry_run:
            print(f"PREPARED {candidate}: {destination}", flush=True)
            continue
        try:
            print(f"START {candidate}", flush=True)
            state = await workflow._director_node_runner("key_vision_prompt").run(destination, state)
            (destination / "image-prompt.md").write_text(state.metadata["key_vision_prompt"]["prompt"], encoding="utf-8")
            record["status"] = "prompt_complete"
            save(manifest, record)
            print(f"PROMPT READY {candidate}; generating image", flush=True)
            state = await workflow._director_node_runner("key_vision_image_generation").run(destination, state)
            record.update(status="complete", image=state.metadata["key_vision_asset_path"])
            print(f"COMPLETE {candidate}: {record['image']}", flush=True)
        except Exception as exc:
            failures += 1
            record.update(status="failed", error_type=type(exc).__name__)
            print(f"FAILED {candidate}: {type(exc).__name__}; inspect isolated node outputs", flush=True)
        finally:
            save(manifest, record)
    if hashlib.sha256((source / "state.json").read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Source state changed during experiment; inspect concurrent work")
    return bool(failures)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "fangu.yaml"))
    parser.add_argument("--project", default=str(ROOT / "outputs" / "fangu_0903"))
    parser.add_argument("--output", default=str(ROOT / ".tmp" / "key_vision_photo"))
    parser.add_argument("--candidate", nargs="+", choices=["baseline", "a_scene", "b_camera", "c_film"],
                        default=["baseline", "a_scene", "b_camera", "c_film"])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="Render inputs locally; no API calls")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
