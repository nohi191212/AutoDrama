from __future__ import annotations

import base64
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    BGMDesignOutput,
    LayoutDedupeReviewOutput,
    LayoutDesignOutput,
    PropDesignOutput,
    RoleAppearanceDesignOutput,
    RoleDesignOutput,
    RoleVoiceDesignOutput,
    ScriptCompressOutput,
    ScriptDetailOutput,
    ScriptOutlineOutput,
    ScriptPolishOutput,
    StoryboardEpisodeOutput,
)
from autodrama.providers.base import (
    AssetRef,
    ImageGenerationResult,
    MusicGenerationResult,
    VideoGenerationResult,
    VoiceDesignResult,
    VoiceSynthesisResult,
)

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
        elif schema is RoleAppearanceDesignOutput or node_name == "role_appearance_design":
            data = {
                "appearances": [
                    {
                        "role_name": "林舟",
                        "name": "base",
                        "desc": "二十八岁职场青年，身形偏瘦，短发，眼下有轻微疲惫感，五官清秀但神情克制。",
                        "prompt": "真人电影质感，二十八岁中国职场青年男性，短发，身形偏瘦，五官清秀，眼神疲惫但冷静，半身角色设定图，干净背景，自然电影布光。",
                    },
                    {
                        "role_name": "苏晚",
                        "name": "base",
                        "desc": "二十六岁数据分析师，身形修长，眉眼清冷，气质理性克制。",
                        "prompt": "真人电影质感，二十六岁中国女性数据分析师，身形修长，眉眼清冷，气质理性克制，半身角色设定图，干净背景，自然电影布光。",
                    },
                    {
                        "role_name": "赵启",
                        "name": "base",
                        "desc": "三十五岁部门主管，体型中等偏壮，五官锐利，神情自负，压迫感强。",
                        "prompt": "真人电影质感，三十五岁中国男性部门主管，体型中等偏壮，五官锐利，神情自负，半身角色设定图，干净背景，自然电影布光。",
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
        elif schema is PropDesignOutput or node_name == "prop_design":
            data = {
                "props": [
                    {
                        "name": "被调包的合同",
                        "desc": "一份装订整齐的商务合同，关键页纸张颜色略浅，页码和边缘纹理与其他页不一致。",
                        "prompt": "真人电影质感，商务合同特写，装订整齐，关键页纸张颜色略浅，页码和纸张边缘细节清晰，办公室桌面，自然冷色光。",
                        "status": "normal",
                    },
                    {
                        "name": "邮件截图",
                        "desc": "手机或电脑上的旧邮件截图，能看到时间线和附件记录，是反击证据。",
                        "prompt": "真人电影质感，电脑屏幕上的邮件截图特写，时间线和附件记录清晰但不过度曝光，办公室环境反光自然。",
                        "status": "normal",
                    },
                ]
            }
        elif schema is ScriptCompressOutput or node_name == "script_compress":
            data = {
                "simple_script": {
                    key: f"第{index}集：林舟发现合同异常，在苏晚帮助下整理证据，并在会议上反击赵启。"
                    for index, key in enumerate(episode_keys, start=1)
                },
                "global_script": "林舟被赵启陷害后发现合同调包线索，在苏晚帮助下用邮件时间线完成反击。",
            }
        elif schema is LayoutDesignOutput or node_name == "layout_design":
            data = {
                "layouts": [
                    {
                        "name": "雨夜办公室",
                        "desc": "深夜办公区，冷白灯和窗外雨光交织，桌面散落合同和电脑，适合悬疑调查氛围。",
                        "prompt": "真人电影质感，深夜现代办公室，窗外雨夜，冷白灯，桌面散落合同和打开的电脑，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                    {
                        "name": "会议室",
                        "desc": "玻璃会议室，长桌、投影屏和冷色顶灯，适合公开对峙和证据投屏。",
                        "prompt": "真人电影质感，现代公司玻璃会议室，长桌，投影屏，冷色顶灯，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                ]
            }
        elif schema is LayoutDedupeReviewOutput or node_name == "layout_dedupe_review":
            data = {
                "layouts": [
                    {
                        "name": "雨夜办公室",
                        "desc": "深夜办公区，冷白灯和窗外雨光交织，桌面散落合同和电脑，适合悬疑调查氛围。",
                        "prompt": "真人电影质感，深夜现代办公室，窗外雨夜，冷白灯，桌面散落合同和打开的电脑，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                    {
                        "name": "会议室",
                        "desc": "玻璃会议室，长桌、投影屏和冷色顶灯，适合公开对峙和证据投屏。",
                        "prompt": "真人电影质感，现代公司玻璃会议室，长桌，投影屏，冷色顶灯，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                ],
                "merge_notes": ["未发现需要合并的重复场景。"],
            }
        elif schema is BGMDesignOutput or node_name == "bgm_design":
            data = {
                "bgms": [
                    {
                        "name": "暗线推进",
                        "mood": "紧张、克制、悬疑",
                        "prompt": "紧张克制的悬疑影视配乐，低频脉冲、轻微电子氛围、节奏逐步推进，适合办公室调查和证据发现。",
                        "usage_hint": "调查、发现线索、反击前铺垫。",
                    },
                    {
                        "name": "公开反击",
                        "mood": "压迫、爆发、胜负揭晓",
                        "prompt": "短剧高潮反击配乐，弦乐和电子鼓逐步增强，节奏果断，适合会议室投屏证据和反派失控。",
                        "usage_hint": "会议对峙和反击高潮。",
                    },
                ]
            }
        elif schema is StoryboardEpisodeOutput or node_name == "storyboard_generation":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            data = {
                "episode_key": episode_key,
                "shots": [
                    {
                        "shot_id": f"{episode_key}_shot_001",
                        "index": 1,
                        "layout_id": "layout_雨夜办公室",
                        "title": "发现异常合同",
                        "content": "林舟在雨夜办公室翻看合同，注意到关键页纸张颜色不一致。",
                        "camera_shooting_angle": "桌面近景切到人物中近景",
                        "camera_movement": "缓慢推近",
                        "focal_length": "50mm",
                        "duration_seconds": 6,
                        "dialogue": [],
                        "role_ids": ["role_林舟"],
                        "role_appearance_ids": ["role_林舟_appearance_base"],
                        "role_audio_ids": [],
                        "prop_ids": ["prop_被调包的合同"],
                        "bgm_id": "bgm_暗线推进",
                        "ref_frame_prompt": "林舟在雨夜办公室桌前检查合同，桌面近景，纸张色差清楚。",
                        "video_prompt": "镜头缓慢推近合同关键页，林舟神情由疲惫转为警觉。",
                    },
                    {
                        "shot_id": f"{episode_key}_shot_002",
                        "index": 2,
                        "layout_id": "layout_会议室",
                        "title": "会议室反击",
                        "content": "赵启在会议室施压，林舟投屏邮件时间线，会议室瞬间安静。",
                        "camera_shooting_angle": "会议桌对峙中景",
                        "camera_movement": "轻微横移后定格",
                        "focal_length": "35mm",
                        "duration_seconds": 8,
                        "dialogue": ["林舟：这份合同被换过，时间线就在这里。"],
                        "role_ids": ["role_林舟", "role_赵启"],
                        "role_appearance_ids": ["role_林舟_appearance_base", "role_赵启_appearance_base"],
                        "role_audio_ids": ["role_林舟_audio_normal"],
                        "prop_ids": ["prop_邮件截图"],
                        "bgm_id": "bgm_公开反击",
                        "ref_frame_prompt": "现代会议室，林舟站在投影屏旁展示邮件时间线，赵启表情僵住。",
                        "video_prompt": "林舟投屏证据，镜头横移扫过沉默的会议桌，最终停在赵启僵硬的表情上。",
                    },
                ],
            }
        else:
            raise ValueError(f"Fake provider has no fixture for schema {schema.__name__}")

        return schema.model_validate(data)


class FakeImageProvider:
    name = "fake"
    model = "fake-image"

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        del refs, size
        metadata = metadata or {}
        image_bytes = f"fake image: {metadata.get('asset_id', 'asset')}: {prompt}".encode("utf-8")
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(image_bytes).decode("ascii")],
            request_id=f"fake-image-request-{metadata.get('asset_id', 'asset')}",
            usage={"image_count": 1},
            raw_response={
                "output": {"image": "<base64 image omitted>"},
                "request_id": f"fake-image-request-{metadata.get('asset_id', 'asset')}",
            },
        )


class FakeMusicProvider:
    name = "fake"
    model = "fake-music"

    async def generate_music(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MusicGenerationResult:
        metadata = metadata or {}
        audio_bytes = f"fake music: {metadata.get('asset_id', 'bgm')}: {prompt}".encode("utf-8")
        return MusicGenerationResult(
            provider=self.name,
            model=self.model,
            audio_id=f"fake_music_{metadata.get('asset_id', 'bgm')}",
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_format=str(metadata.get("format", "mp3")),
            duration_seconds=30,
            lyrics=lyrics,
            request_id=f"fake-music-request-{metadata.get('asset_id', 'bgm')}",
            usage={"duration": 30},
            raw_response={
                "output": {"audio": {"data": "<base64 audio omitted>"}},
                "request_id": f"fake-music-request-{metadata.get('asset_id', 'bgm')}",
            },
        )


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
