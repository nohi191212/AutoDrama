from __future__ import annotations

from typing import Any

import httpx

from autodrama.core.errors import ProviderBadResponseError


def request_id_from_response(body: dict[str, Any], response: httpx.Response, *header_keys: str) -> str | None:
    for key in ("request_id", "requestId", "id"):
        value = body.get(key)
        if value:
            return str(value)
    for key in header_keys or ("x-request-id", "x-requestid", "x-tt-logid"):
        value = response.headers.get(key)
        if value:
            return str(value)
    return None


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int | float | httpx.Timeout,
    label: str,
) -> tuple[dict[str, Any], httpx.Response]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.ConnectError as exc:
        raise ProviderBadResponseError(
            f"{label} connection failed before receiving an HTTP response. Check network/proxy/TLS settings for {url}: {exc}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ProviderBadResponseError(f"{label} timed out while calling {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ProviderBadResponseError(f"{label} request failed while calling {url}: {exc}") from exc

    if response.status_code >= 400:
        raise ProviderBadResponseError(f"{label} failed with HTTP {response.status_code}: {response.text[:500]}")

    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderBadResponseError(f"{label} returned non-JSON response: {exc}") from exc
    if not isinstance(body, dict):
        raise ProviderBadResponseError(f"{label} JSON response is not an object")
    return body, response


def safe_headers(headers: httpx.Headers, *, secret_names: set[str] | None = None) -> dict[str, str]:
    secret_names = secret_names or {"authorization", "x-api-key", "x-api-access-key", "xi-api-key"}
    return {
        key: ("<secret omitted>" if key.lower() in secret_names else value)
        for key, value in headers.items()
    }
