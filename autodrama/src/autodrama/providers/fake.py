from __future__ import annotations

import base64
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    RoleDesignOutput,
    RoleVoiceDesignOutput,
    ScriptDetailOutput,
    ScriptOutlineOutput,
    ScriptPolishOutput,
)
from autodrama.providers.base import AssetRef, VideoGenerationResult, VoiceDesignResult, VoiceSynthesisResult

T = TypeVar("T", bound=BaseModel)


def _extract_prompt_int(prompt: str, label: str, default: int) -> int:
    match = re.search(rf"{re.escape(label)}\s*[：:]\s*(\d+)", prompt)
    if match:
        return int(match.group(1))
    return default


def _episode_keys(episode_count: int) -> list[str]:
    return [f"episode_{index:03d}" for index in range(1, episode_count + 1)]


class FakeTextProvider:
    name = "fake"

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del temperature
        metadata = metadata or {}
        node_name = metadata.get("node_name")

        episode_count = _extract_prompt_int(prompt, "目标集数", 1)
        episode_duration_seconds = _extract_prompt_int(prompt, "单集目标时长", 30)
        episode_keys = _episode_keys(episode_count)

        if schema is ScriptOutlineOutput or node_name == "script_outline":
            data = {
                "logline": "落魄青年在雨夜发现被调包的合同，决定当众反击。",
                "outline": "林舟被赵启陷害丢掉晋升机会，苏晚提醒他查看旧邮件。林舟逐步发现合同被调包的证据，并在会议上反击。",
                "episode_count": episode_count,
                "target_duration_seconds": episode_duration_seconds,
                "episode_outlines": {
                    key: f"第{index}集：林舟围绕合同调包事件推进调查与反击，冲突逐步升级。"
                    for index, key in enumerate(episode_keys, start=1)
                },
            }
        elif schema is ScriptDetailOutput or node_name == "script_detail":
            data = {
                "detailed_script": {
                    key: (
                        f"第{index}集，雨夜办公室，林舟发现合同页码被替换。"
                        "苏晚递来旧邮件截图，提醒他保存证据。"
                        "赵启在电话里催他认错，林舟沉默片刻后决定反击。"
                    )
                    for index, key in enumerate(episode_keys, start=1)
                }
            }
        elif schema is ScriptPolishOutput or node_name == "script_polish":
            data = {
                "final_script": {
                    key: (
                        f"第{index}集，雨夜，公司只剩林舟还在翻合同。"
                        "他发现关键页纸张颜色不对，刚要放弃，苏晚把三天前的邮件截图推到他面前。"
                        "次日会议，赵启正准备宣布林舟失职，林舟投屏原始合同和邮件时间线。"
                        "会议室安静下来，赵启的笑僵在脸上。"
                    )
                    for index, key in enumerate(episode_keys, start=1)
                },
                "revision_notes": [
                    "强化了合同被调包的视觉线索。",
                    "把反击点集中到会议投屏，便于后续分镜。"
                ],
            }
        elif schema is RoleDesignOutput or node_name == "role_design":
            data = {
                "roles": [
                    {
                        "name": "林舟",
                        "intro": "二十八岁职场青年，长期被压制但观察细致，外表疲惫，关键时刻冷静锋利。",
                        "personality": "隐忍、敏锐、爆发力强",
                        "aliases": ["男主"],
                    },
                    {
                        "name": "苏晚",
                        "intro": "二十六岁数据分析师，理性克制，善于发现证据，是男主反击的关键助力。",
                        "personality": "冷静、聪明、行动果断",
                        "aliases": ["女主"],
                    },
                    {
                        "name": "赵启",
                        "intro": "三十五岁部门主管，精致强势，擅长操控会议节奏，害怕证据曝光。",
                        "personality": "自负、控制欲强、心虚时急躁",
                        "aliases": ["反派"],
                    },
                ]
            }
        elif schema is RoleVoiceDesignOutput or node_name == "role_voice_design":
            data = {
                "role_voices": [
                    {
                        "role_name": "林舟",
                        "emotion": "normal",
                        "desc": "二十八岁青年男声，低沉克制，略带疲惫感，语速中等，咬字清晰。",
                        "sample_text": "我是林舟，一个总在办公室熬到深夜的普通职员。我不擅长争辩，只习惯把每个细节记在心里。最近的风向不太对，但我相信只要冷静下来，总能找到问题的源头。",
                    },
                    {
                        "role_name": "林舟",
                        "emotion": "tense",
                        "desc": "同一青年男声，压低音量，呼吸略紧，语尾收住，表现强忍怒意。",
                        "sample_text": "我是林舟，一个被压力推到角落的职员。我知道现在每句话都可能被误解，所以只能把情绪压住。越是混乱的时候，我越要盯紧那些不该被忽略的细节。",
                    },
                    {
                        "role_name": "苏晚",
                        "emotion": "normal",
                        "desc": "二十六岁女性声音，清冷理性，音色干净，语速稳定。",
                        "sample_text": "我是苏晚，负责数据分析，也习惯用证据说话。很多人只看结果，我更在意过程里那些微小的偏差。只要线索还在，我就不会轻易下结论。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "normal",
                        "desc": "三十五岁男性声音，成熟强势，语气带压迫感，习惯短暂停顿后下判断。",
                        "sample_text": "我是赵启，这个部门的负责人。会议室里的节奏必须由我来掌控，任何失误都要有人承担。一个团队想往上走，就不能让犹豫和软弱拖慢脚步。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "tense",
                        "desc": "同一成熟男声，音量变虚，语速变快，带掩饰慌张的强硬。",
                        "sample_text": "我是赵启，我必须让所有事情看起来仍在掌控之中。越有人追问，我越不能露出破绽。只要会议还没结束，局面就还有被我拉回来的机会。",
                    },
                ]
            }
        else:
            raise ValueError(f"Fake provider has no fixture for schema {schema.__name__}")

        return schema.model_validate(data)


class FakeVideoProvider:
    name = "fake"

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        del prompt, refs, metadata
        return VideoGenerationResult(
            provider=self.name,
            model="fake-video",
            task_id="fake_video_task",
            task_status="PENDING",
            usage={"duration": duration or 5},
            raw_response={"output": {"task_id": "fake_video_task", "task_status": "PENDING"}},
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        return VideoGenerationResult(
            provider=self.name,
            model="fake-video",
            task_id=task_id,
            task_status="SUCCEEDED",
            video_url="https://example.invalid/fake.mp4",
            raw_response={
                "output": {
                    "task_id": task_id,
                    "task_status": "SUCCEEDED",
                    "video_url": "https://example.invalid/fake.mp4",
                }
            },
        )

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        result = await self.submit_video(prompt, refs, duration=duration, metadata=metadata)
        if wait:
            return await self.query_video_task(result.task_id or "fake_video_task")
        return result


class FakeVoiceDesignProvider:
    name = "fake"
    model = "fake-voice-design"
    clone_model = "fake-voice-clone"
    target_model = "fake-tts"

    async def create_voice(
        self,
        *,
        voice_prompt: str,
        preview_text: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        audio_bytes = f"fake audio preview: {preview_text}".encode("utf-8")
        response_format = str(metadata.get("response_format", "wav"))
        sample_rate = int(metadata.get("sample_rate", 24000))
        voice = f"fake_{preferred_name}"
        return VoiceDesignResult(
            provider=self.name,
            model=self.model,
            voice=voice,
            target_model=self.target_model,
            preview_audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            preview_audio_sample_rate=sample_rate,
            preview_audio_format=response_format,
            request_id=f"fake-request-{preferred_name}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "voice": voice,
                    "target_model": self.target_model,
                    "preview_audio": {
                        "data": "<base64 preview audio omitted>",
                        "sample_rate": sample_rate,
                        "response_format": response_format,
                    },
                },
                "usage": {"count": 1},
                "request_id": f"fake-request-{preferred_name}",
            },
        )

    async def clone_voice_from_audio(
        self,
        *,
        source_audio_path: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        voice = f"fake_clone_{preferred_name}"
        return VoiceDesignResult(
            provider=self.name,
            model=self.clone_model,
            voice=voice,
            target_model=self.target_model,
            request_id=f"fake-clone-request-{preferred_name}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "voice": voice,
                    "target_model": self.target_model,
                    "source_audio_path": source_audio_path,
                },
                "usage": {"count": 1},
                "request_id": f"fake-clone-request-{preferred_name}",
                "metadata": metadata,
            },
        )

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        metadata = metadata or {}
        response_format = str(metadata.get("response_format", "wav"))
        sample_rate = int(metadata.get("sample_rate", 24000))
        audio_bytes = f"fake synthesized audio: {voice}: {text}".encode("utf-8")
        return VoiceSynthesisResult(
            provider=self.name,
            model=self.target_model,
            voice=voice,
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_sample_rate=sample_rate,
            audio_format=response_format,
            request_id=f"fake-synthesis-request-{voice}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "audio": {
                        "data": "<base64 audio omitted>",
                        "sample_rate": sample_rate,
                        "response_format": response_format,
                    }
                },
                "usage": {"count": 1},
                "request_id": f"fake-synthesis-request-{voice}",
            },
        )
