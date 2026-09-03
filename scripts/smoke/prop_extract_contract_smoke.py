from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import PropExtractOutput  # noqa: E402


def prop_payload(*, assets: list[dict[str, object]] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "木杖",
        "intro": "叶凡长期使用的木杖。",
        "episode_keys": ["episode_001"],
        "source_chapters": [],
    }
    if assets is not None:
        payload["assets"] = assets
    return {"props": [payload]}


def main() -> int:
    valid = PropExtractOutput.model_validate(
        prop_payload(
            assets=[
                {
                    "name": "base",
                    "asset_role": "base",
                    "desc": "一根有磨损痕迹的木杖。",
                },
                {
                    "name": "损坏",
                    "asset_role": "variant",
                    "reference_asset_name": "base",
                    "status": "damaged",
                    "desc": "木杖出现裂痕。",
                },
            ]
        )
    )
    assert valid.props[0].assets[0].asset_role == "base"

    for invalid in (
        prop_payload(),
        prop_payload(
            assets=[
                {
                    "name": "损坏",
                    "asset_role": "variant",
                    "reference_asset_name": "base",
                    "desc": "木杖出现裂痕。",
                }
            ]
        ),
    ):
        try:
            PropExtractOutput.model_validate(invalid)
        except ValidationError:
            continue
        raise AssertionError("invalid prop asset structure unexpectedly passed schema validation")

    prompt = (ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "prop_extract" / "default.md").read_text(
        encoding="utf-8"
    )
    assert "非空的 `assets` 数组" in prompt
    assert "asset_role=\"base\"" in prompt
    print("prop extract contract smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
