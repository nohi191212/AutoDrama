from __future__ import annotations

import base64
import mimetypes
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from autodrama.providers.base import AssetRef


def _utc_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for date_format in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        resolved = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return resolved.replace(tzinfo=resolved.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        resolved = parsedate_to_datetime(text)
        return resolved.replace(tzinfo=resolved.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _epoch_datetime(value: str) -> datetime | None:
    try:
        timestamp = int(str(value or "").strip())
    except ValueError:
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def signed_url_expiration(url: str) -> datetime | None:
    """Return a recognized signed-URL expiry timestamp, or None for ordinary URLs."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.query:
        return None
    query = {
        str(key).casefold(): str(values[-1])
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if values
    }

    for date_key, duration_key in (
        ("x-amz-date", "x-amz-expires"),
        ("x-goog-date", "x-goog-expires"),
        ("x-oss-date", "x-oss-expires"),
    ):
        signed_at = _utc_datetime(query.get(date_key, ""))
        try:
            valid_seconds = int(query.get(duration_key, ""))
        except ValueError:
            valid_seconds = 0
        if signed_at is not None and valid_seconds > 0:
            return signed_at + timedelta(seconds=valid_seconds)

    for key in ("q-key-time", "q-sign-time"):
        value = query.get(key, "")
        if ";" in value:
            _start, expires = value.rsplit(";", 1)
            if resolved := _epoch_datetime(expires):
                return resolved

    if resolved := _utc_datetime(query.get("se", "")):
        return resolved
    expires_value = query.get("expires", "")
    return _epoch_datetime(expires_value) or _utc_datetime(expires_value)


def is_remote_url_expired(
    url: str,
    *,
    now: datetime | None = None,
    refresh_margin_seconds: float = 60,
) -> bool:
    expires_at = signed_url_expiration(url)
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=current.tzinfo or timezone.utc).astimezone(timezone.utc)
    return current + timedelta(seconds=max(0, refresh_margin_seconds)) >= expires_at


def local_ref_path(ref: AssetRef) -> Path | None:
    if not ref.path:
        return None
    value = str(ref.path)
    if value.startswith(("http://", "https://", "data:", "asset://")):
        return None
    path = Path(value).expanduser()
    return path if path.exists() and path.is_file() else None


def refresh_expired_image_ref_urls(refs: Iterable[AssetRef] | None) -> int:
    """Clear recognized expired URLs so the local image can be re-uploaded through ToAPI."""
    refreshed = 0
    for ref in refs or []:
        if not isinstance(ref, AssetRef):
            continue
        if ref.type != "image" or not ref.url or not is_remote_url_expired(ref.url):
            continue
        if local_ref_path(ref) is None:
            continue
        old_url = ref.url
        ref.url = None
        ref.metadata["expired_asset_url"] = old_url
        ref.metadata["expired_url_cleared_for_local_refresh"] = True
        refreshed += 1
    return refreshed


def file_to_data_url(path: Path, *, expected_type: str | None = None, default_mime: str | None = None) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or default_mime
    if not mime_type:
        return None
    if expected_type and not mime_type.startswith(f"{expected_type}/"):
        return None
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def asset_uri(ref: AssetRef) -> str | None:
    for value in (ref.path, ref.id):
        if isinstance(value, str) and value.startswith("asset://"):
            return value
    return None


def ref_url_or_data(
    ref: AssetRef,
    *,
    expected_type: str | None = None,
    default_mime: str | None = None,
    allow_asset_uri: bool = True,
) -> str | None:
    if ref.url:
        return ref.url
    if allow_asset_uri and (uri := asset_uri(ref)):
        return uri
    if not ref.path:
        return None
    path_value = str(ref.path)
    if path_value.startswith(("http://", "https://", "data:")):
        return path_value
    return file_to_data_url(Path(path_value), expected_type=expected_type, default_mime=default_mime)
