"""Tests for scene planning module."""

import json
from unittest.mock import MagicMock

from autodrama.llm.base import LLMResponse
from autodrama.scene.planner import ScenePlanner

MOCK_PLANS = [
    {
        "scene_number": 1,
        "background_music": "tense",
        "shots": [
            {
                "shot_index": 0,
                "camera_angle": "eye-level",
                "frame_type": "close-up",
                "subject_focus": "Alice worried",
                "background_description": "dark office",
                "lighting": "low-key",
                "image_prompt": "A worried woman in a dark office, cinematic, 4K",
                "negative_prompt": "daylight",
            },
            {
                "shot_index": 1,
                "camera_angle": "high-angle",
                "frame_type": "medium",
                "subject_focus": "Bob near the door",
                "background_description": "office corridor",
                "lighting": "moody",
                "image_prompt": "A cautious man standing near a door, moody cinematic, 4K",
                "negative_prompt": "daylight, smiling",
            },
        ],
    },
]


def test_plan_returns_scene_plans(mock_llm_provider: MagicMock, sample_script) -> None:
    mock_llm_provider.chat_with_structured_output.return_value = LLMResponse(
        content=json.dumps(MOCK_PLANS, ensure_ascii=False),
        model="mock-model",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )

    planner = ScenePlanner(mock_llm_provider)
    plans = planner.plan(sample_script)

    assert len(plans) == 1
    assert plans[0].scene_number == 1
    assert plans[0].shots[0].frame_type == "close-up"
    assert len(plans[0].shots) == 2


def test_plan_fills_missing_prompts(mock_llm_provider: MagicMock, sample_script) -> None:
    # Return plans with empty image_prompt
    plans_no_prompt = [
        {
            "scene_number": 1,
            "background_music": "",
            "shots": [
                {
                    "shot_index": 0,
                    "camera_angle": "eye-level",
                    "frame_type": "close-up",
                    "subject_focus": "Alice",
                    "background_description": "office",
                    "lighting": "low-key",
                    "image_prompt": "",
                    "negative_prompt": "",
                },
            ],
        },
    ]
    mock_llm_provider.chat_with_structured_output.return_value = LLMResponse(
        content=json.dumps(plans_no_prompt, ensure_ascii=False),
        model="mock-model",
        usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
    )

    planner = ScenePlanner(mock_llm_provider)
    plans = planner.plan(sample_script)

    # The prompt_builder should have filled the empty prompt
    assert plans[0].shots[0].image_prompt
    assert "Alice" in plans[0].shots[0].image_prompt
