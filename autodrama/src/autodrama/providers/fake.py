from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    RoleDesignOutput,
    RoleVoiceDesignOutput,
    ScriptDetailOutput,
    ScriptOutlineOutput,
    ScriptPolishOutput,
)

T = TypeVar("T", bound=BaseModel)


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
        del prompt, temperature
        metadata = metadata or {}
        node_name = metadata.get("node_name")

        if schema is ScriptOutlineOutput or node_name == "script_outline":
            data = {
                "logline": "落魄青年在雨夜发现被调包的合同，决定当众反击。",
                "outline": "男主被同事陷害丢掉晋升机会，女主提醒他查看旧邮件。男主在会议上拿出证据，反派计划败露。",
                "episode_count": 1,
                "target_duration_seconds": 30,
            }
        elif schema is ScriptDetailOutput or node_name == "script_detail":
            data = {
                "detailed_script": {
                    "episode_001": "雨夜办公室，林舟发现合同页码被替换。苏晚递来旧邮件截图。第二天会议上，林舟当众展示证据，赵启脸色骤变。"
                }
            }
        elif schema is ScriptPolishOutput or node_name == "script_polish":
            data = {
                "final_script": {
                    "episode_001": "雨夜，公司只剩林舟还在翻合同。他发现关键页的纸张颜色不对，刚要放弃，苏晚把三天前的邮件截图推到他面前。次日会议，赵启正准备宣布林舟失职，林舟投屏原始合同和邮件时间线。会议室安静下来，赵启的笑僵在脸上。"
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
                        "sample_text": "这份合同，不是我昨天交上去的那一份。",
                    },
                    {
                        "role_name": "林舟",
                        "emotion": "tense",
                        "desc": "同一青年男声，压低音量，呼吸略紧，语尾收住，表现强忍怒意。",
                        "sample_text": "你们看这里的时间线。",
                    },
                    {
                        "role_name": "苏晚",
                        "emotion": "normal",
                        "desc": "二十六岁女性声音，清冷理性，音色干净，语速稳定。",
                        "sample_text": "别急，我把原始邮件找出来了。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "normal",
                        "desc": "三十五岁男性声音，成熟强势，语气带压迫感，习惯短暂停顿后下判断。",
                        "sample_text": "这件事，公司必须有一个交代。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "tense",
                        "desc": "同一成熟男声，音量变虚，语速变快，带掩饰慌张的强硬。",
                        "sample_text": "这不可能，你从哪里拿到的？",
                    },
                ]
            }
        else:
            raise ValueError(f"Fake provider has no fixture for schema {schema.__name__}")

        return schema.model_validate(data)
