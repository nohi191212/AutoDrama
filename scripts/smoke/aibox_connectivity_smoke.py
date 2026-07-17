from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402


def exception_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 8:
        parts.append(f"{type(current).__name__}({current!s}) args={current.args!r}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def proxy_summary() -> str:
    values: list[str] = []
    for key, value in os.environ.items():
        if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}:
            continue
        parsed = urlparse(value)
        safe_value = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.hostname else "<set>"
        values.append(f"{key}={safe_value}")
    return ", ".join(sorted(values)) or "none"


async def probe(url: str, *, verify: bool, trust_env: bool) -> None:
    label = f"verify={verify} trust_env={trust_env}"
    try:
        async with httpx.AsyncClient(verify=verify, trust_env=trust_env, timeout=20) as client:
            response = await client.get(url)
        print(f"{label}: HTTP {response.status_code} content_type={response.headers.get('content-type', '-')}")
    except BaseException as exc:
        print(f"{label}: ERROR {exception_chain(exc)}")


async def concurrent_probe(url: str, *, count: int = 12) -> None:
    async with httpx.AsyncClient(verify=True, trust_env=False, timeout=20) as client:
        async def request_once() -> str:
            try:
                response = await client.get(url)
                return f"HTTP {response.status_code}"
            except BaseException as exc:
                return f"ERROR {exception_chain(exc)}"

        results = await asyncio.gather(*(request_once() for _ in range(count)))
    summary = {result: results.count(result) for result in sorted(set(results))}
    print(f"concurrent={count}: {summary}")


async def main() -> None:
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    settings = load_settings(config_path)
    base_url = str(settings.providers["aibox"].base_url or "https://api.lk888.ai").rstrip("/")
    url = f"{base_url}/v1/media/status"
    print(f"url={url}")
    print(f"proxy={proxy_summary()}")
    await probe(url, verify=True, trust_env=True)
    await probe(url, verify=True, trust_env=False)
    await probe(url, verify=False, trust_env=False)
    await concurrent_probe(url)


if __name__ == "__main__":
    asyncio.run(main())
