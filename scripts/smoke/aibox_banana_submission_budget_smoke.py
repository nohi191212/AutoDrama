from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.aibox.image import gpt_image as aibox_gpt_image  # noqa: E402
from autodrama.providers.aibox.image.banana import AiboxBananaImageProvider  # noqa: E402
from autodrama.providers.aibox.image.submission_budget import (  # noqa: E402
    ImageSubmissionBudgetExceededError,
    ImageSubmissionLedger,
)
from autodrama.providers.base import ImageGenerationResult  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


SMOKE_ROOT = ROOT / ".tmp" / "aibox_banana_submission_budget_smoke"


def _reserve_in_process(path: str, limit: int, queue: Any) -> None:
    ledger = ImageSubmissionLedger(Path(path), limit=limit)
    try:
        ticket = ledger.reserve(metadata={"node_name": "process_smoke", "asset_id": os.getpid()})
        ledger.record_status(ticket, status="completed")
        queue.put({"ok": True, "sequence": ticket.sequence})
    except ImageSubmissionBudgetExceededError:
        queue.put({"ok": False})


class _FakeAiboxController:
    def __init__(self, *, transport_failures: int = 0) -> None:
        self.post_calls = 0
        self.transport_failures = transport_failures


class _FakeAsyncClient:
    def __init__(self, controller: _FakeAiboxController) -> None:
        self.controller = controller

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> httpx.Response:
        del headers, json
        self.controller.post_calls += 1
        call_index = self.controller.post_calls
        await asyncio.sleep(0.01)
        request = httpx.Request("POST", url)
        if call_index <= self.controller.transport_failures:
            raise httpx.ConnectError("intentional smoke transport failure", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "state": "success",
                "data": {
                    "task_id": f"smoke-{call_index}",
                    "url": f"https://example.invalid/generated-{call_index}.png",
                },
            },
        )


def _provider(
    ledger_path: Path,
    *,
    limit: int,
    max_attempts: int,
) -> AiboxBananaImageProvider:
    settings = load_settings(ROOT / "saodi.yaml")
    provider = AiboxBananaImageProvider(
        settings.providers["aibox"],
        settings.runtime,
        reference_uploader_settings=settings.providers.get("toapi"),
        submission_ledger_path=ledger_path,
        submission_limit=limit,
    )
    provider.api_key = "smoke-key"
    provider.max_attempts = max_attempts
    provider.settings.options["aibox_retry_initial_delay_seconds"] = 0
    provider.settings.options["aibox_retry_max_delay_seconds"] = 0
    return provider


async def _exercise_real_post_boundary() -> dict[str, Any]:
    original_client = aibox_gpt_image.httpx.AsyncClient
    controller = _FakeAiboxController()
    aibox_gpt_image.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(controller)
    try:
        ledger_path = SMOKE_ROOT / "real_post_boundary.jsonl"
        provider = _provider(ledger_path, limit=2, max_attempts=1)
        outputs = await asyncio.gather(
            *(
                provider.generate_image(
                    f"smoke prompt {index}",
                    metadata={
                        "node_name": "shot_keyframe_image_generation",
                        "asset_id": f"asset-{index}",
                    },
                )
                for index in range(3)
            ),
            return_exceptions=True,
        )
        assert controller.post_calls == 2, controller.post_calls
        assert sum(isinstance(item, ImageGenerationResult) for item in outputs) == 2
        assert sum(isinstance(item, ImageSubmissionBudgetExceededError) for item in outputs) == 1
        snapshot = ImageSubmissionLedger(ledger_path, limit=2).snapshot()
        assert snapshot["used"] == 2
        assert snapshot["remaining"] == 0
        assert set(snapshot["latest_status"].values()) == {"completed"}
    finally:
        aibox_gpt_image.httpx.AsyncClient = original_client

    original_client = aibox_gpt_image.httpx.AsyncClient
    retry_controller = _FakeAiboxController(transport_failures=1)
    aibox_gpt_image.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(retry_controller)
    try:
        retry_ledger_path = SMOKE_ROOT / "retry_attempts.jsonl"
        provider = _provider(retry_ledger_path, limit=2, max_attempts=2)
        result = await provider.generate_image(
            "retry smoke prompt",
            metadata={"node_name": "shot_background_image_generation", "asset_id": "retry-asset"},
        )
        assert isinstance(result, ImageGenerationResult)
        assert retry_controller.post_calls == 2
        snapshot = ImageSubmissionLedger(retry_ledger_path, limit=2).snapshot()
        assert snapshot["used"] == 2
        assert set(snapshot["latest_status"].values()) == {"transport_error", "completed"}
        try:
            await provider.generate_image(
                "must not post",
                metadata={"node_name": "shot_background_image_generation", "asset_id": "over-budget"},
            )
        except ImageSubmissionBudgetExceededError:
            pass
        else:
            raise AssertionError("the third real POST reservation was not rejected")
        assert retry_controller.post_calls == 2
    finally:
        aibox_gpt_image.httpx.AsyncClient = original_client

    return {
        "concurrent_limit": 2,
        "concurrent_real_posts": controller.post_calls,
        "retry_real_posts": retry_controller.post_calls,
    }


def _exercise_process_safe_persistence() -> dict[str, Any]:
    ledger_path = SMOKE_ROOT / "multi_process.jsonl"
    limit = 3
    process_count = 8
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_reserve_in_process, args=(str(ledger_path), limit, queue))
        for _ in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0, process.exitcode
    results = [queue.get(timeout=5) for _ in processes]
    assert sum(bool(item["ok"]) for item in results) == limit
    assert sorted(item["sequence"] for item in results if item["ok"]) == [1, 2, 3]
    reopened = ImageSubmissionLedger(ledger_path, limit=limit)
    snapshot = reopened.snapshot()
    assert snapshot["used"] == limit
    assert snapshot["remaining"] == 0
    try:
        reopened.reserve(metadata={"node_name": "restart_smoke", "asset_id": "over-budget"})
    except ImageSubmissionBudgetExceededError:
        pass
    else:
        raise AssertionError("persisted budget was not enforced after reopening the ledger")
    return {
        "processes": process_count,
        "accepted": limit,
        "rejected": process_count - limit,
    }


def _exercise_router_project_ledger() -> dict[str, Any]:
    settings = load_settings(ROOT / "saodi.yaml")
    router = ProviderRouter(settings)
    project_dir = SMOKE_ROOT / "router_project"
    router.set_prompt_audit_project_dir(project_dir)
    for node_name in (
        "scene_multiview_image_generation",
        "shot_background_image_generation",
        "shot_keyframe_stage_generation",
        "shot_keyframe_image_generation",
    ):
        provider = router.image("shot", node_name=node_name)
        assert provider.model == "gemini-3-pro-image-preview", (node_name, provider.model)
        assert isinstance(provider._provider, AiboxBananaImageProvider), node_name
        assert provider._provider.submission_ledger.path == (
            project_dir / "logs" / "budgets" / "aibox_banana_image_submissions.jsonl"
        )
        assert provider._provider.submission_ledger.limit == 100
    return {
        "nodes": 4,
        "model": "gemini-3-pro-image-preview",
        "limit": 100,
    }


def main() -> int:
    if SMOKE_ROOT.exists():
        shutil.rmtree(SMOKE_ROOT)
    SMOKE_ROOT.mkdir(parents=True)
    saved_env = {
        key: os.environ.pop(key, None)
        for key in (
            AiboxBananaImageProvider.SUBMISSION_LIMIT_ENV,
            AiboxBananaImageProvider.SUBMISSION_LEDGER_ENV,
        )
    }
    try:
        summary = {
            "process_safety": _exercise_process_safe_persistence(),
            "post_boundary": asyncio.run(_exercise_real_post_boundary()),
            "router": _exercise_router_project_ledger(),
        }
    finally:
        for key, value in saved_env.items():
            if value is not None:
                os.environ[key] = value
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
