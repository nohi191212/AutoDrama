"""Query a Kling omni-video task and download the video if it is complete."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import yaml


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / ".tmp" / "kling_prompt_tests"
BASE_URL = "https://api-beijing.klingai.com"
ENDPOINT = "/v1/videos/omni-video"
TASK_ID = os.environ.get("KLING_QUERY_TASK_ID", "901207354090872915").strip()
OUTPUT_STEM = os.environ.get("KLING_QUERY_OUTPUT_STEM", "基准——单角色").strip() or TASK_ID
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("KLING_REQUEST_TIMEOUT_SECONDS", "300") or "300")
DOWNLOAD_MAX_ATTEMPTS = int(os.environ.get("KLING_DOWNLOAD_MAX_ATTEMPTS", "5") or "5")


def load_api_key() -> str | None:
    key = os.environ.get("KLING_API_KEY")
    if key:
        return key

    apikeys_path = ROOT / "apikeys.yaml"
    if apikeys_path.exists():
        data = yaml.safe_load(apikeys_path.read_text(encoding="utf-8")) or {}
        key = data.get("KLING_API_KEY")
        if key:
            return str(key)

    smoke_path = ROOT / "scripts" / "smoke" / "kling_first_shot_multi_image_smoke.py"
    if smoke_path.exists():
        match = re.search(r'KLING_API_KEY\s*=\s*"([^"]+)"', smoke_path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return None


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def query_task(task_id: str) -> dict[str, Any]:
    api_key = load_api_key()
    if not api_key:
        raise RuntimeError("Missing KLING_API_KEY in environment, apikeys.yaml, or smoke script")

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=60.0, write=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response = client.get(f"{BASE_URL}{ENDPOINT}/{task_id}")
    try:
        body = response.json()
    except ValueError:
        body = {"raw_text": response.text[:2000]}
    if response.status_code >= 400:
        body["_http_status_code"] = response.status_code
        raise RuntimeError(f"Kling query failed HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)[:1000]}")
    return body


def first_video_url(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("video_url", "videoUrl", "url"):
            candidate = value.get(key)
            found = first_video_url(candidate)
            if found:
                return found
        for item in value.values():
            found = first_video_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_video_url(item)
            if found:
                return found
    elif isinstance(value, str):
        if value.startswith(("http://", "https://")) and any(token in value.lower() for token in (".mp4", ".mov", ".webm", "video")):
            return value
    return None


def download_video(video_url: str, path: Path) -> None:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=60.0, write=REQUEST_TIMEOUT_SECONDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        expected_size = content_length(client, video_url)
        for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
            existing_size = path.stat().st_size if path.exists() else 0
            if expected_size is not None and existing_size >= expected_size:
                return

            headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
            mode = "ab" if existing_size else "wb"
            try:
                with client.stream("GET", video_url, headers=headers) as response:
                    if existing_size and response.status_code == 200:
                        mode = "wb"
                    response.raise_for_status()
                    with path.open(mode) as file:
                        for chunk in response.iter_bytes():
                            file.write(chunk)
            except httpx.HTTPError:
                if attempt >= DOWNLOAD_MAX_ATTEMPTS:
                    raise
                continue

        final_size = path.stat().st_size if path.exists() else 0
        if expected_size is not None and final_size < expected_size:
            raise RuntimeError(f"Downloaded {final_size} bytes, expected {expected_size} bytes")


def content_length(client: httpx.Client, url: str) -> int | None:
    try:
        response = client.head(url)
        if response.status_code >= 400:
            return None
        value = response.headers.get("content-length")
        return int(value) if value and value.isdigit() else None
    except httpx.HTTPError:
        return None


def main() -> None:
    if not TASK_ID:
        raise RuntimeError("Set KLING_QUERY_TASK_ID")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    query_path = OUT_DIR / f"{OUTPUT_STEM}_query.json"
    body = query_task(TASK_ID)
    save_json(query_path, body)

    status = str(body.get("data", {}).get("task_status") or body.get("task_status") or "")
    print(f"task_id={TASK_ID}")
    print(f"task_status={status}")
    print(f"query_json={query_path}")

    video_url = first_video_url(body)
    if not video_url:
        print("video_url=<none>")
        return

    video_path = OUT_DIR / f"{OUTPUT_STEM}_{TASK_ID}.mp4"
    download_video(video_url, video_path)
    print(f"video_url={video_url}")
    print(f"video_path={video_path}")


if __name__ == "__main__":
    main()
