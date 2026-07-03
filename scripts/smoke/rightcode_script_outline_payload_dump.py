from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.schemas import BudgetState, ProjectState, ScriptBundle, ScriptOutlineOutput
from autodrama.providers.router import ProviderRouter
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore


def payload_summary(provider: Any, payload: dict[str, Any], *, schema_name: str, prompt: str) -> dict[str, Any]:
    input_messages = payload.get("input") if isinstance(payload.get("input"), list) else []
    content = []
    if input_messages and isinstance(input_messages[0], dict):
        raw_content = input_messages[0].get("content")
        if isinstance(raw_content, list):
            content = raw_content

    return {
        "endpoint": getattr(provider, "endpoint", None),
        "provider": getattr(provider, "name", None),
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "temperature": payload.get("temperature"),
        "response_format": payload.get("response_format"),
        "reasoning": payload.get("reasoning"),
        "schema_name": schema_name,
        "prompt_chars": len(prompt),
        "content_part_types": [part.get("type") for part in content if isinstance(part, dict)],
        "first_content_text": (
            str(content[0].get("text", ""))[:120] if content and isinstance(content[0], dict) else ""
        ),
        "instructions": str(payload.get("instructions", ""))[:200],
    }


def build_bound_payload(
    bound_provider: Any,
    prompt: str,
    schema: type,
    *,
    temperature: float,
    metadata: dict[str, Any],
) -> tuple[Any, dict[str, Any], dict[str, Any], float]:
    target = getattr(bound_provider, "_provider", bound_provider)
    effective_temperature = temperature
    effective_metadata = dict(metadata)
    binding = getattr(bound_provider, "model_binding", None)
    if binding is not None:
        effective_temperature = binding.params.get("temperature", effective_temperature)
        metadata_method = getattr(bound_provider, "_metadata", None)
        if callable(metadata_method):
            effective_metadata = metadata_method(effective_metadata)

    payload = target.build_payload(
        prompt,
        schema,
        temperature=effective_temperature,
        metadata=effective_metadata,
    )
    return target, payload, effective_metadata, effective_temperature


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    parser.add_argument("--project", default=None)
    parser.add_argument("--output", default=".tmp/rightcode_script_outline_payload_dump.json")
    parser.add_argument("--payload-output", default=None)
    parser.add_argument("--endpoint-output", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    if not settings.project.script_outline_file:
        raise ValueError("project.script_outline_file is not configured")
    raw_script = settings.project.script_outline_file.read_text(encoding="utf-8")
    project_id = args.project or settings.project.id or "payload_dump"
    state = ProjectState(
        project_id=project_id,
        title=settings.project.title or project_id,
        raw_script=raw_script,
        script=ScriptBundle(raw_script=raw_script),
        budget=BudgetState.model_validate(settings.budget.model_dump()),
        metadata={
            "episode_count": settings.project.episode_count,
            "episode_duration_seconds": settings.project.episode_duration_seconds,
        },
    )

    router = ProviderRouter(settings)
    service = ScriptService(PromptStore())
    actual_prompt = service.prompts.render(
        "script_outline",
        raw_script=state.raw_script,
        episode_duration_seconds=service.episode_duration_seconds(state),
    )
    actual_bound_provider = router.text("script", node_name="script_outline")
    actual_target, actual_payload, actual_metadata, actual_temperature = build_bound_payload(
        actual_bound_provider,
        actual_prompt,
        ScriptOutlineOutput,
        temperature=0.7,
        metadata={
            "node_name": "script_outline",
            "project_id": state.project_id,
            "required_mapping_field": "episode_outlines",
        },
    )

    from pydantic import BaseModel

    class SmokeAnswerOutput(BaseModel):
        answer: str

    smoke_prompt = "问题：1+1等于几？只输出 JSON，answer 字段填数字字符串。"
    smoke_bound_provider = router.text("script", node_name="script_outline")
    smoke_target, smoke_payload, smoke_metadata, smoke_temperature = build_bound_payload(
        smoke_bound_provider,
        smoke_prompt,
        SmokeAnswerOutput,
        temperature=0,
        metadata={
            "node_name": "script_outline_simple_json_smoke",
            "project_id": "rightcode_simple_json_smoke",
        },
    )

    actual_summary = payload_summary(
        actual_target,
        actual_payload,
        schema_name="ScriptOutlineOutput",
        prompt=actual_prompt,
    )
    smoke_summary = payload_summary(
        smoke_target,
        smoke_payload,
        schema_name="SmokeAnswerOutput",
        prompt=smoke_prompt,
    )
    comparison = {
        "same_endpoint": actual_summary["endpoint"] == smoke_summary["endpoint"],
        "same_model": actual_summary["model"] == smoke_summary["model"],
        "same_stream": actual_summary["stream"] == smoke_summary["stream"],
        "same_response_format": actual_summary["response_format"] == smoke_summary["response_format"],
        "same_reasoning": actual_summary["reasoning"] == smoke_summary["reasoning"],
        "same_content_part_types": actual_summary["content_part_types"] == smoke_summary["content_part_types"],
        "different_temperature": actual_temperature != smoke_temperature,
        "different_schema": actual_summary["schema_name"] != smoke_summary["schema_name"],
        "different_prompt": actual_prompt != smoke_prompt,
        "actual_metadata": actual_metadata,
        "smoke_metadata": smoke_metadata,
    }
    output = {
        "actual_script_outline": actual_summary,
        "simple_json_smoke": smoke_summary,
        "comparison": comparison,
    }

    output_path = (ROOT / args.output).resolve()
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.payload_output:
        payload_path = (ROOT / args.payload_output).resolve()
        payload_path.parent.mkdir(exist_ok=True)
        payload_path.write_text(json.dumps(actual_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.endpoint_output:
        endpoint_path = (ROOT / args.endpoint_output).resolve()
        endpoint_path.parent.mkdir(exist_ok=True)
        endpoint_path.write_text(str(actual_summary["endpoint"]) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
