from __future__ import annotations

import json
import re
from typing import Any

from autodrama.core.errors import ProviderBadResponseError


def _raw_decode_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ProviderBadResponseError("Provider returned empty content")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raw_decoded = _raw_decode_first_json_object(text)
        if raw_decoded is not None:
            return raw_decoded

        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            return parse_json_object(fenced.group(1))

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ProviderBadResponseError(f"Failed to parse JSON object from provider content: {exc}") from exc
        else:
            raise ProviderBadResponseError("Provider content does not contain a JSON object")

    if not isinstance(parsed, dict):
        raise ProviderBadResponseError("Provider JSON response is not an object")
    return parsed
