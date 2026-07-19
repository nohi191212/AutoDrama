from __future__ import annotations

import asyncio
import json
import sys
import re
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import Settings
from autodrama.core.model_catalog import NodeModelSettings
from autodrama.core.schemas import (
    ClipSegment,
    ClipStoryboardPromptModelOutput,
    Layout,
    ProjectState,
    Role,
    RoleAppearance,
    ScriptBundle,
    StoryboardPromptOutput,
)
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardPromptNode


def _clip_payload(episode_key: str, index: int) -> dict[str, object]:
    return {
        "clip_id": f"{episode_key}_clip_{index:03d}",
        "clip_title": f"Clip {index:03d}",
        "clip_duration_hint": "10s",
        "clip_text": f"第 {index} 段。",
        "duration_seconds": 10,
        "role_ids": ["role_江未晞"],
        "layout_ids": ["layout_古老殿宇"],
        "prop_ids": [],
        "camera_shots": [
            {"camera_shot_id": "CS1", "time_range": "0-5秒", "description": "连续动作开始。"},
            {"camera_shot_id": "CS2", "time_range": "5-10秒", "description": "连续动作完成。"},
        ],
        "panel_plan": {f"P{panel:02d}": "Camera Shot 1" for panel in range(1, 13)},
        "video_prompt": (
            "Camera Shot 1（0-5秒）：江未晞在古老殿宇中观察。"
            "Camera Shot 2（5-10秒）：她继续行动并形成情绪推进。"
            "十二宫格面板规划 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12。"
        ),
        "negative_prompt": "禁止字幕、水印、logo。",
    }


class _SmokeLogger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        return

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


class _SmokeWorkflow:
    def __init__(self, clip_selectors: set[str] | None = None) -> None:
        self.prompts = PromptStore()
        self._active_episode_keys = {"episode_002"}
        if clip_selectors:
            self._active_clip_selectors = clip_selectors

    def _hydrate_roles_from_design_files(self, _project_dir: Path, _state: ProjectState) -> None:
        return


class _FakeStoryboardPromptProvider:
    name = "fake"
    model = "storyboard"

    def __init__(self) -> None:
        self.model_binding = SimpleNamespace(params={"clip_storyboard_prompt_concurrency": 2})
        self.settings = SimpleNamespace(options={})
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls: list[dict[str, object]] = []
        self.deferred_single_clip_once = False

    async def generate_json(
        self,
        prompt: str,
        _schema: type[ClipStoryboardPromptModelOutput],
        *,
        temperature: float = 0.7,
        metadata: dict[str, object] | None = None,
        refs: list[object] | None = None,
    ) -> ClipStoryboardPromptModelOutput:
        metadata = metadata or {}
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        batch_index = int(metadata["clip_batch_index"])
        retry_round = int(metadata["clip_retry_round"])
        batch_keys = [int(str(value)) for value in metadata["clip_batch_keys"]]  # type: ignore[index]
        self.calls.append(
            {
                "batch_index": batch_index,
                "batch_keys": batch_keys,
                "ref_count": len(refs or []),
                "temperature": temperature,
                "prompt": prompt,
                "retry_round": retry_round,
            }
        )
        if retry_round == 0 and batch_keys == [13, 14, 15, 16]:
            try:
                await asyncio.sleep(0.03 * (4 - batch_index))
                return ClipStoryboardPromptModelOutput.model_validate(
                    {
                        "clips": [
                            {
                                "clip_id": "clip_1",
                                "clip_storyboard_prompt": "მოახია",
                            }
                        ]
                    }
                )
            finally:
                self.active_calls -= 1
        returned_keys = batch_keys[:1] if batch_index == 1 and len(batch_keys) > 1 else batch_keys
        invalid_single_clip = False
        if batch_keys == [2] and not self.deferred_single_clip_once:
            invalid_single_clip = True
            self.deferred_single_clip_once = True
        try:
            await asyncio.sleep(0.03 * (4 - batch_index))
            return ClipStoryboardPromptModelOutput.model_validate(
                {
                    "clips": [
                        {
                            "clip_id": f"{metadata['episode_key']}_clip_{index:03d}",
                            "clip_storyboard_prompt": (
                                "<CAMERA_SHOTS>\n"
                                f"Camera Shot 1（0-{'4' if invalid_single_clip else '5'}秒）：\n连续动作开始。\n"
                                "Camera Shot 2（5-10秒）：\n连续动作完成。\n"
                                "</CAMERA_SHOTS>\n<PANEL_PLAN>\n"
                                + "\n".join(
                                    f'<P{panel:02d} camera_shot="{1 if panel <= 6 else 2}">第 {panel} 格。</P{panel:02d}>'
                                    for panel in range(1, 13)
                                )
                                + "\n</PANEL_PLAN>"
                            ),
                        }
                        for index in returned_keys
                    ]
                }
            )
        finally:
            self.active_calls -= 1


async def _run_parallel_generation_smoke(
    *,
    settings: Settings,
    layout: ProjectLayout,
    tmp_project: Path,
    state: ProjectState,
    clips: dict[str, ClipSegment],
) -> None:
    provider = _FakeStoryboardPromptProvider()
    node = object.__new__(StoryboardPromptNode)
    node.workflow = _SmokeWorkflow()
    node.repo = ProjectRepository(settings)
    node.layout = layout
    node.router = SimpleNamespace(text=lambda _purpose, node_name=None: provider)
    node.script_service = ScriptService(PromptStore())
    node.asset_service = SimpleNamespace(visual_tone=lambda _state: "电影级冷灰视觉调性")
    node.script_contents = ScriptContentRepository.__new__(ScriptContentRepository)
    node.script_contents.layout = layout
    node.logger = _SmokeLogger()
    node.clip_segments_by_episode = lambda _project_dir: {"episode_002": clips}

    if node.batch_generation_concurrency(provider) != 2:
        raise AssertionError("clip_storyboard_prompt should read configured batch concurrency")

    state.metadata["episode_count"] = 3
    state.budget.used_text_calls = 0
    await node.run(tmp_project, state)

    if provider.max_active_calls < 2:
        raise AssertionError("clip_storyboard_prompt batches should run concurrently")
    if len(provider.calls) != 13:
        raise AssertionError(f"expected 5 initial calls and 8 retry calls, got {len(provider.calls)}")
    single_clip_calls = [call for call in provider.calls if len(call["batch_keys"]) == 1]
    if len(single_clip_calls) != 9:
        raise AssertionError(f"expected 9 single-clip calls including the final initial batch, got {len(single_clip_calls)}")
    if any(call["ref_count"] != 2 for call in provider.calls):
        raise AssertionError("each clip_storyboard_prompt batch should receive roleboard and layout refs")
    expected_reference_prefix = "图1是【江未晞：主角】，图2是【古老殿宇：残破殿宇】。"
    if any(not str(call["prompt"]).startswith(expected_reference_prefix) for call in provider.calls):
        raise AssertionError("clip_storyboard_prompt must prepend the ordered reference image descriptions")
    if state.budget.used_text_calls != 13:
        raise AssertionError(f"expected 13 text calls, got {state.budget.used_text_calls}")
    for call in provider.calls:
        expected_ids = [f"episode_002_clip_{index:03d}" for index in call["batch_keys"]]
        prompt = str(call["prompt"])
        if any(clip_id not in prompt for clip_id in expected_ids):
            raise AssertionError(f"allowed clip_id list is missing from rendered prompt: {expected_ids}")
        if "禁止缩写为 `clip_1`" not in prompt:
            raise AssertionError("rendered prompt does not forbid shorthand clip IDs")

    saved = StoryboardPromptOutput.model_validate_json(
        layout.node_output_path(tmp_project, "clip_storyboard_prompt").read_text(encoding="utf-8")
    )
    episode = next(item for item in saved.storyboards if item.episode_key == "episode_002")
    actual_ids = [clip.clip_id for clip in episode.clips]
    expected_ids = [f"episode_002_clip_{index:03d}" for index in range(1, 18)]
    if actual_ids != expected_ids:
        raise AssertionError("parallel clip_storyboard_prompt output should be merged in original clip order")
    if any(len(clip.camera_shots) != 2 for clip in episode.clips):
        raise AssertionError("camera shots should be parsed from the minimal prompt response")
    if any(set(clip.panel_plan) != {f"P{index:02d}" for index in range(1, 13)} for clip in episode.clips):
        raise AssertionError("P01-P12 should be parsed from the minimal prompt response")
    if any(not clip.storyboard_image_prompt for clip in episode.clips):
        raise AssertionError("clip_storyboard_prompt must persist the complete storyboard image prompt")
    forbidden_image_prompt_text = (
        "当前 clip 视频提示词",
        "clip_storyboard_image_generation",
        "episode_002_clip_",
        "最终图会由系统并排覆盖",
        "生图阶段不要自行绘制数字",
    )
    if any(
        forbidden in (clip.storyboard_image_prompt or "")
        for clip in episode.clips
        for forbidden in forbidden_image_prompt_text
    ):
        raise AssertionError("storyboard image prompts must not include video prompts or workflow metadata")
    if any("【逐格画面】" not in (clip.storyboard_image_prompt or "") for clip in episode.clips):
        raise AssertionError("storyboard image prompts must include the image-only panel plan")
    raw_dir = layout.node_episode_output_path(tmp_project, "clip_storyboard_prompt_raw", "_").parent
    expected_raw_names = {
        *(f"episode_002_batch_{index:03d}_of_005.json" for index in range(1, 6)),
        *(f"episode_002_retry_001_batch_{index:03d}_of_007.json" for index in range(1, 8)),
        "episode_002_retry_002_batch_001_of_001.json",
    }
    if not expected_raw_names.issubset({path.name for path in raw_dir.glob("*.json")}):
        raise AssertionError("each model batch response should have a raw minimal-response snapshot")


async def _run_clip_selection_smoke(
    *,
    settings: Settings,
    layout: ProjectLayout,
    tmp_project: Path,
    state: ProjectState,
    clips: dict[str, ClipSegment],
) -> None:
    existing_clips = []
    for index in range(1, 6):
        payload = _clip_payload("episode_002", index)
        payload["clip_title"] = f"sentinel title {index}"
        payload["video_prompt"] = f"sentinel video prompt {index}"
        payload["storyboard_image_prompt"] = f"sentinel image prompt {index}"
        existing_clips.append(payload)
    ProjectRepository(settings).save_node_output(
        tmp_project,
        "clip_storyboard_prompt",
        StoryboardPromptOutput.model_validate(
            {
                "storyboards": [
                    {
                        "episode_key": "episode_002",
                        "clips": existing_clips,
                    }
                ]
            }
        ),
    )

    provider = _FakeStoryboardPromptProvider()
    node = object.__new__(StoryboardPromptNode)
    node.workflow = _SmokeWorkflow({"2", "4"})
    node.repo = ProjectRepository(settings)
    node.layout = layout
    node.router = SimpleNamespace(text=lambda _purpose, node_name=None: provider)
    node.script_service = ScriptService(PromptStore())
    node.asset_service = SimpleNamespace(visual_tone=lambda _state: "电影级冷灰视觉调性")
    node.script_contents = ScriptContentRepository.__new__(ScriptContentRepository)
    node.script_contents.layout = layout
    node.logger = _SmokeLogger()
    node.clip_segments_by_episode = lambda _project_dir: {"episode_002": dict(list(clips.items())[:5])}

    await node.run(tmp_project, state)
    submitted_batch_keys = [call["batch_keys"] for call in provider.calls]
    if submitted_batch_keys[0] != [2, 4] or any(
        not set(batch_keys).issubset({2, 4}) for batch_keys in submitted_batch_keys
    ):
        raise AssertionError(f"selected prompt generation should only submit clips 2 and 4: {provider.calls}")

    saved = StoryboardPromptOutput.model_validate_json(
        layout.node_output_path(tmp_project, "clip_storyboard_prompt").read_text(encoding="utf-8")
    )
    episode = next(item for item in saved.storyboards if item.episode_key == "episode_002")
    if [clip.clip_id for clip in episode.clips] != [
        f"episode_002_clip_{index:03d}" for index in range(1, 6)
    ]:
        raise AssertionError("selected prompt generation must retain all existing clips in source order")
    by_id = {clip.clip_id: clip for clip in episode.clips}
    for index in (1, 3, 5):
        if by_id[f"episode_002_clip_{index:03d}"].video_prompt != f"sentinel video prompt {index}":
            raise AssertionError(f"unselected clip {index} should remain unchanged")
    for index in (2, 4):
        if by_id[f"episode_002_clip_{index:03d}"].video_prompt == f"sentinel video prompt {index}":
            raise AssertionError(f"selected clip {index} should be regenerated")

    node.workflow = _SmokeWorkflow({"99"})
    provider.calls.clear()
    try:
        await node.run(tmp_project, state)
    except ValueError as exc:
        if "--clips matched no clips" not in str(exc):
            raise AssertionError(f"unexpected unmatched selector error: {exc}") from exc
    else:
        raise AssertionError("an unmatched clip selector should fail before provider submission")
    if provider.calls:
        raise AssertionError("an unmatched clip selector must not call the provider")


def main() -> None:
    tmp_project = ROOT / ".tmp" / "clip_storyboard_prompt_batching" / "project"
    role_image = tmp_project / "assets" / "images" / "roles" / "role_jiang.png"
    layout_image = tmp_project / "assets" / "images" / "layouts" / "layout_hall.png"
    role_image.parent.mkdir(parents=True, exist_ok=True)
    layout_image.parent.mkdir(parents=True, exist_ok=True)
    role_image.write_bytes(b"role image placeholder")
    layout_image.write_bytes(b"layout image placeholder")

    settings = Settings()
    layout = ProjectLayout(settings)
    node = object.__new__(StoryboardPromptNode)
    node.layout = layout
    node.script_contents = ScriptContentRepository.__new__(ScriptContentRepository)
    node.script_contents.layout = layout
    node.logger = None
    ratio_settings = Settings(
        nodes={
            "clip_video_generation": NodeModelSettings(
                model="fake:video",
                params={"aspect_ratio": "9:16"},
            ),
            "clip_storyboard_image_generation": NodeModelSettings(
                model="fake:image",
                params={"size": "3840x2160", "storyboard_panel_aspect_ratio": "4:3"},
            ),
        }
    )
    node.repo = ProjectRepository(ratio_settings)
    if node.final_aspect_ratio() != "9:16":
        raise AssertionError("storyboard square sheet size must not override final video aspect ratio")
    if node.storyboard_sheet_aspect_ratio() != "16:9":
        raise AssertionError("storyboard sheet must follow its configured image size")
    if node.storyboard_panel_aspect_ratio() != "4:3":
        raise AssertionError("each storyboard panel must use the configured 4:3 ratio")
    split_dialogue_prompt = (
        "<CAMERA_SHOTS>\n"
        "Camera Shot 1（0-5秒）：说出「甲，乙。」\n"
        "Camera Shot 2（5-10秒）：继续说出「丙。」\n"
        "</CAMERA_SHOTS>\n<PANEL_PLAN>\n"
        + "\n".join(
            f'<P{panel:02d} camera_shot="{1 if panel <= 6 else 2}">动作。</P{panel:02d}>'
            for panel in range(1, 13)
        )
        + "\n</PANEL_PLAN>"
    )
    split_shots = node._camera_shots_from_storyboard_prompt(split_dialogue_prompt)
    split_panels = node._panel_plan_from_storyboard_prompt(split_dialogue_prompt)
    split_errors = node._clip_storyboard_prompt_errors(
        prompt=split_dialogue_prompt,
        source_text="角色：「甲，乙。丙。」",
        target_duration_seconds=10,
        camera_shots=split_shots,
        panel_plan=split_panels,
    )
    if split_errors:
        raise AssertionError(f"dialogue split across camera shots should remain valid: {split_errors}")
    alternate_quote_prompt = split_dialogue_prompt.replace("「", "『").replace("」", "』")
    alternate_quote_errors = node._clip_storyboard_prompt_errors(
        prompt=alternate_quote_prompt,
        source_text="角色：「甲，乙。丙。」",
        target_duration_seconds=10,
        camera_shots=node._camera_shots_from_storyboard_prompt(alternate_quote_prompt),
        panel_plan=node._panel_plan_from_storyboard_prompt(alternate_quote_prompt),
    )
    if alternate_quote_errors:
        raise AssertionError(f"alternate paired dialogue quotes should remain valid: {alternate_quote_errors}")

    clips = {
        str(index): ClipSegment(
            text=f"第 {index} 段，江未晞在古老殿宇行动。",
            role_names=["江未晞"],
            layout_names=["古老殿宇"],
            prop_names=[],
        )
        for index in range(1, 18)
    }
    batches = node._clip_segment_batches(clips)
    if [len(batch) for batch in batches] != [4, 4, 4, 4, 1]:
        raise AssertionError("clip_storyboard_prompt batch sizes should be 4, 4, 4, 4, 1")
    if list(batches[1]) != [str(index) for index in range(5, 9)]:
        raise AssertionError("second clip_storyboard_prompt batch should preserve original clip keys 5-8")

    state = ProjectState(
        project_id="clip_storyboard_prompt_batching",
        title="Smoke",
        raw_script="原始故事",
        script=ScriptBundle(
            raw_script="原始故事",
            novel_full={
                "episode_001": "上一集全文",
                "episode_002": "本集全文",
                "episode_003": "下一集全文",
            },
            novel_extract={"episode_002": "本集摘要"},
        ),
        roles={
            "role_江未晞": Role(
                id="role_江未晞",
                name="江未晞",
                intro="主角",
                episode_keys=["episode_002"],
                appearances={
                    "base": RoleAppearance(
                        id="appearance_江未晞_base",
                        role_id="role_江未晞",
                        asset_path="assets/images/roles/role_jiang.png",
                    )
                },
            )
        },
        layouts={
            "layout_古老殿宇": Layout(
                id="layout_古老殿宇",
                name="古老殿宇",
                desc="残破殿宇",
                prompt="残破殿宇",
                episode_keys=["episode_002"],
                asset_path="assets/images/layouts/layout_hall.png",
            )
        },
    )
    clip_prompt_path = layout.node_output_path(tmp_project, "clip_prompt")
    clip_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    clip_prompt_path.write_text(
        json.dumps(
            {
                "clip_prompts": [
                    {
                        "episode_key": "episode_002",
                        "clips": [
                            {
                                "episode_key": "episode_002",
                                "clip_id": f"episode_002_clip_{index:03d}",
                                "clip_index": index,
                                "source_clip_key": str(index),
                                "clip_text": clips[str(index)].text,
                                "role_names": ["江未晞"],
                                "layout_names": ["古老殿宇"],
                                "prop_names": [],
                                "role_ids": ["role_江未晞"],
                                "layout_ids": ["layout_古老殿宇"],
                                "prop_ids": [],
                                "target_duration_seconds": 10,
                                "clip_prompt": f"镜头1（0-10秒）：第 {index} 段的逐镜头提示。",
                                "reference_image_context": [],
                            }
                            for index in range(1, 18)
                        ],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    role_ids = node._role_ids_for_batch(state, episode_key="episode_002", batch_clips=batches[1])
    layout_ids = node._layout_ids_for_batch(state, episode_key="episode_002", batch_clips=batches[1])
    if role_ids != ["role_江未晞"]:
        raise AssertionError(f"unexpected role ids for batch: {role_ids}")
    if layout_ids != ["layout_古老殿宇"]:
        raise AssertionError(f"unexpected layout ids for batch: {layout_ids}")

    refs, ref_context = node._clip_storyboard_prompt_reference_refs(
        tmp_project,
        state,
        role_ids=role_ids,
        layout_ids=layout_ids,
        prop_ids=[],
    )
    if len(refs) != 2:
        raise AssertionError("clip_storyboard_prompt should attach roleboard and layout image refs")
    if [item["input_slot"] for item in ref_context] != ["image_1", "image_2"]:
        raise AssertionError("reference image context should expose stable image slots")
    reference_prefix = node._reference_image_prompt_prefix(ref_context)
    if reference_prefix != "图1是【江未晞：主角】，图2是【古老殿宇：残破殿宇】。":
        raise AssertionError(f"unexpected reference image prompt prefix: {reference_prefix}")

    full_context = node._episode_window_full_context(
        tmp_project,
        state,
        "episode_002",
        ["episode_001", "episode_002", "episode_003"],
    )
    if list(full_context) != ["episode_001", "episode_002", "episode_003"]:
        raise AssertionError("clip_storyboard_prompt should include previous/current/next full episode context")

    batch_output = StoryboardPromptOutput.model_validate(
        {
            "storyboards": [
                {
                    "episode_key": "episode_002",
                    "clips": [_clip_payload("episode_002", index) for index in range(5, 9)],
                }
            ]
        }
    )
    validated = node.validate_clip_storyboard_prompt_output(
        batch_output,
        expected_episode_keys=["episode_002"],
        expected_clip_segments={"episode_002": batches[1]},
    )
    if validated.storyboards[0].clips[0].clip_id != "episode_002_clip_005":
        raise AssertionError("partial clip_storyboard_prompt batch should preserve original clip index")

    rendered = PromptStore().render(
        "clip_storyboard_prompt",
        final_aspect_ratio="9:16",
        storyboard_panel_count=node.STORYBOARD_PANEL_COUNT,
        storyboard_grid=node.storyboard_grid(),
        storyboard_sheet_aspect_ratio=node.storyboard_sheet_aspect_ratio(),
        storyboard_panel_aspect_ratio=node.storyboard_panel_aspect_ratio(),
        novel_extract=node._format_json({"episode_002": "本集摘要"}),
        current_episode_full=full_context["episode_002"],
        adjacent_episode_summaries=node._format_json(
            {"episode_001": "上一集摘要", "episode_003": "下一集摘要"}
        ),
        visual_tone="电影级冷灰视觉调性",
        roleboard_context=node._format_json(node._roleboard_context_for_ids(tmp_project, state, role_ids)),
        layout_context=node._format_json(node._layout_context_for_ids(state, layout_ids)),
        prop_context=node._format_json([]),
        clip_prompt_context=node._format_json({"episode_002_clip_009": {"clip_prompt": "镜头提示", "target_duration_seconds": 10}}),
        allowed_clip_ids=node._format_json(["episode_002_clip_005", "episode_002_clip_006", "episode_002_clip_007", "episode_002_clip_008"]),
        clip_segments=node._format_json({"episode_002": batches[1]}),
    )
    if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", rendered):
        raise AssertionError("clip_storyboard_prompt render left unresolved template variables")
    if "所有对白原文统一放在中文直角引号 `「……」` 中" not in rendered:
        raise AssertionError("clip_storyboard_prompt must require stable dialogue quote formatting")
    safety_requirements = (
        "不主动重复或强化“未成年、高中生、少女、幼小”等年龄标签",
        "不连续安排脱离动作语境的手、脚、嘴唇、胸口、颈部等身体局部特写",
        "不得为了规避而删除剧情关键动作",
        "不输出“安全、合规、审核、规避、敏感词、政策”等元说明",
    )
    for requirement in safety_requirements:
        if requirement not in rendered:
            raise AssertionError(f"clip_storyboard_prompt missing image-safety requirement: {requirement}")
    for removed_field in (
        "clip_batch",
        "clip_count_by_episode",
        "total_clip_count_by_episode",
        "目标集：",
        "项目标题",
        "project_context",
        "原始故事：",
        "随请求附加的参考图片顺序",
    ):
        if removed_field in rendered:
            raise AssertionError(f"removed prompt field remains rendered: {removed_field}")

    asyncio.run(
        _run_parallel_generation_smoke(
            settings=settings,
            layout=layout,
            tmp_project=tmp_project,
            state=state,
            clips=clips,
        )
    )
    asyncio.run(
        _run_clip_selection_smoke(
            settings=settings,
            layout=layout,
            tmp_project=tmp_project,
            state=state,
            clips=clips,
        )
    )

    marker = ROOT / ".tmp" / "clip_storyboard_prompt_batching_smoke.ok"
    marker.write_text("ok\n", encoding="utf-8")
    print("clip_storyboard_prompt_batching_smoke: ok")


if __name__ == "__main__":
    main()
