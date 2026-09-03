from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from autodrama.config import load_settings
from autodrama.core.model_catalog import NodeModelSettings
from autodrama.core.schemas import ProjectState, ScriptBundle
from autodrama.providers.router import ProviderRouter
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore


ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = (ROOT / ".tmp" / "script_cinematic_adapt_model_compare").resolve()
NODE_NAME = "script_cinematic_adapt"
SOURCE_PATH = ROOT / "inputs" / "fangu_first_3_chapters" / "chap0003_排械之印.txt"

MODEL_CASES = [
    {
        "model": "aibox:gpt-5.6-luna",
        "params": {
            "reasoning_effort": "xhigh",
            "temperature": 0.35,
            "response_format": "json_object",
            "console_stream": False,
        },
    },
    {
        "model": "aibox:gemini-3.6-flash",
        "params": {
            "reasoning_effort": "xhigh",
            "temperature": 0.35,
            "response_format": "json_object",
            "max_output_tokens": 8192,
            "console_stream": False,
        },
    },
    {
        "model": "deepseek:deepseek-v4-flash",
        "params": {
            "reasoning_effort": "max",
            "thinking_enabled": True,
            "temperature": 0.35,
        },
    },
]

KEY_ENTITIES = (
    "镇灵司",
    "鬼医婆婆",
    "陆沉",
    "排械之印",
    "原初之火",
    "素问真人",
    "陆云袖",
    "天工司",
    "神机院",
)
FACT_ANCHORS = (
    "她给自己装了民械",
    "目的：掩盖子嗣排械之印",
    "你母亲也是排械体",
    "平均存活十一个月",
    "最长的三年零两个月",
    "一百三十万个排械体",
    "别装",
)
CAMERA_TERMS = ("镜头", "特写", "推近", "拉远", "切到", "机位", "景别", "音效")


def prepare_result_root() -> None:
    expected_parent = (ROOT / ".tmp").resolve()
    if RESULT_ROOT.parent != expected_parent:
        raise RuntimeError(f"Refusing to clear unexpected result path: {RESULT_ROOT}")
    if RESULT_ROOT.exists():
        shutil.rmtree(RESULT_ROOT)
    (RESULT_ROOT / "blind").mkdir(parents=True)


def source_excerpt() -> str:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\r?\n\s*\r?\n", SOURCE_PATH.read_text(encoding="utf-8"))
        if paragraph.strip()
    ]
    if len(paragraphs) < 2:
        raise ValueError(f"Expected at least two paragraphs in {SOURCE_PATH}")
    return "\n\n".join(paragraphs[:2])


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def sentence_chunks(text: str) -> list[str]:
    return [
        chunk.strip()
        for chunk in re.split(r"(?<=[。！？!?])", text)
        if len(compact_text(chunk)) >= 8
    ]


def metrics(source: str, output: str, elapsed_seconds: float) -> dict[str, Any]:
    compact_source = compact_text(source)
    compact_output = compact_text(output)
    source_sentences = sentence_chunks(source)
    exact_sentences = sum(compact_text(sentence) in compact_output for sentence in source_sentences)
    camera_hits = {term: output.count(term) for term in CAMERA_TERMS if term in output}
    format_marker_lines = sum(
        bool(re.match(r"\s*(?:[-*+]\s+|\d+[.)、]\s*|#+\s+|\|)", line))
        for line in output.splitlines()
        if line.strip()
    )
    return {
        "source_chars": len(compact_source),
        "output_chars": len(compact_output),
        "length_ratio": round(len(compact_output) / max(1, len(compact_source)), 4),
        "sequence_similarity": round(
            difflib.SequenceMatcher(None, compact_source, compact_output, autojunk=False).ratio(),
            4,
        ),
        "exact_sentence_retention": f"{exact_sentences}/{len(source_sentences)}",
        "entity_retention": f"{sum(entity in output for entity in KEY_ENTITIES)}/{len(KEY_ENTITIES)}",
        "fact_anchor_retention": f"{sum(anchor in output for anchor in FACT_ANCHORS)}/{len(FACT_ANCHORS)}",
        "camera_term_hits": camera_hits,
        "format_marker_lines": format_marker_lines,
        "paragraphs": len([part for part in re.split(r"\n\s*\n", output) if part.strip()]),
        "elapsed_seconds": round(elapsed_seconds, 2),
    }


async def run_case(
    *,
    base_settings: Any,
    state: ProjectState,
    source: str,
    case: dict[str, Any],
) -> tuple[str, float]:
    settings = base_settings.model_copy(deep=True)
    settings.nodes[NODE_NAME] = NodeModelSettings.model_validate(case)
    provider = ProviderRouter(settings).text("script", node_name=NODE_NAME)
    service = ScriptService(PromptStore())
    started = time.perf_counter()
    result = await service.script_cinematic_adapt(
        state,
        provider,
        episode_key="episode_003_excerpt",
        raw_script=source,
    )
    return result.cinematic_script.strip(), time.perf_counter() - started


async def main() -> None:
    prepare_result_root()
    source = source_excerpt()
    prompt = PromptStore().render(NODE_NAME, raw_script=source)
    base_settings = load_settings(ROOT / "fangu.yaml")
    for case in MODEL_CASES:
        spec = base_settings.model_catalog.require(str(case["model"]))
        provider_settings = base_settings.providers[spec.provider_name]
        if not provider_settings.secret("api_key_env"):
            raise RuntimeError(f"Missing configured API credential for provider {spec.provider_name}")

    state = ProjectState(
        project_id="script_cinematic_adapt_model_compare",
        title="Script Cinematic Adapt Model Comparison",
        raw_script=source,
        script=ScriptBundle(raw_script=source),
    )
    shuffled_cases = list(MODEL_CASES)
    secrets.SystemRandom().shuffle(shuffled_cases)
    mapping: dict[str, str] = {}
    blind_results: dict[str, dict[str, Any]] = {}

    (RESULT_ROOT / "input.txt").write_text(source, encoding="utf-8", newline="\n")
    (RESULT_ROOT / "prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
    for index, case in enumerate(shuffled_cases):
        blind_id = chr(ord("A") + index)
        print(f"candidate {blind_id}: running", flush=True)
        output, elapsed_seconds = await run_case(
            base_settings=base_settings,
            state=state,
            source=source,
            case=case,
        )
        if not output:
            raise ValueError(f"Candidate {blind_id} returned empty cinematic_script")
        mapping[blind_id] = str(case["model"])
        blind_results[blind_id] = {
            "metrics": metrics(source, output, elapsed_seconds),
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        }
        (RESULT_ROOT / "blind" / f"{blind_id}.txt").write_text(
            output,
            encoding="utf-8",
            newline="\n",
        )
        print(f"candidate {blind_id}: complete", flush=True)

    (RESULT_ROOT / "blind_metrics.json").write_text(
        json.dumps(blind_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    (RESULT_ROOT / "mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(f"comparison artifacts: {RESULT_ROOT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
