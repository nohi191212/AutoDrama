from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAB_DIR = PROJECT_ROOT / ".tmp" / "gpt-image-2-template-evolution-20260805"
REPORT = LAB_DIR / "report-standalone.html"
RESULTS = LAB_DIR / "results.jsonl"


def main() -> int:
    document = REPORT.read_text(encoding="utf-8")
    assert REPORT.stat().st_size > 10_000_000, "report is unexpectedly small"
    assert "8.9965" in document and "0.038634" in document
    assert "c03-balanced" in document
    assert "75 / 200" in document
    assert "54 维评分" in document

    data_uris = re.findall(r'src="data:image/webp;base64,([A-Za-z0-9+/=]+)"', document)
    assert len(data_uris) == 53, f"expected 53 embedded images, found {len(data_uris)}"
    for payload in (data_uris[0], data_uris[-1]):
        with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
            assert max(image.width, image.height) >= 1024
            assert image.width * image.height >= 655_360
            assert image.format == "WEBP"

    references = re.findall(r'(?:src|href)="([^"]+)"', document)
    bad_references = [value for value in references if not value.startswith(("data:", "#"))]
    assert not bad_references, f"report has external/local dependencies: {bad_references[:3]}"

    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    image_rows = [row for row in rows if row.get("kind") == "image"]
    gemini_rows = [row for row in rows if row.get("kind") == "gemini"]
    assert len(image_rows) == 75
    assert sum(bool(row.get("success")) for row in image_rows) == 53
    assert len(gemini_rows) == 111

    print(
        f"ok report={REPORT} bytes={REPORT.stat().st_size} "
        f"embedded_images={len(data_uris)} image_calls={len(image_rows)} gemini_calls={len(gemini_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
