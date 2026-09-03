from __future__ import annotations

import re
from datetime import datetime


def slugify(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned or fallback


def make_project_id(title: str, template: str) -> str:
    now = datetime.now()
    values = {
        "date": now.strftime("%Y%m%d_%H%M%S"),
        "slug": slugify(title),
    }
    return template.format(**values)


def normalize_id(prefix: str, value: str) -> str:
    return f"{prefix}_{slugify(value).lower()}"
