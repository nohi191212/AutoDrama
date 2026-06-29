"""Build and optionally submit a Kling first-shot multi-image video request.

Default mode is an offline contract check. It builds the same payload shape used
by autodrama.providers.kling.video.omni.KlingOmniVideoProvider and writes a
redacted payload to .tmp/.

Example dry run with real assets:

    D:/miniforge3/envs/autodrama/python.exe scripts/smoke/kling_first_shot_multi_image_smoke.py ^
      --prompt "A tense first shot in a rain-soaked alley." ^
      --start-frame outputs/project/assets/images/shot_001_start.png ^
      --end-frame outputs/project/assets/images/shot_001_end.png ^
      --storyboard outputs/project/assets/images/storyboards/shot_001.png ^
      --scene outputs/project/assets/images/layouts/alley.png ^
      --character outputs/project/assets/images/roles/hero.png ^
      --prop outputs/project/assets/images/props/key.png

Add --submit only when you want to send the request to Kling.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings, load_settings
from autodrama.providers.base import AssetRef, VideoGenerationResult
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider


SAMPLE_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
SAMPLE_PROMPT = (
    "A cinematic first shot: the protagonist enters a narrow neon-lit alley, "
    "rain on the ground, a small brass key in hand, slow push-in camera, "
    "tense atmosphere, no subtitles, no logo, no watermark."
)
PROMPT_LIMIT = 2500


IMAGE_KINDS: tuple[dict[str, Any], ...] = (
    {
        "arg": "start_frame",
        "asset_type": "clip_start_frame",
        "label": "first frame",
        "kling_type": "first_frame",
        "instruction": "video must open from this frame",
    },
    {
        "arg": "end_frame",
        "asset_type": "clip_end_frame",
        "label": "end frame",
        "kling_type": "end_frame",
        "instruction": "video must end as close as possible to this frame",
    },
    {
        "arg": "storyboard",
        "asset_type": "storyboard",
        "label": "storyboard sheet",
        "kling_type": None,
        "instruction": "locks composition, camera rhythm, action order, and cut boundaries",
    },
    {
        "arg": "scene",
        "asset_type": "layout",
        "label": "scene reference",
        "kling_type": None,
        "instruction": "locks space, materials, lighting, and movement paths",
    },
    {
        "arg": "character",
        "asset_type": "roleboard",
        "label": "character reference",
        "kling_type": None,
        "instruction": "locks face, hair, body shape, costume, age, and accessories",
    },
    {
        "arg": "prop",
        "asset_type": "prop",
        "label": "prop reference",
        "kling_type": None,
        "instruction": "locks prop shape, material, scale, and identifying details",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline/online smoke test for Kling first-shot multi-image video generation.",
    )
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt", help="Final first-shot video prompt text.")
    prompt_group.add_argument("--prompt-file", type=Path, help="UTF-8 text file containing the prompt.")

    parser.add_argument("--start-frame", "--first-frame", dest="start_frame", action="append", help="First frame image path or URL.")
    parser.add_argument("--end-frame", "--last-frame", dest="end_frame", action="append", help="End frame image path or URL.")
    parser.add_argument("--storyboard", action="append", help="Storyboard sheet image path or URL.")
    parser.add_argument("--scene", "--layout", dest="scene", action="append", help="Scene/layout image path or URL. Repeatable.")
    parser.add_argument("--character", "--role", dest="character", action="append", help="Character/roleboard image path or URL. Repeatable.")
    parser.add_argument("--prop", action="append", help="Prop image path or URL. Repeatable.")

    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml", help="Project config file.")
    parser.add_argument("--out", type=Path, default=ROOT / ".tmp" / "kling_first_shot_multi_image_payload.json")
    parser.add_argument("--duration", type=float, default=5.0, help="Requested video duration in seconds.")
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--mode", default="pro")
    parser.add_argument("--sound", choices=("on", "off"), default="off")
    parser.add_argument("--model", default=None, help="Override Kling model name.")
    parser.add_argument("--base-url", default=None, help="Override Kling base URL.")
    parser.add_argument("--timeout", type=int, default=120, help="Submit timeout in seconds.")
    parser.add_argument("--max-reference-images", type=int, default=None, help="Provider image limit override.")
    parser.add_argument("--allow-prompt-truncation", action="store_true")
    parser.add_argument("--sample-assets", action="store_true", help="Use generated .tmp placeholder images.")
    parser.add_argument("--submit", action="store_true", help="Submit the request to Kling instead of dry-run only.")
    parser.add_argument("--print-payload", action="store_true", help="Print redacted payload JSON.")
    return parser.parse_args()


def flatten_api_keys(value: Any, *, prefix: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    keys: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(raw_value, dict):
            keys.update(flatten_api_keys(raw_value, prefix=full_key))
            continue
        if raw_value is None:
            continue
        text = str(raw_value)
        keys[full_key] = text
        keys[full_key.replace(".", "_").upper()] = text
    return keys


def load_api_keys_from_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return flatten_api_keys(data)


def values(items: list[str] | None) -> list[str]:
    return [str(item).strip() for item in items or [] if str(item).strip()]


def image_args_present(args: argparse.Namespace) -> bool:
    return any(values(getattr(args, spec["arg"], None)) for spec in IMAGE_KINDS)


def ensure_sample_assets() -> dict[str, list[str]]:
    asset_dir = ROOT / ".tmp" / "kling_first_shot_multi_image_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_bytes = base64.b64decode(SAMPLE_PNG_BASE64)
    paths: dict[str, list[str]] = {}
    for spec in IMAGE_KINDS:
        path = asset_dir / f"{spec['arg']}.png"
        path.write_bytes(image_bytes)
        paths[spec["arg"]] = [str(path)]
    return paths


def resolve_prompt(args: argparse.Namespace, *, using_sample_assets: bool) -> str:
    if args.prompt_file:
        return args.prompt_file.expanduser().resolve().read_text(encoding="utf-8").strip()
    if args.prompt:
        return str(args.prompt).strip()
    if using_sample_assets:
        return SAMPLE_PROMPT
    raise SystemExit("ERROR: pass --prompt or --prompt-file.")


def path_or_url_ref(value: str, *, slot: str, spec: dict[str, Any], order: int) -> AssetRef:
    metadata = {
        "slot": slot,
        "order": order,
        "asset_type": spec["asset_type"],
        "label": spec["label"],
    }
    if spec["kling_type"]:
        metadata["kling_type"] = spec["kling_type"]

    if value.startswith(("http://", "https://")):
        return AssetRef(id=slot, type="image", url=value, metadata=metadata)
    if value.startswith("data:"):
        return AssetRef(id=slot, type="image", path=value, metadata=metadata)
    return AssetRef(id=slot, type="image", path=str(Path(value).expanduser().resolve()), metadata=metadata)


def build_refs(args: argparse.Namespace, *, using_sample_assets: bool) -> list[AssetRef]:
    sample_paths = ensure_sample_assets() if using_sample_assets else {}
    refs: list[AssetRef] = []
    order = 1
    for spec in IMAGE_KINDS:
        raw_values = sample_paths.get(spec["arg"]) if using_sample_assets else values(getattr(args, spec["arg"], None))
        for value in raw_values or []:
            slot = f"image_{order}"
            refs.append(path_or_url_ref(value, slot=slot, spec=spec, order=order))
            order += 1
    return refs


def validate_refs(refs: list[AssetRef]) -> None:
    errors: list[str] = []
    for ref in refs:
        if ref.url or not ref.path or str(ref.path).startswith(("http://", "https://", "data:")):
            continue
        path = Path(ref.path)
        if not path.exists() or not path.is_file():
            errors.append(f"{ref.metadata.get('slot')}: file not found: {path}")
    if errors:
        raise SystemExit("ERROR: invalid image input(s):\n" + "\n".join(f"  - {item}" for item in errors))


def build_first_shot_prompt(base_prompt: str, refs: list[AssetRef]) -> str:
    lines = [
        "USER VIDEO PROMPT:",
        base_prompt.strip(),
        "",
        "FIRST SHOT REFERENCE IMAGES:",
    ]
    for ref in refs:
        slot = ref.metadata.get("slot")
        label = ref.metadata.get("label")
        instruction = next(
            (spec["instruction"] for spec in IMAGE_KINDS if spec["asset_type"] == ref.metadata.get("asset_type")),
            "use as a visual reference",
        )
        lines.append(f"- {slot}: {label}; {instruction}.")
    lines.extend(
        [
            "",
            "Kling execution rules:",
            "- This is the first clip, so do not use previous-shot continuity.",
            "- Prioritize image_1 for the opening frame and image_2 for the ending frame.",
            "- Use the storyboard only for composition, action order, camera rhythm, and cut boundaries.",
            "- Use scene, character, and prop references only for visual consistency.",
            "- Do not render storyboard panels, reference sheets, grid lines, image labels, subtitles, logos, or watermarks.",
        ]
    )
    return "\n".join(lines)


def default_provider_settings() -> ProviderSettings:
    return ProviderSettings(
        base_url="https://api-beijing.klingai.com",
        api_key_env="KLING_API_KEY",
        models={"video": "kling-v3-omni", "subject_element": "advanced-custom-elements"},
        options={
            "mode": "pro",
            "aspect_ratio": "9:16",
            "sound": "off",
            "watermark": False,
            "max_reference_images": 6,
            "video_min_duration_seconds": 3,
            "video_max_duration_seconds": 15,
        },
        api_keys=load_api_keys_from_file(ROOT / "apikeys.yaml"),
    )


def load_provider(args: argparse.Namespace, *, ref_count: int) -> tuple[KlingOmniVideoProvider, ProviderSettings]:
    runtime = RuntimeSettings(request_timeout_seconds=args.timeout)
    provider_settings = default_provider_settings()
    if args.config and args.config.exists():
        try:
            settings = load_settings(args.config)
        except Exception as exc:
            print(f"WARNING: failed to load {args.config}: {exc}. Using Kling defaults.", file=sys.stderr)
        else:
            runtime = settings.runtime
            configured = settings.providers.get("kling")
            if configured is not None:
                provider_settings = configured.model_copy(deep=True)

    if not provider_settings.api_keys:
        provider_settings.api_keys = load_api_keys_from_file(ROOT / "apikeys.yaml")
    provider_settings.base_url = args.base_url or provider_settings.base_url or "https://api-beijing.klingai.com"
    provider_settings.api_key_env = provider_settings.api_key_env or "KLING_API_KEY"

    provider_settings.models = dict(provider_settings.models)
    provider_settings.models["video"] = args.model or provider_settings.models.get("video") or "kling-v3-omni"

    max_reference_images = args.max_reference_images or max(ref_count, 1)
    provider_settings.options = dict(provider_settings.options)
    provider_settings.options.update(
        {
            "mode": args.mode,
            "aspect_ratio": args.aspect_ratio,
            "sound": args.sound,
            "watermark": False,
            "max_reference_images": max(max_reference_images, ref_count),
        }
    )
    runtime.request_timeout_seconds = args.timeout
    return KlingOmniVideoProvider(provider_settings, runtime), provider_settings


def assert_payload_contract(payload: dict[str, Any], refs: list[AssetRef], *, allow_prompt_truncation: bool) -> None:
    errors: list[str] = []
    prompt = str(payload.get("prompt") or "")
    if not prompt.strip():
        errors.append("payload.prompt is empty")
    if len(prompt) >= PROMPT_LIMIT and not allow_prompt_truncation:
        errors.append(
            f"payload.prompt is {len(prompt)} chars; Kling provider truncates at {PROMPT_LIMIT}. "
            "Shorten the prompt or pass --allow-prompt-truncation."
        )

    images = payload.get("image_list")
    if not isinstance(images, list):
        errors.append("payload.image_list is missing")
        images = []
    if len(images) != len(refs):
        errors.append(f"payload.image_list has {len(images)} image(s), expected {len(refs)}")
    for index, item in enumerate(images, start=1):
        if not isinstance(item, dict) or not item.get("image_url"):
            errors.append(f"image_{index} has no image_url")

    if len(images) >= 1 and images[0].get("type") != "first_frame":
        errors.append("image_1 must be marked type=first_frame")
    if len(images) >= 2 and images[1].get("type") != "end_frame":
        errors.append("image_2 must be marked type=end_frame")
    if "element_list" in payload:
        errors.append("payload must not include Kling subject element_list for this test")
    if "video_list" in payload:
        errors.append("payload must not include video_list for this first-shot image test")

    if errors:
        raise AssertionError("Kling first-shot payload contract failed:\n" + "\n".join(f"  - {item}" for item in errors))


def redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload, ensure_ascii=False))
    for item in redacted.get("image_list", []):
        value = item.get("image_url")
        if not isinstance(value, str):
            continue
        if value.startswith(("http://", "https://")):
            continue
        if value.startswith("data:"):
            item["image_url"] = f"<data-url {len(value)} chars>"
        else:
            item["image_url"] = f"<base64 {len(value)} chars>"
    return redacted


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def submit_video(provider: KlingOmniVideoProvider, prompt: str, refs: list[AssetRef], args: argparse.Namespace) -> VideoGenerationResult:
    return await provider.submit_video(
        prompt,
        refs=refs,
        duration=args.duration,
        metadata={
            "duration": args.duration,
            "mode": args.mode,
            "aspect_ratio": args.aspect_ratio,
            "sound": args.sound,
            "model": provider.model,
            "watermark": False,
        },
    )


def main() -> None:
    args = parse_args()
    using_sample_assets = args.sample_assets or not image_args_present(args)
    if args.submit and using_sample_assets:
        raise SystemExit("ERROR: --submit requires real image paths or URLs; do not submit generated sample assets.")

    refs = build_refs(args, using_sample_assets=using_sample_assets)
    if not refs:
        raise SystemExit("ERROR: at least one image reference is required.")
    validate_refs(refs)

    base_prompt = resolve_prompt(args, using_sample_assets=using_sample_assets)
    final_prompt = build_first_shot_prompt(base_prompt, refs)
    provider, provider_settings = load_provider(args, ref_count=len(refs))
    payload = provider.build_payload(
        final_prompt,
        refs=refs,
        duration=args.duration,
        metadata={
            "duration": args.duration,
            "mode": args.mode,
            "aspect_ratio": args.aspect_ratio,
            "sound": args.sound,
            "model": provider.model,
            "watermark": False,
        },
    )
    assert_payload_contract(payload, refs, allow_prompt_truncation=args.allow_prompt_truncation)

    redacted = redacted_payload(payload)
    write_json(args.out, redacted)
    if args.print_payload:
        print(json.dumps(redacted, ensure_ascii=False, indent=2))

    print("kling_first_shot_multi_image_smoke: payload ok")
    print(f"  references: {len(refs)}")
    print(f"  model: {payload.get('model_name')}")
    print(f"  duration: {payload.get('duration')}")
    print(f"  redacted payload: {args.out}")

    if not args.submit:
        if using_sample_assets:
            print("  sample assets were used for dry-run only")
        return

    if not provider_settings.secret("api_key_env"):
        env_name = provider_settings.api_key_env or "KLING_API_KEY"
        raise SystemExit(f"ERROR: missing Kling API key. Set {env_name} in apikeys.yaml or environment.")

    result = asyncio.run(submit_video(provider, final_prompt, refs, args))
    response_path = args.out.with_name(args.out.stem + "_submit_response.json")
    write_json(response_path, result.model_dump(mode="json"))
    print("kling_first_shot_multi_image_smoke: submit ok")
    print(f"  task_id: {result.task_id}")
    print(f"  task_status: {result.task_status}")
    print(f"  response: {response_path}")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
