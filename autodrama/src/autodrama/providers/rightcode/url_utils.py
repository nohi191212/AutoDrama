from __future__ import annotations

from typing import Any

from autodrama.config import ProviderSettings


RIGHTCODE_SERVICE_BASE_PATHS = ("/draw", "/codex")
RIGHTCODE_API_SUFFIXES = (
    "/v1/responses",
    "/responses",
    "/v1/images/generations",
    "/images/generations",
    "/v1/chat/completions",
    "/chat/completions",
)


def _join_url(base_url: str, path: str) -> str:
    path_value = str(path or "").strip()
    if not path_value:
        return base_url.rstrip("/")
    if path_value.startswith(("http://", "https://")):
        return path_value.rstrip("/")
    return f"{base_url.rstrip('/')}/{path_value.lstrip('/')}"


def _strip_suffix(value: str, suffixes: tuple[str, ...]) -> str:
    result = value.rstrip("/")
    for suffix in suffixes:
        if result.endswith(suffix):
            return result[: -len(suffix)].rstrip("/")
    return result


def rightcode_service_root(base_url: str) -> str:
    root = _strip_suffix(base_url, RIGHTCODE_API_SUFFIXES)
    return _strip_suffix(root, RIGHTCODE_SERVICE_BASE_PATHS)


def _nested_option(options: dict[str, Any], option_name: str, key: str | None) -> Any:
    if not key:
        return None
    value = options.get(option_name)
    if not isinstance(value, dict):
        return None
    if key in value:
        return value[key]
    key_lower = key.lower()
    for candidate_key, candidate_value in value.items():
        if str(candidate_key).lower() == key_lower:
            return candidate_value
    return None


def _first_option(options: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = options.get(key)
        if value is not None:
            return value
    return None


def resolve_rightcode_endpoint(
    settings: ProviderSettings,
    *,
    capability: str,
    model_key: str,
    model: str,
    default_base_path: str,
    api_path: str,
    default_root: str = "https://www.right.codes",
) -> str:
    """Resolve RightCode root/base-path/API-path config into a concrete endpoint."""

    options = settings.options or {}
    model_name = str(model or "").strip()

    endpoint = (
        _nested_option(options, "model_endpoints", model_name)
        or _first_option(options, f"{model_key}_endpoint", f"{capability}_endpoint")
    )
    if endpoint:
        endpoint_value = str(endpoint).strip()
        if endpoint_value.startswith(("http://", "https://")):
            return endpoint_value.rstrip("/")
        root = rightcode_service_root(settings.base_url or default_root)
        return _join_url(root, endpoint_value)

    model_base_url = _nested_option(options, "model_base_urls", model_name)
    keyed_base_url = _first_option(options, f"{model_key}_base_url", f"{capability}_base_url")
    base_url = str(model_base_url or keyed_base_url or settings.base_url or default_root).rstrip("/")

    base_path = (
        _nested_option(options, "model_base_paths", model_name)
        or _first_option(options, f"{model_key}_base_path", f"{capability}_base_path")
    )
    if base_path is None and not (model_base_url or keyed_base_url):
        base_path = default_base_path

    base_without_api = _strip_suffix(base_url, RIGHTCODE_API_SUFFIXES)
    if base_path:
        service_base = _join_url(rightcode_service_root(base_without_api), str(base_path))
    else:
        service_base = base_without_api
    return _join_url(service_base, api_path)
