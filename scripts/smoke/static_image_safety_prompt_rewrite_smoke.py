from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import ProjectState, SafeImagePromptRewriteOutput, ScriptBundle  # noqa: E402
from autodrama.providers.base import ImageGenerationResult  # noqa: E402
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase  # noqa: E402


class UnsafeImageProvider:
    name = "unsafe_static_image"
    model = "unsafe-static-model"
    supports_reference_images = True

    def __init__(self, failures_before_success: int | None) -> None:
        self.failures_before_success = failures_before_success
        self.calls: list[dict[str, Any]] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        self.calls.append({"prompt": prompt, "refs": refs or [], "size": size, "metadata": metadata or {}})
        should_fail = self.failures_before_success is None or len(self.calls) <= self.failures_before_success
        if should_fail:
            raise ProviderBadResponseError(
                "ToAPI image task tsk_fake failed: {'code': 'generation_failed', "
                "'message': 'image job failed: {\"error_code\":\"image_unsafe\","
                "\"message\":\"The generated images appear to be unsafe.\"}'}"
            )
        asset_id = str((metadata or {}).get("asset_id") or "asset")
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(f"safe image:{asset_id}:{prompt}".encode("utf-8")).decode("ascii")],
            request_id=f"safe-image-{asset_id}",
            raw_response={"asset_id": asset_id, "prompt": prompt},
        )


class SafetyRewriteTextProvider:
    name = "static_safety_rewrite_text"
    model = "static-safety-rewrite-model"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, prompt: str, schema, *, temperature=0.7, metadata=None):
        self.calls.append({"prompt": prompt, "schema": schema, "temperature": temperature, "metadata": metadata or {}})
        if schema is not SafeImagePromptRewriteOutput:
            raise AssertionError(f"Unexpected schema: {schema}")
        return SafeImagePromptRewriteOutput(
            prompt=f"安全改写后的静态图 prompt 第 {len(self.calls)} 轮：用旧污渍和低风险 CG 质感替代血迹。",
            notes="removed unsafe static image terms",
        )


class Router:
    def __init__(self, image_provider: UnsafeImageProvider) -> None:
        self.image_provider = image_provider
        self.text_provider = SafetyRewriteTextProvider()
        self.text_purposes: list[str] = []

    def image(self, _purpose: str):
        return self.image_provider

    def text(self, purpose: str):
        self.text_purposes.append(purpose)
        return self.text_provider


class Harness(StaticAssetNodeBase):
    def __init__(self, router: Router) -> None:
        self.router = router
        self.logger = logging.getLogger("static_image_safety_prompt_rewrite_smoke")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_state(project_id: str) -> ProjectState:
    return ProjectState(
        project_id=project_id,
        title=project_id,
        raw_script="",
        script=ScriptBundle(raw_script=""),
    )


async def run_success_case() -> None:
    router = Router(UnsafeImageProvider(failures_before_success=1))
    harness = Harness(router)
    state = make_state("static-image-safety-prompt-rewrite-success")

    result, final_prompt, rewrites = await harness._generate_image_with_safety_prompt_rewrites(
        provider=router.image_provider,
        state=state,
        node_name="prop_generation",
        asset_id="prop_fake",
        prompt="生成带有血迹和开放性伤口细节的道具图。",
        refs=None,
        metadata={"node_name": "prop_generation", "project_id": state.project_id, "asset_id": "prop_fake"},
        context={"asset_type": "prop", "prop_name": "fake"},
    )

    require(len(router.image_provider.calls) == 2, f"Expected original + rewritten image calls: {router.image_provider.calls}")
    require(len(router.text_provider.calls) == 1, f"Expected one prompt rewrite call: {router.text_provider.calls}")
    require(router.text_purposes == ["prop"], f"Expected prop text provider first: {router.text_purposes}")
    require(len(rewrites) == 1, f"Expected one rewrite record: {rewrites}")
    require("安全改写后的静态图 prompt" in final_prompt, final_prompt)
    require(router.image_provider.calls[0]["metadata"]["safety_prompt_rewrite_attempt"] == 0, router.image_provider.calls)
    require(router.image_provider.calls[1]["metadata"]["safety_prompt_rewrite_attempt"] == 1, router.image_provider.calls)
    require(result.raw_response.get("safety_prompt_rewrite", {}).get("final_prompt") == final_prompt, result.raw_response)
    require(state.budget.used_text_calls == 1, f"Rewrite text call should be counted: {state.budget.used_text_calls}")


async def run_max_rewrite_case() -> None:
    router = Router(UnsafeImageProvider(failures_before_success=None))
    harness = Harness(router)
    state = make_state("static-image-safety-prompt-rewrite-max")

    try:
        await harness._generate_image_with_safety_prompt_rewrites(
            provider=router.image_provider,
            state=state,
            node_name="layout_image_generation",
            asset_id="layout_fake",
            prompt="生成带有血迹和开放性伤口细节的场景图。",
            refs=None,
            metadata={"node_name": "layout_image_generation", "project_id": state.project_id, "asset_id": "layout_fake"},
            context={"asset_type": "layout", "layout_name": "fake"},
        )
    except ProviderBadResponseError as exc:
        require("after 3 prompt rewrite attempt" in str(exc), str(exc))
    else:
        raise AssertionError("Expected safety failure after max rewrite attempts")

    require(len(router.image_provider.calls) == 4, f"Expected original + 3 rewritten image calls: {router.image_provider.calls}")
    require(len(router.text_provider.calls) == 3, f"Expected exactly 3 prompt rewrite calls: {router.text_provider.calls}")
    require(state.budget.used_text_calls == 3, f"Expected 3 counted text calls: {state.budget.used_text_calls}")


async def main_async() -> int:
    await run_success_case()
    await run_max_rewrite_case()
    print("static_image_safety_prompt_rewrite_smoke=ok")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
