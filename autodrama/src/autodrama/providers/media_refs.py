from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from autodrama.providers.base import AssetRef


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
