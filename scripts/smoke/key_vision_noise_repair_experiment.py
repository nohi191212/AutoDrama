from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    KeyVisionPromptOutput,
    ProjectState,
    StaticAssetGenerationItem,
)
from autodrama.providers.aibox.image.banana import AiboxBananaImageProvider  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402
from autodrama.workflows.nodes.image_audit_nodes import (  # noqa: E402
    KeyVisionAuditDecision,
    KeyVisionImageAuditNode,
)
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402

from key_vision_real_project_experiment import (  # noqa: E402
    generate_image_with_experiment_retries,
    generate_json_with_experiment_retries,
    score_summary,
)


PROJECT_DIR = ROOT / "outputs" / "saodi_0803"
LAB = ROOT / ".tmp" / "key-vision-noise-repair-20260807"
REPORT_PATH = PROJECT_DIR / "docs" / "key_vision_noise_repair_report.html"
ORIGINAL_PROMPT_PATH = PROJECT_DIR / "logs" / "prompts" / "key_vision_image_generation" / "key_vision_original.prompt.txt"
BASELINE_IMAGE_PATH = PROJECT_DIR / "assets" / "images" / "key_visions" / "key_vision_original.png"
IMAGE_SIZE = "3840x2160"
BANANA_MODEL = "gemini-3-pro-image-preview"
AUDIT_INLINE_LIMIT_BYTES = 20 * 1024 * 1024
AUDIT_TARGET_BYTES = 18 * 1024 * 1024

NOISE_REPAIR_PROMPT = (
    "Edit the supplied image while preserving the exact composition, architecture, subject count, camera, "
    "lighting, color palette, spatial layout, and world content. Repair only visible AI rendering artifacts: "
    "synthetic high-frequency speckle, waxy or plastic microtexture, repeated procedural surface chatter, "
    "crunchy oversharpening, random bright or dark flecks, muddy local detail, and inconsistent texture frequency. "
    "Re-render clean coherent surfaces with stable geometry and natural material transitions. Do not add, remove, "
    "move, crop, redesign, stylize, or reinterpret any object. Do not introduce film grain, digital noise, extra "
    "particles, or decorative texture."
)

GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "gpt_original_redraw",
        "label": "GPT · 原提示词重画",
        "provider_label": "AIBOX GPT Image 2",
        "mode": "text-to-image redraw",
        "prompt_kind": "original_prompt",
        "provider_kind": "gpt",
        "uses_reference": "false",
    },
    {
        "id": "gpt_noise_repair",
        "label": "GPT · 噪点修复编辑",
        "provider_label": "AIBOX GPT Image 2",
        "mode": "reference-image edit",
        "prompt_kind": "noise_repair_prompt",
        "provider_kind": "gpt",
        "uses_reference": "true",
    },
    {
        "id": "banana_original_prompt",
        "label": "Banana · 原提示词编辑",
        "provider_label": "AIBOX Gemini Banana",
        "mode": "reference-image edit",
        "prompt_kind": "original_prompt",
        "provider_kind": "banana",
        "uses_reference": "true",
    },
    {
        "id": "banana_noise_repair",
        "label": "Banana · 噪点修复编辑",
        "provider_label": "AIBOX Gemini Banana",
        "mode": "reference-image edit",
        "prompt_kind": "noise_repair_prompt",
        "provider_kind": "banana",
        "uses_reference": "true",
    },
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redact_sensitive(value: Any, *, key: str = "") -> Any:
    key_lower = key.casefold()
    if any(marker in key_lower for marker in ("api_key", "apikey", "authorization", "access_token", "secret")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact_sensitive(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, key=key) for item in value]
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", value, flags=re.IGNORECASE)
        return value
    return value


def safe_error(exc: BaseException) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(redact_sensitive(str(exc)))[:2000],
    }


def prepare_audit_image(path: Path, label: str) -> Path:
    """Create a temporary, content-preserving audit copy only when needed.

    Gemini text auditing in this project inlines local images and rejects files
    above 20 MB. The original experiment image is never replaced; this copy is
    only a JPEG/downscaled transport representation for the audit request.
    """
    if path.stat().st_size <= AUDIT_INLINE_LIMIT_BYTES:
        return path
    target = LAB / "audit_images" / f"{label}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size <= AUDIT_TARGET_BYTES:
        return target
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        for quality in (88, 82, 76, 70, 64, 58):
            image.save(target, format="JPEG", quality=quality, optimize=True, progressive=True)
            if target.stat().st_size <= AUDIT_TARGET_BYTES:
                return target
    return target


def relative_to_report(path: str | Path) -> str:
    return Path(os.path.relpath(Path(path), REPORT_PATH.parent)).as_posix()


def image_path_from_item(item: StaticAssetGenerationItem) -> Path:
    raw = Path(str(item.asset_path or ""))
    if raw.is_absolute():
        return raw
    return PROJECT_DIR / raw


def base_item(repo: ProjectRepository) -> StaticAssetGenerationItem:
    node_path = repo.layout.node_output_path(PROJECT_DIR, "key_vision_image_generation")
    payload = read_json(node_path)
    return StaticAssetGenerationItem.model_validate(payload["generated_assets"][0])


def implementation(provider: Any) -> Any:
    return getattr(provider, "_provider", provider)


def generation_contract(provider: Any, prompt: str, refs: list[AssetRef], metadata: dict[str, Any]) -> dict[str, Any]:
    impl = implementation(provider)
    contract: dict[str, Any] = {
        "provider": getattr(provider, "name", getattr(impl, "name", "unknown")),
        "model": getattr(provider, "model", getattr(impl, "model", "unknown")),
        "generation_endpoint": getattr(impl, "generation_endpoint", None),
        "status_endpoint": getattr(impl, "status_endpoint", None),
        "reference_count": len(refs),
        "metadata": {key: value for key, value in metadata.items() if key not in {"project_id"}},
    }
    builder = getattr(impl, "build_payload", None)
    if callable(builder):
        try:
            contract["payload"] = redact_sensitive(
                builder(prompt, refs=refs, metadata=metadata, size=IMAGE_SIZE)
            )
        except Exception as exc:
            contract["payload_error"] = safe_error(exc)
    return contract


async def generate_image(
    *,
    provider: Any,
    media_store: MediaStore,
    prompt: str,
    candidate_id: str,
    refs: list[AssetRef],
    metadata: dict[str, Any],
) -> tuple[Path, str | None, dict[str, Any]]:
    contract = generation_contract(provider, prompt, refs, metadata)
    result = await generate_image_with_experiment_retries(
        provider,
        prompt,
        refs=refs or None,
        size=IMAGE_SIZE,
        metadata=metadata,
    )
    output_path = LAB / "images" / f"{candidate_id}.png"
    await media_store.write_first_generated_image(LAB, output_path, result)
    response = {
        "provider": result.provider,
        "model": result.model,
        "task_id": result.task_id,
        "task_status": result.task_status,
        "request_id": result.request_id,
        "image_urls": result.image_urls,
        "usage": result.usage,
        "raw_response": redact_sensitive(result.raw_response),
    }
    payload = {
        "candidate_id": candidate_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "request_contract": contract,
        "response": response,
    }
    write_json(LAB / "responses" / "image" / f"{candidate_id}.json", payload)
    return output_path, result.image_urls[0] if result.image_urls else None, payload


async def audit_image(
    *,
    audit_node: KeyVisionImageAuditNode,
    audit_provider: Any,
    state: ProjectState,
    item: StaticAssetGenerationItem,
    image_path: Path,
    label: str,
) -> dict[str, Any]:
    request = audit_node._render_audit_request(state, item)
    metadata = {
        "node_name": audit_node.name,
        "project_id": state.project_id,
        "asset_id": item.asset_id,
        "prompt_asset_type": "key_vision_noise_repair_audit",
        "prompt_asset_name": label,
        "reasoning_effort": "high",
        "max_output_tokens": 16384,
    }
    audit_image_path = prepare_audit_image(image_path, label)
    ref = AssetRef(id=item.asset_id, type="image", path=str(audit_image_path), url=None)
    validation_feedback = ""
    decision: KeyVisionAuditDecision | None = None
    for attempt in range(1, 4):
        candidate = await generate_json_with_experiment_retries(
            audit_provider,
            request + validation_feedback,
            audit_node._decision_model(),
            temperature=0.1,
            refs=[ref],
            metadata=metadata,
        )
        state.budget.used_text_calls += 1
        if not isinstance(candidate, KeyVisionAuditDecision):
            raise TypeError("key vision audit provider returned an unexpected schema")
        try:
            decision = audit_node._normalize_decision(candidate, state=state, item=item)
            break
        except (TypeError, ValueError) as exc:
            if attempt >= 3:
                raise
            validation_feedback = (
                "\n\n上一份 JSON 未通过生产节点的 rubric 覆盖校验。"
                f"校验错误：{exc}。请逐一输出 rubric 中每个 dimension_id，保持所有 assessment 字段完整。"
            )
    if decision is None:
        raise AssertionError("audit did not return a decision")
    summary = score_summary(decision)
    summary.update(
        {
            "audit_prompt": request,
            "audit_model": getattr(audit_provider, "model", "unknown"),
            "audit_provider": getattr(audit_provider, "name", "unknown"),
            "audit_image_path": str(audit_image_path),
            "audit_completed_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    write_json(LAB / "responses" / "audit" / f"{label}.json", redact_sensitive(summary))
    return summary


def noise_score(audit: dict[str, Any] | None) -> float | None:
    if not audit:
        return None
    for row in audit.get("assessments") or []:
        if row.get("dimension_id") == "rendering_artifact_noise":
            return row.get("score")
    return None


def make_item(
    base: StaticAssetGenerationItem,
    *,
    candidate_id: str,
    prompt: str,
    image_path: Path,
    image_url: str | None,
    label: str,
) -> StaticAssetGenerationItem:
    return base.model_copy(
        update={
            "asset_id": candidate_id,
            "name": label,
            "prompt": prompt,
            "asset_path": str(image_path),
            "asset_url": image_url,
        }
    )


async def run_one(
    *,
    group: dict[str, str],
    repeat: int,
    original_prompt: str,
    noise_prompt: str,
    base_ref: AssetRef,
    base_item_value: StaticAssetGenerationItem,
    state: ProjectState,
    prompt_output: KeyVisionPromptOutput,
    image_provider: Any,
    banana_provider: Any,
    audit_provider: Any,
    audit_node: KeyVisionImageAuditNode,
    media_store: MediaStore,
) -> dict[str, Any]:
    candidate_id = f"{group['id']}_r{repeat:02d}"
    prompt = original_prompt if group["prompt_kind"] == "original_prompt" else noise_prompt
    refs = [base_ref] if group["uses_reference"] == "true" else []
    provider = image_provider if group["provider_kind"] == "gpt" else banana_provider
    node_name = "key_vision_image_generation" if group["provider_kind"] == "gpt" and not refs else "key_vision_edit"
    metadata: dict[str, Any] = {
        "node_name": node_name,
        "project_id": state.project_id,
        "asset_id": candidate_id,
        "asset_type": "key_vision",
        "prompt_asset_type": "key_vision_noise_repair_experiment",
        "prompt_asset_name": candidate_id,
        "size": IMAGE_SIZE,
        "quality": "high",
        "model": "gpt-image-2" if group["provider_kind"] == "gpt" else BANANA_MODEL,
        "aspectRatio": "16:9",
        "imageSize": "4K",
        "max_reference_images": 14,
    }
    generated_path, generated_url, generation = await generate_image(
        provider=provider,
        media_store=media_store,
        prompt=prompt,
        candidate_id=candidate_id,
        refs=refs,
        metadata=metadata,
    )
    state_variant = state.model_copy(deep=True)
    state_variant.metadata["key_vision_prompt"] = prompt_output.model_dump(mode="json")
    item = make_item(
        base_item_value,
        candidate_id=candidate_id,
        prompt=prompt,
        image_path=generated_path,
        image_url=generated_url,
        label=group["label"],
    )
    audit = await audit_image(
        audit_node=audit_node,
        audit_provider=audit_provider,
        state=state_variant,
        item=item,
        image_path=generated_path,
        label=candidate_id,
    )
    return {
        "candidate_id": candidate_id,
        "group_id": group["id"],
        "group_label": group["label"],
        "provider": group["provider_label"],
        "provider_kind": group["provider_kind"],
        "mode": group["mode"],
        "repeat": repeat,
        "prompt_kind": group["prompt_kind"],
        "uses_reference": group["uses_reference"] == "true",
        "image_path": str(generated_path),
        "image_url": generated_url,
        "prompt": prompt,
        "generation": generation,
        "audit": audit,
        "weighted_score": audit.get("weighted_score"),
        "noise_score": noise_score(audit),
        "status": "success",
    }


async def recover_existing_candidate(
    *,
    group: dict[str, str],
    repeat: int,
    original_prompt: str,
    noise_prompt: str,
    base_item_value: StaticAssetGenerationItem,
    state: ProjectState,
    prompt_output: KeyVisionPromptOutput,
    audit_provider: Any,
    audit_node: KeyVisionImageAuditNode,
) -> dict[str, Any] | None:
    """Audit an already generated image without issuing another image call."""
    candidate_id = f"{group['id']}_r{repeat:02d}"
    image_path = LAB / "images" / f"{candidate_id}.png"
    response_path = LAB / "responses" / "image" / f"{candidate_id}.json"
    if not image_path.is_file() or not response_path.is_file():
        return None
    generation = read_json(response_path)
    response = generation.get("response") if isinstance(generation, dict) else {}
    image_urls = response.get("image_urls") if isinstance(response, dict) else []
    image_url = image_urls[0] if isinstance(image_urls, list) and image_urls else None
    prompt = original_prompt if group["prompt_kind"] == "original_prompt" else noise_prompt
    state_variant = state.model_copy(deep=True)
    state_variant.metadata["key_vision_prompt"] = prompt_output.model_dump(mode="json")
    item = make_item(
        base_item_value,
        candidate_id=candidate_id,
        prompt=prompt,
        image_path=image_path,
        image_url=image_url,
        label=group["label"],
    )
    audit = await audit_image(
        audit_node=audit_node,
        audit_provider=audit_provider,
        state=state_variant,
        item=item,
        image_path=image_path,
        label=candidate_id,
    )
    return {
        "candidate_id": candidate_id,
        "group_id": group["id"],
        "group_label": group["label"],
        "provider": group["provider_label"],
        "provider_kind": group["provider_kind"],
        "mode": group["mode"],
        "repeat": repeat,
        "prompt_kind": group["prompt_kind"],
        "uses_reference": group["uses_reference"] == "true",
        "image_path": str(image_path),
        "image_url": image_url,
        "prompt": prompt,
        "generation": generation,
        "audit": audit,
        "weighted_score": audit.get("weighted_score"),
        "noise_score": noise_score(audit),
        "status": "success",
        "recovered_without_image_regeneration": True,
    }


def summary_stats(records: list[dict[str, Any]], baseline_score: float | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in GROUPS:
        values = [
            float(item["weighted_score"])
            for item in records
            if item.get("group_id") == group["id"] and item.get("status") == "success" and item.get("weighted_score") is not None
        ]
        noise_values = [
            float(item["noise_score"])
            for item in records
            if item.get("group_id") == group["id"] and item.get("status") == "success" and item.get("noise_score") is not None
        ]
        approved = [
            item for item in records
            if item.get("group_id") == group["id"] and item.get("status") == "success" and item.get("audit", {}).get("approved") is True
        ]
        mean = statistics.mean(values) if values else None
        std = statistics.stdev(values) if len(values) > 1 else 0.0 if values else None
        noise_mean = statistics.mean(noise_values) if noise_values else None
        rows.append(
            {
                "group_id": group["id"],
                "label": group["label"],
                "provider": group["provider_label"],
                "mode": group["mode"],
                "n": len(values),
                "mean": mean,
                "std": std,
                "delta_vs_baseline": mean - baseline_score if mean is not None and baseline_score is not None else None,
                "noise_mean": noise_mean,
                "approved_n": len(approved),
                "failed_n": sum(
                    1 for item in records if item.get("group_id") == group["id"] and item.get("status") != "success"
                ),
            }
        )
    return rows


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return escape(str(value))


def render_report(
    *,
    baseline: dict[str, Any],
    records: list[dict[str, Any]],
    stats: list[dict[str, Any]],
    started_at: str,
    completed_at: str,
) -> None:
    baseline_score = baseline.get("weighted_score")
    successful = [item for item in records if item.get("status") == "success"]
    best = max(successful, key=lambda item: float(item.get("weighted_score") or -1), default=None)
    best_noise = max(successful, key=lambda item: float(item.get("noise_score") or -1), default=None)
    stat_by_id = {item["group_id"]: item for item in stats}

    def image_src(record: dict[str, Any]) -> str:
        path = record.get("image_path")
        return relative_to_report(path) if path else ""

    def audit_badge(record: dict[str, Any]) -> str:
        if record.get("status") != "success":
            return '<span class="badge bad">调用失败</span>'
        audit = record.get("audit") or {}
        if audit.get("approved"):
            return '<span class="badge good">通过</span>'
        return '<span class="badge warn">未通过</span>'

    group_cards: list[str] = []
    for group in GROUPS:
        group_records = [item for item in records if item.get("group_id") == group["id"]]
        stat = stat_by_id[group["id"]]
        cards: list[str] = []
        for item in group_records:
            image = (
                f'<img src="{escape(image_src(item))}" alt="{escape(item["candidate_id"])}" loading="lazy">'
                if item.get("image_path")
                else '<div class="image-missing">无图片</div>'
            )
            audit = item.get("audit") or {}
            issues = "；".join(audit.get("issues") or []) or "无"
            cards.append(
                f"""<article class="candidate">
  <div class="candidate-image">{image}</div>
  <div class="candidate-body">
    <div class="candidate-top"><code>{escape(item['candidate_id'])}</code>{audit_badge(item)}</div>
    <div class="score-line"><strong>{fmt(item.get('weighted_score'))}</strong><span>/ 10</span><em>噪点维度 {fmt(item.get('noise_score'))}</em></div>
    <p class="muted">{escape(item.get('mode', ''))} · {('有基线参考图' if item.get('uses_reference') else '无参考图')}</p>
    <p><b>审计意见：</b>{escape(issues)}</p>
    <details><summary>查看提示词与调用记录</summary><pre>{escape(item.get('prompt', ''))}</pre><pre>{escape(json.dumps(item.get('generation', {}), ensure_ascii=False, indent=2))}</pre></details>
  </div>
</article>"""
            )
        group_cards.append(
            f"""<section class="group-section" id="{escape(group['id'])}">
  <div class="group-heading"><div><span class="eyebrow">{escape(group['provider_label'])}</span><h3>{escape(group['label'])}</h3><p>{escape(group['mode'])}</p></div><div class="group-score"><strong>{fmt(stat['mean'])}</strong><span>± {fmt(stat['std'])}</span><small>噪点均值 {fmt(stat['noise_mean'])}</small></div></div>
  <div class="gallery">{''.join(cards)}</div>
</section>"""
        )

    raw_rows: list[str] = []
    all_rows = [baseline, *records]
    for item in all_rows:
        is_baseline = item.get("group_id") == "baseline"
        audit = item.get("audit") or {}
        raw_rows.append(
            f"<tr><td><code>{escape(item.get('candidate_id', ''))}</code></td><td>{escape(item.get('group_label', '基线'))}</td><td>{escape(item.get('provider', '基线'))}</td><td>{escape(item.get('mode', 'baseline'))}</td><td>{'是' if item.get('uses_reference') else '否'}</td><td>{fmt(item.get('weighted_score'))}</td><td>{fmt(item.get('noise_score'))}</td><td>{fmt((item.get('weighted_score') - baseline_score) if not is_baseline and item.get('weighted_score') is not None and baseline_score is not None else 0 if is_baseline else None)}</td><td>{'成功' if item.get('status') == 'success' else '失败'} / {'通过' if audit.get('approved') else '未通过' if audit else '—'}</td></tr>"
        )

    stat_rows = []
    for item in stats:
        stat_rows.append(
            f"<tr><td>{escape(item['label'])}</td><td>{escape(item['provider'])}</td><td>{item['n']}</td><td>{fmt(item['mean'])} ± {fmt(item['std'])}</td><td>{fmt(item['noise_mean'])}</td><td>{fmt(item['delta_vs_baseline'], 2)}</td><td>{item['approved_n']}/{item['n']}</td><td>{item['failed_n']}</td></tr>"
        )

    findings: list[str] = []
    if baseline_score is not None:
        findings.append(
            f"基线在新增渲染噪点 rubric 下为 <b>{fmt(baseline_score)}</b> 分，噪点维度为 <b>{fmt(baseline.get('noise_score'))}</b>。"
        )
    if best is not None:
        findings.append(
            f"整体最高候选是 <code>{escape(best['candidate_id'])}</code>，总分 <b>{fmt(best.get('weighted_score'))}</b>，噪点维度 <b>{fmt(best.get('noise_score'))}</b>。"
        )
    if best_noise is not None:
        findings.append(
            f"噪点维度最高候选是 <code>{escape(best_noise['candidate_id'])}</code>，得分 <b>{fmt(best_noise.get('noise_score'))}</b>；这比总分最高更直接回答“能否修复噪点”。"
        )
    for left_id, right_id, label in (
        ("gpt_original_redraw", "gpt_noise_repair", "GPT 重画 vs GPT 修复"),
        ("banana_original_prompt", "banana_noise_repair", "Banana 原提示词 vs Banana 修复"),
    ):
        left = stat_by_id[left_id]
        right = stat_by_id[right_id]
        if left["mean"] is not None and right["mean"] is not None:
            findings.append(
                f"{label}：修复组平均总分变化 <b>{fmt(right['mean'] - left['mean'], 2)}</b>，噪点均值变化 <b>{fmt((right['noise_mean'] or 0) - (left['noise_mean'] or 0), 2)}</b>。"
            )
    if not findings:
        findings.append("本轮尚无可用审计分数；请查看失败调用记录并使用 resume 继续实验。")

    next_steps = [
        "对本轮最高噪点分候选做一次 100%/200% 人工复核，确认模型评分没有把真实石材纹理、雾气或合法电影颗粒误判为伪影。",
        "若修复组只提高噪点分而明显损伤构图或建筑几何，下一轮将修复 prompt 收窄为局部材质重渲染，并增加一张局部裁剪参考图。",
        "若 Banana 与 GPT 修复均无稳定提升，测试先用低频去噪/材质重建的专用编辑模型，再把结果交给同一 rubric 复审。",
    ]
    baseline_image = relative_to_report(BASELINE_IMAGE_PATH)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>主视觉渲染噪点修复实验</title>
<style>
:root{{--ink:#17212b;--muted:#6b7785;--line:#dfe5ec;--soft:#f5f7fa;--blue:#2563eb;--teal:#0f766e;--amber:#b45309;--red:#b91c1c;--shadow:0 18px 45px rgba(30,42,60,.10)}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef2f6;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.55}} .wrap{{max-width:1500px;margin:0 auto;padding:36px 28px 70px}} .hero{{background:linear-gradient(135deg,#102233 0%,#1e3a5f 60%,#315e78 100%);color:#fff;border-radius:28px;padding:42px 46px;box-shadow:var(--shadow);position:relative;overflow:hidden}} .hero:after{{content:"";position:absolute;width:420px;height:420px;border-radius:50%;right:-150px;top:-200px;background:rgba(255,255,255,.08)}} .eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#6b8ba4;font-weight:800}} .hero .eyebrow{{color:#b9d4e6}} h1{{font-size:clamp(30px,4vw,58px);line-height:1.08;margin:12px 0 14px;max-width:900px;letter-spacing:-.04em}} .hero p{{max-width:900px;color:#d7e7f2;font-size:16px;margin:0}} .meta{{display:flex;gap:22px;flex-wrap:wrap;margin-top:28px;color:#c4d9e8;font-size:13px}} .section{{margin-top:28px;background:#fff;border-radius:22px;padding:28px;box-shadow:var(--shadow)}} h2{{font-size:25px;margin:0 0 16px;letter-spacing:-.02em}} h3{{font-size:22px;margin:4px 0}} .muted{{color:var(--muted)}} .baseline-grid{{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.8fr);gap:24px;align-items:start}} .baseline-grid img{{display:block;width:100%;border-radius:16px;background:#dce5ed}} .baseline-copy{{padding:8px 4px}} .metric{{font-size:38px;font-weight:800;letter-spacing:-.05em;color:var(--blue)}} .metric small{{font-size:14px;letter-spacing:0;color:var(--muted);font-weight:600}} .callout{{padding:16px 18px;border:1px solid #dbe5f0;border-radius:15px;background:#f8fbff;margin-top:18px}} .method-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .method{{border:1px solid var(--line);border-radius:16px;padding:17px;background:linear-gradient(180deg,#fff,#f8fafc)}} .method strong{{display:block;font-size:16px;margin:6px 0}} .method span{{font-size:13px;color:var(--muted)}} .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:14px}} table{{border-collapse:collapse;width:100%;min-width:860px;font-size:13px}} th,td{{padding:12px 13px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#f4f7fa;color:#536171;font-weight:800;white-space:nowrap}} tr:last-child td{{border-bottom:0}} code{{font-family:"SFMono-Regular",Consolas,monospace;font-size:.9em;color:#334155}} .badge{{display:inline-flex;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:800}} .badge.good{{color:#065f46;background:#d1fae5}} .badge.warn{{color:#92400e;background:#fef3c7}} .badge.bad{{color:#991b1b;background:#fee2e2}} .findings{{counter-reset:f}} .finding{{position:relative;padding:14px 16px 14px 48px;border-bottom:1px solid var(--line)}} .finding:last-child{{border-bottom:0}} .finding:before{{counter-increment:f;content:counter(f);position:absolute;left:0;top:13px;width:28px;height:28px;border-radius:50%;background:#dbeafe;color:#1d4ed8;font-weight:800;display:grid;place-items:center}} .group-section{{margin-top:32px;padding-top:26px;border-top:1px solid var(--line)}} .group-section:first-child{{border-top:0;margin-top:0;padding-top:0}} .group-heading{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:17px}} .group-heading p{{margin:2px 0;color:var(--muted);font-size:13px}} .group-score{{text-align:right;white-space:nowrap}} .group-score strong{{font-size:31px;letter-spacing:-.05em;color:var(--blue)}} .group-score span{{color:var(--muted);font-size:13px}} .group-score small{{display:block;color:var(--teal);font-weight:700}} .gallery{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}} .candidate{{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}} .candidate-image{{aspect-ratio:16/9;background:#dce5ed;display:grid;place-items:center}} .candidate-image img{{display:block;width:100%;height:100%;object-fit:cover}} .image-missing{{color:var(--muted)}} .candidate-body{{padding:15px}} .candidate-top{{display:flex;justify-content:space-between;align-items:center;gap:10px}} .score-line{{display:flex;align-items:baseline;gap:5px;margin:10px 0 3px}} .score-line strong{{font-size:28px;letter-spacing:-.05em}} .score-line span{{color:var(--muted)}} .score-line em{{margin-left:auto;color:var(--teal);font-size:12px;font-style:normal;font-weight:700}} .candidate-body p{{font-size:13px;margin:7px 0}} details{{margin-top:12px}} summary{{cursor:pointer;color:#1d4ed8;font-size:12px;font-weight:700}} pre{{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;border-radius:10px;padding:12px;font-size:11px;max-height:280px;overflow:auto}} .footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:30px}} @media(max-width:1050px){{.method-grid{{grid-template-columns:repeat(2,1fr)}}.baseline-grid{{grid-template-columns:1fr}}.gallery{{grid-template-columns:repeat(2,1fr)}}}} @media(max-width:650px){{.wrap{{padding:18px 12px 50px}}.hero{{padding:28px 24px;border-radius:20px}}.section{{padding:20px 16px;border-radius:18px}}.method-grid,.gallery{{grid-template-columns:1fr}}.group-heading{{display:block}}.group-score{{text-align:left;margin-top:10px}}}}
</style></head>
<body><main class="wrap">
<header class="hero"><div class="eyebrow">AutoDrama · Key Vision Lab</div><h1>主视觉渲染噪点修复实验</h1><p>比较 GPT 原提示词重画、GPT 噪点修复、Gemini Banana 原提示词编辑和 Gemini Banana 噪点修复，判断“GPT 风格噪点”能否通过参考图编辑稳定消除，同时不破坏世界观主视觉的构图与空间证据。</p><div class="meta"><span>项目：扫地八十年，从宗门杂役飞升成仙</span><span>画布：3840 × 2160 · 16:9</span><span>实验图像：{len(successful)} / {len(records)} 成功</span><span>开始：{escape(started_at)}</span></div></header>
<section class="section"><h2>实验设计</h2><div class="method-grid">{''.join(f'<div class="method"><span>{escape(group["provider_label"])}</span><strong>{escape(group["label"])}</strong><span>{escape(group["mode"])} · {"使用基线参考图" if group["uses_reference"] == "true" else "不使用参考图"}</span></div>' for group in GROUPS)}</div><div class="callout"><b>修复目标：</b>只处理高频颗粒、蜡状/塑料微纹理、重复碎纹、过度锐化、随机亮暗斑、局部脏污和不一致细节频率；不允许改动世界内容、建筑、镜头、色彩、人物数量或空间布局。</div></section>
<section class="section"><h2>基线</h2><div class="baseline-grid"><div><img src="{escape(baseline_image)}" alt="基线主视觉"></div><div class="baseline-copy"><div class="eyebrow">baseline_original</div><h3>当前主视觉原图</h3><div class="metric">{fmt(baseline_score)} <small>/ 10</small></div><p class="muted">新增渲染噪点维度：{fmt(baseline.get('noise_score'))} / 10 · 审计状态：{'通过' if baseline.get('audit', {}).get('approved') else '未通过'}</p><p>{escape('；'.join(baseline.get('audit', {}).get('issues') or []) or '审计没有列出额外问题。')}</p><details><summary>查看基线提示词</summary><pre>{escape(baseline.get('prompt', ''))}</pre></details></div></div></section>
<section class="section"><h2>原始数据表</h2><div class="table-wrap"><table><thead><tr><th>候选</th><th>组别</th><th>Provider</th><th>方式</th><th>参考图</th><th>总分</th><th>噪点分</th><th>相对基线</th><th>状态</th></tr></thead><tbody>{''.join(raw_rows)}</tbody></table></div></section>
<section class="section"><h2>组间统计</h2><p class="muted">总分由生产 rubric 的逐维度纯加权分重新计算；噪点分单独读取 <code>rendering_artifact_noise</code>。均值 ± 标准差只统计审计成功的候选。</p><div class="table-wrap"><table><thead><tr><th>方法</th><th>Provider</th><th>n</th><th>总分均值 ± std</th><th>噪点均值</th><th>相对基线</th><th>通过数</th><th>失败</th></tr></thead><tbody>{''.join(stat_rows)}</tbody></table></div></section>
<section class="section"><h2>关键发现</h2><div class="findings">{''.join(f'<div class="finding">{finding}</div>' for finding in findings)}</div></section>
<section class="section"><h2>候选图对比</h2>{''.join(group_cards)}</section>
<section class="section"><h2>建议的下一步实验</h2><ol>{''.join(f'<li>{escape(item)}</li>' for item in next_steps)}</ol><p class="muted">完整 JSON、逐候选审计、请求协议和脱敏 API 响应保存在 <code>{escape(str(LAB))}</code>。</p></section>
<div class="footer">完成时间：{escape(completed_at)} · 报告由本地实验数据生成，无外部依赖。</div>
</main></body></html>"""
    # The baseline prompt is injected here after the template is assembled so
    # the report generator remains usable with resumed result files.
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8", newline="\n")


async def run(args: argparse.Namespace) -> int:
    settings = load_settings(ROOT / "saodi.yaml")
    repo = ProjectRepository(settings)
    state = repo.load_state(PROJECT_DIR)
    base = base_item(repo)
    original_prompt = ORIGINAL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    prompt_output = KeyVisionPromptOutput.model_validate(state.metadata.get("key_vision_prompt"))
    if not BASELINE_IMAGE_PATH.is_file():
        raise FileNotFoundError(BASELINE_IMAGE_PATH)
    if not str(original_prompt).strip():
        raise ValueError("baseline prompt is empty")

    LAB.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(LAB)
    workflow = PregenWorkflow(repo=repo, router=router)
    audit_node = KeyVisionImageAuditNode(workflow=workflow)
    image_provider = router.image("key_vision", node_name="key_vision_image_generation")
    gpt_edit_provider = router.image("key_vision", node_name="key_vision_edit")
    banana_provider = AiboxBananaImageProvider(
        settings.providers["aibox"],
        settings.runtime,
        reference_uploader_settings=settings.providers.get("toapi"),
    )
    audit_provider = router.text("key_vision", node_name="key_vision_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    baseline_url = base.asset_url
    base_ref = AssetRef(
        id="key_vision_original",
        type="image",
        path=str(BASELINE_IMAGE_PATH),
        url=baseline_url,
        metadata={"reference_role": "exact_baseline_image"},
    )
    baseline_item = make_item(
        base,
        candidate_id="baseline_original",
        prompt=original_prompt,
        image_path=BASELINE_IMAGE_PATH,
        image_url=None,
        label="当前主视觉原图",
    )

    baseline_path = LAB / "baseline.json"
    if args.resume and baseline_path.is_file():
        baseline = read_json(baseline_path)
    else:
        baseline_state = state.model_copy(deep=True)
        baseline_state.metadata["key_vision_prompt"] = prompt_output.model_dump(mode="json")
        try:
            baseline_audit = await audit_image(
                audit_node=audit_node,
                audit_provider=audit_provider,
                state=baseline_state,
                item=baseline_item,
                image_path=BASELINE_IMAGE_PATH,
                label="baseline_original",
            )
            baseline = {
                "candidate_id": "baseline_original",
                "group_id": "baseline",
                "group_label": "当前主视觉原图",
                "provider": "existing project asset",
                "mode": "baseline",
                "uses_reference": False,
                "image_path": str(BASELINE_IMAGE_PATH),
                "prompt": original_prompt,
                "audit": baseline_audit,
                "weighted_score": baseline_audit.get("weighted_score"),
                "noise_score": noise_score(baseline_audit),
                "status": "success",
            }
        except Exception as exc:
            baseline = {
                "candidate_id": "baseline_original",
                "group_id": "baseline",
                "group_label": "当前主视觉原图",
                "provider": "existing project asset",
                "mode": "baseline",
                "uses_reference": False,
                "image_path": str(BASELINE_IMAGE_PATH),
                "prompt": original_prompt,
                "status": "audit_failed",
                "error": safe_error(exc),
            }
        write_json(baseline_path, redact_sensitive(baseline))

    records: list[dict[str, Any]] = []
    results_path = LAB / "results.json"
    if args.resume and results_path.is_file():
        existing = read_json(results_path)
        if isinstance(existing, list):
            records.extend(existing)
    completed_ids = {
        str(item.get("candidate_id"))
        for item in records
        if item.get("status") == "success"
    }
    for group in GROUPS:
        for repeat in range(1, 4):
            candidate_id = f"{group['id']}_r{repeat:02d}"
            if candidate_id in completed_ids and args.resume:
                continue
            try:
                result = None
                if args.resume:
                    result = await recover_existing_candidate(
                        group=group,
                        repeat=repeat,
                        original_prompt=original_prompt,
                        noise_prompt=NOISE_REPAIR_PROMPT,
                        base_item_value=base,
                        state=state,
                        prompt_output=prompt_output,
                        audit_provider=audit_provider,
                        audit_node=audit_node,
                    )
                if result is None:
                    provider = image_provider if group["provider_kind"] == "gpt" else banana_provider
                    # GPT original redraw uses the generation node; GPT repair uses
                    # the edit node so the request is auditable as an edit.
                    if group["provider_kind"] == "gpt" and group["uses_reference"] == "true":
                        provider = gpt_edit_provider
                    result = await run_one(
                        group=group,
                        repeat=repeat,
                        original_prompt=original_prompt,
                        noise_prompt=NOISE_REPAIR_PROMPT,
                        base_ref=base_ref,
                        base_item_value=base,
                        state=state,
                        prompt_output=prompt_output,
                        image_provider=provider,
                        banana_provider=banana_provider if group["provider_kind"] == "banana" else provider,
                        audit_provider=audit_provider,
                        audit_node=audit_node,
                        media_store=media_store,
                    )
            except Exception as exc:
                result = {
                    "candidate_id": candidate_id,
                    "group_id": group["id"],
                    "group_label": group["label"],
                    "provider": group["provider_label"],
                    "provider_kind": group["provider_kind"],
                    "mode": group["mode"],
                    "repeat": repeat,
                    "prompt_kind": group["prompt_kind"],
                    "uses_reference": group["uses_reference"] == "true",
                    "prompt": original_prompt if group["prompt_kind"] == "original_prompt" else NOISE_REPAIR_PROMPT,
                    "status": "failed",
                    "error": safe_error(exc),
                }
                if args.stop_on_error:
                    records.append(result)
                    write_json(results_path, redact_sensitive(records))
                    raise
                print(f"[noise-experiment] {candidate_id} failed: {result['error']['type']}", flush=True)
            records = [item for item in records if item.get("candidate_id") != candidate_id]
            records.append(result)
            write_json(results_path, redact_sensitive(records))
            print(
                f"[noise-experiment] {candidate_id} status={result.get('status')} "
                f"score={result.get('weighted_score', '—')} noise={result.get('noise_score', '—')}",
                flush=True,
            )

    baseline_score = baseline.get("weighted_score")
    stats = summary_stats(records, baseline_score)
    completed_at = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "project": str(PROJECT_DIR),
        "baseline_image": str(BASELINE_IMAGE_PATH),
        "baseline_prompt": str(ORIGINAL_PROMPT_PATH),
        "lab": str(LAB),
        "report": str(REPORT_PATH),
        "started_at": started_at,
        "completed_at": completed_at,
        "image_count_requested": 12,
        "image_count_completed": sum(1 for item in records if item.get("status") == "success"),
        "groups": list(GROUPS),
        "noise_repair_prompt": NOISE_REPAIR_PROMPT,
        "baseline": baseline,
        "stats": stats,
    }
    write_json(LAB / "manifest.json", redact_sensitive(manifest))
    render_report(
        baseline=baseline,
        records=records,
        stats=stats,
        started_at=started_at,
        completed_at=completed_at,
    )
    print(json.dumps({"report": str(REPORT_PATH), "lab": str(LAB), "completed": len(records)}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the four-group key vision rendering-noise experiment.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed baseline/candidate JSON files.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop after the first failed candidate.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
