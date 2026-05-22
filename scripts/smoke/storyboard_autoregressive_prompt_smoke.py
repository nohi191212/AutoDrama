from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    ScriptBundle,
    StoryboardNextShotOutput,
)
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.services.storyboard_service import StoryboardService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingFakeTextProvider(FakeTextProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        self.calls.append(
            {
                "schema": schema.__name__,
                "prompt": prompt,
                "metadata": dict(metadata or {}),
            }
        )
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


def build_state() -> ProjectState:
    return ProjectState(
        project_id="storyboard_autoregressive_prompt_smoke",
        title="Storyboard Autoregressive Prompt Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        script=ScriptBundle(
            raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
            novel_extract={
                "episode_001": (
                    "雨夜办公室，林舟发现合同关键页纸张颜色不对。苏晚递来旧邮件截图，"
                    "邮件附件时间线证明合同被调包。次日会议室，赵启试图压住议程，"
                    "林舟投屏证据并公开反击。"
                )
            }
        ),
        roles={
            "role_林舟": Role(
                id="role_林舟",
                name="林舟",
                intro="二十八岁职场青年，疲惫克制，关键时刻冷静锋利。",
                appearances={
                    "base": RoleAppearance(
                        id="role_林舟_appearance_base",
                        role_id="role_林舟",
                        name="base",
                        desc="短发，深灰衬衫，眼神疲惫但冷静。",
                        prompt="真人电影质感，中国青年男性，短发，深灰衬衫，神情克制。",
                    )
                },
                audio={
                    "normal": RoleAudio(
                        id="role_林舟_audio_normal",
                        role_id="role_林舟",
                        emotion="normal",
                        desc="青年男性音色，低沉克制。",
                    )
                },
            ),
            "role_苏晚": Role(
                id="role_苏晚",
                name="苏晚",
                intro="二十六岁数据分析师，理性冷静，善于发现证据。",
                appearances={
                    "base": RoleAppearance(
                        id="role_苏晚_appearance_base",
                        role_id="role_苏晚",
                        name="base",
                        desc="眉眼清冷，白衬衫，动作利落。",
                        prompt="真人电影质感，中国年轻女性，白衬衫，理性克制。",
                    )
                },
            ),
            "role_赵启": Role(
                id="role_赵启",
                name="赵启",
                intro="三十五岁部门主管，自负强势，证据曝光时会紧张失控。",
                appearances={
                    "base": RoleAppearance(
                        id="role_赵启_appearance_base",
                        role_id="role_赵启",
                        name="base",
                        desc="深色西装，表情自负，眼神有压迫感。",
                        prompt="真人电影质感，中国中年男性，深色西装，神情强势。",
                    )
                },
            ),
        },
        props={
            "prop_被调包的合同": Prop(
                id="prop_被调包的合同",
                name="被调包的合同",
                desc="关键页纸张颜色略浅，页码和装订孔有细微错位。",
                prompt="真人电影质感，商务合同特写，关键页色差和装订孔错位清楚。",
            ),
            "prop_邮件截图": Prop(
                id="prop_邮件截图",
                name="邮件截图",
                desc="旧邮件附件记录和时间线截图，是合同调包证据。",
                prompt="真人电影质感，电脑屏幕邮件截图，时间线和附件记录清楚。",
            ),
        },
        layouts={
            "layout_雨夜办公室": Layout(
                id="layout_雨夜办公室",
                name="雨夜办公室",
                desc="深夜现代办公室，冷白灯、电脑冷蓝光、窗外雨痕和城市霓虹反射。",
                prompt="真人电影质感，雨夜现代办公室，冷白灯，窗外雨痕，桌面合同和电脑。",
            ),
            "layout_会议室": Layout(
                id="layout_会议室",
                name="会议室",
                desc="玻璃会议室，长桌、投影屏、冷色顶灯和雨夜城市反射。",
                prompt="真人电影质感，现代玻璃会议室，长桌，投影屏，冷色顶灯。",
            ),
        },
        metadata={
            "episode_duration_seconds": 30,
            "visual_style_label": "真人电影质感",
            "visual_style_prompt": "真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
        },
    )


async def main_async() -> int:
    provider = RecordingFakeTextProvider()
    service = StoryboardService(PromptStore())
    progress_snapshots: list[tuple[str, str, int]] = []
    state = build_state()
    current_novel_full = (
        "雨夜办公室，林舟发现合同关键页纸张颜色不对。苏晚递来旧邮件截图，"
        "邮件附件时间线证明合同被调包。次日会议室，赵启试图压住议程，"
        "林舟投屏证据并公开反击。"
    )

    def record_progress(episode, shot) -> None:
        progress_snapshots.append((episode.episode_key, shot.shot_id, len(episode.shots)))

    output = await service.storyboard_episode(
        state,
        provider,
        episode_key="episode_001",
        novel_extract_all=dict(state.script.novel_extract),
        current_novel_full=current_novel_full,
        on_shot_generated=record_progress,
    )

    require(output.episode_key == "episode_001", f"Unexpected episode_key: {output.episode_key}")
    require(len(output.shots) == 3, f"Expected 3 autoregressive shots, got {len(output.shots)}")
    require(service.last_text_call_count == 3, f"Expected 3 text calls, got {service.last_text_call_count}")
    require(len(provider.calls) == 3, f"Expected 3 provider calls, got {len(provider.calls)}")
    for index, shot in enumerate(output.shots, start=1):
        require(shot.index == index, f"Shot index was not normalized: {shot.index}")
        require(shot.shot_id == f"episode_001_shot_{index:03d}", f"Unexpected shot_id: {shot.shot_id}")
        require(" 秒：" in shot.video_prompt, f"Shot {index} video_prompt missing official-style timed segment")
        require(
            any(marker in shot.video_prompt for marker in ("全程不切镜", "可硬切", "J-Cut 式", "硬切到")),
            f"Shot {index} video_prompt missing natural cut/continuity wording",
        )
        require(shot.source_coverage is not None, f"Shot {index} missing source coverage")
    require(
        progress_snapshots == [
            ("episode_001", "episode_001_shot_001", 1),
            ("episode_001", "episode_001_shot_002", 2),
            ("episode_001", "episode_001_shot_003", 3),
        ],
        f"Unexpected progress snapshots: {progress_snapshots}",
    )

    schema_names = {call["schema"] for call in provider.calls}
    require(schema_names == {StoryboardNextShotOutput.__name__}, f"Unexpected schemas: {schema_names}")
    require("已经生成并已覆盖的分镜" in provider.calls[0]["prompt"], "Prompt missing covered storyboard guidance")
    require("当前 shot 起点原文" in provider.calls[0]["prompt"], "Prompt missing next shot source cursor")
    require("0-4 秒：" in provider.calls[0]["prompt"], "Prompt missing official-style timed video_prompt guidance/example")
    require("方括号标签" in provider.calls[0]["prompt"], "Prompt missing guidance against old bracketed style")
    require("硬切到" in provider.calls[0]["prompt"], "Prompt missing natural explicit cut guidance/example")
    require('"source_coverage"' in provider.calls[1]["prompt"], "Second prompt missing first shot source coverage")
    require("episode_001_shot_001" in provider.calls[1]["prompt"], "Second prompt missing first shot id")
    require("episode_001_shot_002" in provider.calls[2]["prompt"], "Third prompt missing two generated shots context")

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "storyboard_autoregressive_prompt"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (output_dir / f"episode_{stamp}.json").write_text(
        output.model_dump_json(indent=2),
        encoding="utf-8",
    )
    for index, call in enumerate(provider.calls, start=1):
        (output_dir / f"prompt_{index:03d}_{stamp}.md").write_text(call["prompt"], encoding="utf-8")

    print("storyboard_autoregressive_prompt_smoke=ok")
    print(f"shots={len(output.shots)} text_calls={service.last_text_call_count}")
    print(f"output_dir={output_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
