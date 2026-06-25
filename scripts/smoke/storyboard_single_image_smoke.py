from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardPromptClip, StoryboardPromptEpisode, StoryboardPromptOutput  # noqa: E402
from autodrama.logging import get_logger  # noqa: E402
from autodrama.providers.base import AssetRef, ImageGenerationResult  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import (  # noqa: E402
    STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
    StoryboardGenerationNode,
)


DEFAULT_CLIP_ID = "episode_001_clip_001"
DEFAULT_CONFIG = "huyao.yaml" if (ROOT_DIR / "huyao.yaml").exists() else "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate exactly one storyboard sheet image from an existing storyboard_prompt clip. "
            "Outputs prompt, refs, payload, response JSON, and the generated image under .tmp by default."
        )
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path.")
    parser.add_argument("--project", default=None, help="Project id or project directory. Defaults to config/current project.")
    parser.add_argument("--clip-id", default=DEFAULT_CLIP_ID, help="Storyboard clip id to test.")
    parser.add_argument("--episode-key", default=None, help="Optional episode key guard, e.g. episode_001.")
    parser.add_argument("--output-dir", default=None, help="Output directory. Defaults to .tmp/smoke/storyboard_single_image/<stamp>.")
    parser.add_argument("--prompt-file", default=None, help="Optional prompt override file.")
    parser.add_argument("--no-refs", action="store_true", help="Send no reference images.")
    parser.add_argument("--max-refs", type=int, default=None, help="Override max reference images for this test.")
    parser.add_argument("--size", default=None, help="Override request size, e.g. 16:9.")
    parser.add_argument("--resolution", default=None, help="Override request resolution, e.g. 1K or 2K.")
    parser.add_argument("--model", default=None, help="Override provider model in the request payload.")
    parser.add_argument("--response-format", default=None, help="Override response_format.")
    parser.add_argument("--n", type=int, default=None, help="Override image count.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override runtime request timeout seconds.")
    parser.add_argument("--download-attempts", type=int, default=5, help="Generated image download attempts.")
    parser.add_argument("--dry-run", action="store_true", help="Write prompt/refs/payload without calling the image provider.")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".tmp" / "smoke" / "storyboard_single_image" / stamp


def resolve_repo_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT_DIR / path).resolve()


def sanitize_response(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return f"<base64 image data URL omitted; chars={len(value)}>"
        if len(value) > 500 and all(ch.isalnum() or ch in "+/=\r\n" for ch in value):
            return f"<possible base64 omitted; chars={len(value)}>"
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {"b64_json", "image_base64"} and isinstance(nested, str):
                sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
            else:
                sanitized[key] = sanitize_response(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_response(value), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def refs_to_json(refs: list[AssetRef]) -> list[dict[str, Any]]:
    return [ref.model_dump(mode="json") for ref in refs]


def find_clip(
    output: StoryboardPromptOutput,
    *,
    clip_id: str,
    episode_key: str | None,
) -> tuple[StoryboardPromptEpisode, StoryboardPromptClip]:
    matches: list[tuple[StoryboardPromptEpisode, StoryboardPromptClip]] = []
    for episode in output.storyboards:
        if episode_key and episode.episode_key != episode_key:
            continue
        for clip in episode.clips:
            if clip.clip_id == clip_id:
                matches.append((episode, clip))
    if not matches:
        suffix = f" in {episode_key}" if episode_key else ""
        raise ValueError(f"storyboard_prompt clip not found: {clip_id}{suffix}")
    if len(matches) > 1:
        raise ValueError(f"storyboard_prompt clip id is ambiguous: {clip_id}")
    return matches[0]


def make_storyboard_node(repo: ProjectRepository, router: ProviderRouter) -> StoryboardGenerationNode:
    return StoryboardGenerationNode(
        workflow=SimpleNamespace(),
        repo=repo,
        layout=repo.layout,
        router=router,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=get_logger(),
    )


def effective_metadata(provider: object, metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata)
    binding = getattr(provider, "model_binding", None)
    if binding is None:
        return merged
    merged.update(getattr(binding, "params", {}) or {})
    merged.setdefault("node_name", getattr(binding, "node_name", None) or metadata.get("node_name"))
    provider_model = getattr(binding, "provider_model_name", None)
    if provider_model:
        merged["model"] = provider_model
    return merged


def apply_request_overrides(metadata: dict[str, Any], args: argparse.Namespace) -> None:
    overrides: dict[str, Any] = {}
    if args.model:
        overrides["model"] = args.model
    if args.size:
        overrides["size"] = args.size
    if args.resolution:
        overrides["resolution"] = args.resolution
    if args.response_format:
        overrides["response_format"] = args.response_format
    if args.n is not None:
        overrides["n"] = args.n
    if overrides:
        metadata.setdefault("parameters", {}).update(overrides)


def image_extension_from_data_uri(data: str) -> str:
    if data.startswith("data:image/") and ";base64," in data:
        subtype = data.split("data:image/", 1)[1].split(";", 1)[0].lower()
        if subtype == "jpeg":
            return "jpg"
        if subtype in {"png", "jpg", "webp", "gif"}:
            return subtype
    return "png"


def image_extension_from_response(response: httpx.Response, url: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    by_content_type = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    if content_type in by_content_type:
        return by_content_type[content_type]

    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpg"
    if suffix in {"png", "jpg", "webp", "gif"}:
        return suffix
    return "png"


def base64_payload(data: str) -> str:
    if data.startswith("data:") and ";base64," in data:
        return data.split(";base64,", 1)[1]
    return data


async def download_image(url: str, *, timeout_seconds: float, attempts: int) -> tuple[bytes, str]:
    last_error: Exception | None = None
    delay_seconds = 1.0
    attempts = max(1, attempts)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url)
                if response.status_code < 400:
                    return response.content, image_extension_from_response(response, url)
                last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
            except httpx.HTTPError as exc:
                last_error = exc

            if attempt >= attempts:
                break
            print(f"download_attempt={attempt}/{attempts} failed={last_error}; retrying_in={delay_seconds:g}s")
            await asyncio.sleep(delay_seconds)
            delay_seconds = min(10.0, delay_seconds * 2)

    raise RuntimeError(f"failed to download generated image after {attempts} attempt(s): {last_error}")


async def save_first_image(
    result: ImageGenerationResult,
    *,
    output_dir: Path,
    timeout_seconds: float,
    download_attempts: int,
) -> Path:
    if result.image_data:
        extension = image_extension_from_data_uri(result.image_data[0])
        output_path = output_dir / f"image.{extension}"
        output_path.write_bytes(base64.b64decode(base64_payload(result.image_data[0])))
        return output_path

    if not result.image_urls:
        raise RuntimeError("image provider result has no image data or URL")

    data, extension = await download_image(
        result.image_urls[0],
        timeout_seconds=timeout_seconds,
        attempts=download_attempts,
    )
    output_path = output_dir / f"image.{extension}"
    output_path.write_bytes(data)
    return output_path


async def main_async(args: argparse.Namespace) -> int:
    config_path = resolve_repo_path(args.config)
    settings = load_settings(config_path)
    if args.timeout_seconds is not None:
        settings.runtime.request_timeout_seconds = int(args.timeout_seconds)

    repo = ProjectRepository(settings)
    project_dir = repo.resolve_active_project_dir(args.project)
    state = repo.load_state(project_dir)
    router = ProviderRouter(settings)
    provider = router.image("storyboard", node_name=STORYBOARD_IMAGE_PROVIDER_NODE_NAME)
    node = make_storyboard_node(repo, router)

    prompt_output = StoryboardPromptOutput.model_validate_json(
        repo.layout.node_output_path(project_dir, "storyboard_prompt").read_text(encoding="utf-8")
    )
    episode, clip = find_clip(prompt_output, clip_id=args.clip_id, episode_key=args.episode_key)

    if args.prompt_file:
        prompt_path = resolve_repo_path(args.prompt_file)
        image_prompt = prompt_path.read_text(encoding="utf-8")
    else:
        image_prompt = node.storyboard_image_prompt(episode.episode_key, clip)

    provider_max_refs = max(0, int(getattr(provider, "max_reference_images", 12) or 12))
    max_refs = provider_max_refs if args.max_refs is None else max(0, args.max_refs)
    refs = [] if args.no_refs or max_refs <= 0 else node.storyboard_reference_refs(project_dir, state, clip, limit=max_refs)

    asset_id = node.storyboard_asset_id(clip.clip_id)
    metadata: dict[str, Any] = {
        "node_name": StoryboardGenerationNode.name,
        "project_id": state.project_id,
        "episode_key": episode.episode_key,
        "clip_id": clip.clip_id,
        "asset_id": asset_id,
        "asset_type": "storyboard",
        "duration_seconds": clip.duration_seconds,
        "panel_count": node.STORYBOARD_PANEL_COUNT,
        "grid": node.storyboard_grid(),
        "panel_aspect_ratio": node.storyboard_panel_aspect_ratio(),
        "size": args.size or node.storyboard_sheet_size(),
        "provider_binding_node": STORYBOARD_IMAGE_PROVIDER_NODE_NAME,
    }
    apply_request_overrides(metadata, args)

    output_dir = resolve_repo_path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    write_text(output_dir / "prompt.txt", image_prompt)
    write_json(output_dir / "refs.json", refs_to_json(refs))
    write_json(output_dir / "metadata.json", metadata)

    payload: dict[str, Any] | None = None
    if hasattr(provider, "build_payload"):
        payload = provider.build_payload(
            image_prompt,
            refs=refs,
            size=args.size or node.storyboard_sheet_size(),
            metadata=effective_metadata(provider, metadata),
        )
        write_json(output_dir / "request_payload.dryrun.json", payload)

    print(f"project_dir={project_dir}")
    print(f"clip_id={clip.clip_id}")
    print(f"provider={getattr(provider, 'name', '-')}")
    print(f"model={getattr(provider, 'model', '-')}")
    print(f"asset_id={asset_id}")
    print(f"prompt_chars={len(image_prompt)}")
    print(f"refs={len(refs)}")
    print(f"provider_max_refs={provider_max_refs}")
    print(f"output_dir={output_dir}")

    if args.dry_run:
        print("dry_run=true")
        if payload is not None:
            print(f"request_payload={output_dir / 'request_payload.dryrun.json'}")
        print(f"prompt={output_dir / 'prompt.txt'}")
        print(f"refs_json={output_dir / 'refs.json'}")
        return 0

    if not bool(getattr(provider, "api_key", None)):
        print("generation_failed=missing image provider API key")
        return 1

    try:
        result = await provider.generate_image(
            image_prompt,
            refs=refs,
            size=args.size or node.storyboard_sheet_size(),
            metadata=metadata,
        )
        write_json(output_dir / "response.json", result.model_dump(mode="json"))
        write_json(output_dir / "raw_response.json", result.raw_response)
        image_path = await save_first_image(
            result,
            output_dir=output_dir,
            timeout_seconds=float(settings.runtime.request_timeout_seconds),
            download_attempts=args.download_attempts,
        )
    except Exception as exc:
        print(f"generation_failed={exc}")
        return 1

    print("generation_succeeded=true")
    print(f"task_id={result.task_id or '-'}")
    print(f"request_id={result.request_id or '-'}")
    print(f"task_status={result.task_status or '-'}")
    print(f"response={output_dir / 'response.json'}")
    print(f"raw_response={output_dir / 'raw_response.json'}")
    print(f"saved_image={image_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
