from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import VideoGenerationResult  # noqa: E402
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402


NODE_NAME = "role_subject_video_generation"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore locally missing huyao role subject videos from saved Kling raw responses.",
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "huyao.yaml"))
    parser.add_argument("--project", default="huyao")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"JSON payload must be an object: {path}")
    return payload


def _video_result_from_item(item: dict[str, Any]) -> VideoGenerationResult:
    raw_response = item.get("raw_response")
    require(isinstance(raw_response, dict), f"{item.get('asset_id') or '-'} has no raw_response")
    result = KlingOmniVideoProvider._video_result(
        raw_response,
        fallback_model=str(item.get("model") or "kling-v3-omni"),
    )
    return VideoGenerationResult(
        provider=str(result.provider or item.get("provider") or "kling_omni"),
        model=str(result.model or item.get("model") or "kling-v3-omni"),
        task_id=result.task_id or item.get("task_id"),
        task_status=result.task_status or item.get("task_status"),
        video_url=result.video_url or item.get("asset_url"),
        video_data=result.video_data,
        last_frame_url=result.last_frame_url,
        last_frame_data=result.last_frame_data,
        request_id=result.request_id or item.get("request_id"),
        usage=dict(result.usage or item.get("usage") or {}),
        raw_response=raw_response,
    )


def _find_appearance(role: Any, appearance_id: str) -> Any | None:
    appearance = role.appearances.get(appearance_id)
    if appearance is not None:
        return appearance
    for candidate in role.appearances.values():
        if candidate.id == appearance_id:
            return candidate
    return None


async def main_async() -> int:
    args = parse_args()
    settings = load_settings(args.config)
    repo = ProjectRepository(settings)
    layout = repo.layout
    media_store = MediaStore(layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    project_dir = repo.resolve_project_dir(args.project)
    state = repo.load_state(project_dir)
    node_output_path = layout.node_output_path(project_dir, NODE_NAME)
    node_output = _load_json(node_output_path)
    items = node_output.get("generated_subject_videos")
    require(isinstance(items, list), f"{node_output_path} has no generated_subject_videos list")

    restored: list[tuple[str, str, int]] = []
    reused: list[tuple[str, str, int]] = []
    for item in items:
        require(isinstance(item, dict), "generated_subject_videos item must be an object")
        role_id = str(item.get("role_id") or "")
        appearance_id = str(item.get("appearance_id") or "")
        asset_id = str(item.get("asset_id") or "")
        require(role_id, "subject video item is missing role_id")
        require(appearance_id, f"{role_id} subject video item is missing appearance_id")
        require(asset_id, f"{role_id}/{appearance_id} subject video item is missing asset_id")

        result = _video_result_from_item(item)
        require(result.video_url, f"{asset_id} has no downloadable video URL in raw_response")

        output_path = layout.video_asset_path(project_dir, "roles", asset_id)
        existing_path = layout.existing_project_file(project_dir, output_path)
        if existing_path:
            asset_path = existing_path
            byte_count = (project_dir / asset_path).stat().st_size
            reused.append((asset_id, asset_path, byte_count))
        else:
            asset_path = await media_store.write_generated_video(project_dir, output_path, result)
            require(asset_path, f"{asset_id} did not write a local video file")
            byte_count = (project_dir / asset_path).stat().st_size
            restored.append((asset_id, asset_path, byte_count))

        item["asset_path"] = asset_path
        item["asset_url"] = result.video_url
        item["provider"] = result.provider or item.get("provider") or "kling_omni"
        item["model"] = result.model or item.get("model") or "kling-v3-omni"
        item["task_id"] = result.task_id
        item["task_status"] = result.task_status
        item["request_id"] = result.request_id
        item["usage"] = result.usage

        role = state.roles.get(role_id)
        require(role is not None, f"state is missing role {role_id}")
        appearance = _find_appearance(role, appearance_id)
        require(appearance is not None, f"state role {role_id} is missing appearance {appearance_id}")
        appearance.subject_video_asset_id = asset_id
        appearance.subject_video_asset_path = asset_path
        appearance.subject_video_asset_url = result.video_url
        appearance.subject_video_intro_text = item.get("intro_text") or appearance.subject_video_intro_text
        appearance.subject_video_provider = result.provider or str(item.get("provider") or "kling_omni")
        appearance.subject_video_model = result.model or str(item.get("model") or "kling-v3-omni")
        appearance.subject_video_task_id = result.task_id
        appearance.subject_video_task_status = result.task_status
        appearance.subject_video_request_id = result.request_id
        appearance.subject_video_usage = result.usage
        appearance.subject_video_raw_response = result.raw_response

    repo.write_json(node_output_path, node_output)
    repo.save_state(project_dir, state)

    require(restored or reused, "no subject videos were restored or reused")
    print("restore_huyao_subject_videos=ok")
    for asset_id, asset_path, byte_count in restored:
        print(f"downloaded {asset_id} -> {asset_path} bytes={byte_count}")
    for asset_id, asset_path, byte_count in reused:
        print(f"reused {asset_id} -> {asset_path} bytes={byte_count}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
