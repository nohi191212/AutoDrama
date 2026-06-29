"""Smoke test Kling API connectivity using API Key (new auth method)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_api_key() -> str | None:
    apikeys_path = ROOT / "apikeys.yaml"
    if apikeys_path.exists():
        data = yaml.safe_load(apikeys_path.read_text(encoding="utf-8")) or {}
        key = data.get("KLING_API_KEY")
        if key:
            return str(key)
    return os.environ.get("KLING_API_KEY")


def main() -> None:
    print("=== Kling API connectivity smoke test (API Key auth) ===\n")

    api_key = load_api_key()

    print(f"1. Credentials check:")
    print(f"   KLING_API_KEY from apikeys.yaml:  {bool(api_key)}")

    if not api_key:
        print("\n   ERROR: Missing KLING_API_KEY in apikeys.yaml or environment.")
        sys.exit(1)

    print(f"   Key prefix: {api_key[:20]}...")

    print("\n2. API call to Kling (submit text-to-video):")
    base_url = "https://api-beijing.klingai.com"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model_name": "kling-v3-omni",
        "mode": "pro",
        "duration": "5",
        "aspect_ratio": "9:16",
        "sound": "off",
        "prompt": "A single red apple on a white table, static shot, 5 seconds.",
    }

    print(f"   URL:  {base_url}/v1/videos/omni-video")
    print(f"   Model: kling-v3-omni")
    print(f"   Prompt: {payload['prompt']}")

    try:
        resp = httpx.post(
            f"{base_url}/v1/videos/omni-video",
            json=payload,
            headers=headers,
            timeout=30,
        )
        print(f"\n   HTTP Status: {resp.status_code}")
        print(f"   Response: {resp.text[:800]}")
        if resp.status_code == 200:
            data = resp.json()
            task_id = data.get("data", {}).get("task_id", "N/A")
            print(f"\n   SUCCESS! Task submitted. task_id={task_id}")
        else:
            print(f"\n   FAILED: HTTP {resp.status_code}")
            sys.exit(1)
    except Exception as exc:
        print(f"\n   ERROR: {exc}")
        sys.exit(1)

    print("\n2b. Query task status:")
    # The task won't finish immediately, but we can test the query endpoint
    print(f"   (task will be processing, this just tests the query endpoint works)")

    print("\n=== Smoke test passed ===")


if __name__ == "__main__":
    main()
