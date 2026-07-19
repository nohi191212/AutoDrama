from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import Settings
from autodrama.core.errors import ProviderBadResponseError
from autodrama.core.schemas import ClipPromptItem, ProjectState, ScriptBundle, StoryboardPromptClip
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardPromptAuditNode


class _Logger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        return

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


def _clip(index: int, posture: str) -> StoryboardPromptClip:
    return StoryboardPromptClip(
        clip_id=f"episode_001_clip_{index:03d}",
        duration_seconds=12,
        role_ids=["role_jiang"],
        layout_ids=["layout_hall"],
        camera_shots=[
            {
                "camera_shot_id": "Camera Shot 1",
                "time_range": "0-6秒",
                "description": f"固定机位中景，江未晞{posture}殿内。",
            },
            {
                "camera_shot_id": "Camera Shot 2",
                "time_range": "6-12秒",
                "description": f"固定机位近景，江未晞仍{posture}原处。",
            },
        ],
        panel_plan={
            f"P{panel:02d}": (
                f"P{panel:02d}（Camera Shot {1 if panel <= 6 else 2}）："
                f"江未晞{posture}原处。"
            )
            for panel in range(1, 13)
        },
        video_prompt=f"江未晞{posture}殿内。",
    )


def _clip_prompt(index: int) -> ClipPromptItem:
    return ClipPromptItem(
        episode_key="episode_001",
        clip_id=f"episode_001_clip_{index:03d}",
        clip_index=index,
        source_clip_key=str(index),
        clip_text=f"第{index}段。",
        role_ids=["role_jiang"],
        layout_ids=["layout_hall"],
        target_duration_seconds=12,
        clip_prompt="固定镜头。",
    )


class _FixingProvider:
    name = "fake"
    model = "audit"

    def __init__(self, prompts: dict[str, str]) -> None:
        self.prompts = prompts
        self.calls: list[dict[str, object]] = []

    async def generate_json(
        self,
        prompt: str,
        schema: type[object],
        **kwargs: object,
    ) -> object:
        metadata = dict(kwargs.get("metadata") or {})
        expected_ids = list(metadata["expected_clip_ids"])
        self.calls.append({"prompt": prompt, "expected_ids": expected_ids})
        clips = []
        for clip_id in expected_ids:
            changed = clip_id.endswith("_002")
            revised = self.prompts[clip_id].replace("站在", "坐在") if changed else "ignored"
            clips.append(
                {
                    "clip_id": clip_id,
                    "changed": changed,
                    "issues": ["前一 clip 结尾仍坐着，本 clip 无起身动作却改为站姿"] if changed else [],
                    "clip_storyboard_prompt": revised,
                }
            )
        return schema.model_validate({"clips": clips})  # type: ignore[attr-defined]


class _InvalidFourShotProvider:
    name = "fake"
    model = "invalid-audit"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, _prompt: str, schema: type[object], **kwargs: object) -> object:
        self.calls += 1
        clip_id = list(dict(kwargs.get("metadata") or {})["expected_clip_ids"])[0]
        camera = "\n".join(
            f"Camera Shot {index}（{(index - 1) * 3}-{index * 3}秒）：固定机位。"
            for index in range(1, 5)
        )
        panels = "\n".join(
            f'<P{panel:02d} camera_shot="{(panel - 1) // 3 + 1}">固定构图。</P{panel:02d}>'
            for panel in range(1, 13)
        )
        return schema.model_validate(  # type: ignore[attr-defined]
            {
                "clips": [
                    {
                        "clip_id": clip_id,
                        "changed": True,
                        "issues": ["测试非法修订"],
                        "clip_storyboard_prompt": (
                            f"<CAMERA_SHOTS>\n{camera}\n</CAMERA_SHOTS>\n"
                            f"<PANEL_PLAN>\n{panels}\n</PANEL_PLAN>"
                        ),
                    }
                ]
            }
        )


async def _main() -> None:
    project_dir = ROOT / ".tmp" / "clip_storyboard_prompt_audit" / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    node = object.__new__(StoryboardPromptAuditNode)
    node.workflow = SimpleNamespace(prompts=PromptStore())
    node.repo = ProjectRepository(settings)
    node.layout = ProjectLayout(settings)
    node.logger = _Logger()

    source_clips = {str(index): {"text": f"第{index}段。"} for index in range(1, 10)}
    batches = node._audit_batches(source_clips, source_clips)
    if [len(batch) for batch in batches] != [8, 1]:
        raise AssertionError("audit batches must be 8 plus remainder")
    sparse = {key: source_clips[key] for key in ("1", "3", "4", "9")}
    sparse_batches = node._audit_batches(sparse, source_clips)
    if [list(batch) for batch in sparse_batches] != [["1"], ["3", "4"], ["9"]]:
        raise AssertionError("non-adjacent clip selectors must not be placed in one audit batch")

    clips = {
        f"episode_001_clip_{index:03d}": _clip(index, "站在" if index == 2 else "坐在")
        for index in range(1, 10)
    }
    prompts = {clip_id: node._storyboard_prompt_for_audit(clip) for clip_id, clip in clips.items()}
    clip_prompts = {
        f"episode_001_clip_{index:03d}": _clip_prompt(index)
        for index in range(1, 10)
    }
    provider = _FixingProvider(prompts)
    state = ProjectState(
        project_id="audit-smoke",
        title="Audit",
        raw_script="剧本",
        script=ScriptBundle(raw_script="剧本"),
    )
    corrected, report = await node._audit_batch(
        provider=provider,
        project_dir=project_dir,
        state=state,
        episode_key="episode_001",
        batch_index=1,
        batch_total=2,
        batch_clips={key: source_clips[key] for key in list(source_clips)[:8]},
        all_source_clips=source_clips,
        clip_prompt_items=clip_prompts,
        canonical_clips_by_id=clips,
        current_episode_full="江未晞始终坐在殿内，没有起身动作。",
    )
    if list(provider.calls[0]["expected_ids"]) != [f"episode_001_clip_{i:03d}" for i in range(1, 9)]:
        raise AssertionError("first audit request must contain exactly 8 ordered clip ids")
    if "episode_001_clip_009" not in str(provider.calls[0]["prompt"]):
        raise AssertionError("audit request must include the following boundary clip as read-only context")
    if len(corrected) != 1 or corrected[0].clip_id != "episode_001_clip_002":
        raise AssertionError("only the problematic clip should be replaced")
    if "坐在" not in corrected[0].video_prompt or "站在" in corrected[0].video_prompt:
        raise AssertionError("the seated/standing discontinuity was not corrected")
    changed = [item for item in report if item.changed]
    if len(changed) != 1 or not changed[0].issues:
        raise AssertionError("audit report must identify the changed clip and its issue")

    invalid_provider = _InvalidFourShotProvider()
    try:
        await node._audit_batch(
            provider=invalid_provider,
            project_dir=project_dir,
            state=state,
            episode_key="episode_001",
            batch_index=1,
            batch_total=1,
            batch_clips={"2": source_clips["2"]},
            all_source_clips=source_clips,
            clip_prompt_items=clip_prompts,
            canonical_clips_by_id=clips,
            current_episode_full="剧本。",
        )
    except ProviderBadResponseError:
        pass
    else:
        raise AssertionError("a four-shot audit revision must be rejected")
    if invalid_provider.calls != node.MAX_AUDIT_ATTEMPTS:
        raise AssertionError("invalid audit revisions should be retried before failing")

    marker = ROOT / ".tmp" / "clip_storyboard_prompt_audit_smoke.ok"
    marker.write_text("ok\n", encoding="utf-8")
    print("clip_storyboard_prompt_audit_smoke: ok")


if __name__ == "__main__":
    asyncio.run(_main())
