from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "autodrama" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autodrama.core.schemas import SemanticProvenance


def _load_auditor():
    script_path = REPOSITORY_ROOT / "scripts" / "check_semantic_patterns.py"
    spec = importlib.util.spec_from_file_location("semantic_pattern_auditor", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load auditor: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class UnknownStateContract(BaseModel):
    state: Literal["known", "unknown", "unspecified"] | None = None


def main() -> None:
    provenance = SemanticProvenance(
        source="model",
        evidence=["explicit structured output"],
        confidence=0.75,
        model="contract-smoke",
    )
    round_trip = SemanticProvenance.model_validate_json(provenance.model_dump_json())
    assert round_trip == provenance
    assert json.loads(round_trip.model_dump_json())["schema_version"] == 1

    for invalid_confidence in (-0.01, 1.01):
        try:
            SemanticProvenance(source="migration", confidence=invalid_confidence)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"confidence accepted outside [0, 1]: {invalid_confidence}")

    assert UnknownStateContract.model_validate({"state": "unknown"}).state == "unknown"
    assert UnknownStateContract.model_validate({"state": "unspecified"}).state == "unspecified"
    assert UnknownStateContract.model_validate({}).state is None

    auditor = _load_auditor()
    violations = auditor.inspect_source(
        """
import re
STATE_MARKERS = ("受伤", "流血")
def infer(text):
    matched = any(marker in text for marker in STATE_MARKERS)
    direct = "奔跑" in text or "战斗" in text
    regex_match = re.search(r"激活|发光", text)
    rewritten = text.replace("挥剑", "攻击")
    rewritten = rewritten.replace("跪倒", "倒地")
    return matched or direct or bool(regex_match) or bool(rewritten)
"""
    )
    categories = {finding.category for finding in violations}
    assert {
        "named-semantic-collection",
        "semantic-membership-aggregate",
        "repeated-natural-language-membership",
        "natural-language-regex",
        "natural-language-rewrite",
    } <= categories, f"known semantic-pattern fixture was incompletely detected: {categories}"
    allowed_fixture = """
import re
URL_PATTERN = re.compile(r"https?://[^/]+")
SHOT_ID_PATTERN = re.compile(r"^shot_[0-9]+$")
ERROR_CODES = {"401", "429"}
PUNCTUATION = ("：", "；")
def parse(value):
    return value.startswith("shot_") or value in ERROR_CODES
"""
    assert auditor.inspect_source(allowed_fixture) == [], "mechanical syntax fixture produced a false positive"
    assert auditor._data_semantic_paths({"state_phrases": ["受伤", "流血"]}) == [
        ("state_phrases",)
    ], "runtime data rule collection was not detected"
    assert auditor._data_semantic_paths(
        {"visual_style": {"materials": ["布料", "金属"]}}
    ) == [], "structured business data produced a false positive"
    print("semantic schema contract smoke passed")


if __name__ == "__main__":
    main()
