"""Tests for the LangGraph pipeline (with mocked nodes)."""

from unittest.mock import MagicMock, patch

from autodrama.config.loader import ConfigLoader
from autodrama.pipeline.graph import build_pipeline


def test_pipeline_success_path(test_config_path: str) -> None:
    """Verify that a successful pipeline invocation flows end-to-end."""
    cfg = ConfigLoader.load(test_config_path)

    with (
        patch("autodrama.pipeline.nodes.script_node.LLMFactory") as mock_llm_factory,
        patch("autodrama.pipeline.nodes.scene_node.LLMFactory") as mock_llm_factory2,
        patch("autodrama.pipeline.nodes.image_node.ImageFactory") as mock_img_factory,
        patch("autodrama.pipeline.nodes.audio_node.TTSFactory") as mock_tts_factory,
    ):
        # Mock LLM for script generation
        mock_llm = MagicMock()
        mock_llm.chat_with_structured_output.return_value.content = (
            '{"title":"Test","genre":"thriller","style":"realistic","logline":"","synopsis":"",'
            '"characters":[{"name":"A","gender":"female","personality":"brave","voice_description":""}],'
            '"scenes":[{"scene_number":1,"location":"office","time_of_day":"night","atmosphere":"tense",'
            '"description":"Dark office","dialogue":[{"character":"A","text":"Go!","emotion":"urgent",'
            '"timing_hint":1.5,"shot_type":"close-up"}],"narration":"","image_keywords":[]}]}'
        )
        mock_llm_factory.create.return_value = mock_llm
        # Mock LLM for scene planning
        mock_llm2 = MagicMock()
        mock_llm2.chat_with_structured_output.return_value.content = (
            '[{"scene_number":1,"background_music":"","sound_effects":[],'
            '"shots":[{"shot_index":0,"camera_angle":"eye-level","frame_type":"close-up",'
            '"subject_focus":"A","background_description":"office","lighting":"low-key",'
            '"image_prompt":"Woman in dark office, 4K","negative_prompt":""}]}]'
        )
        mock_llm_factory2.create.return_value = mock_llm2

        # Mock image generator
        mock_img = MagicMock()
        mock_img.generate.return_value.image_path = "/tmp/test_img.png"
        mock_img.generate.return_value.scene_number = 1
        mock_img.generate.return_value.shot_index = 0
        mock_img_factory.create.return_value = mock_img

        # Mock TTS
        mock_tts = MagicMock()
        mock_tts.synthesize_batch.return_value = []
        mock_tts_factory.create.return_value = mock_tts

        pipeline = build_pipeline(cfg)

        result = pipeline.invoke({
            "concept": "Test drama",
            "input_params": {},
            "max_retries": 3,
            "retry_count": 0,
            "errors": [],
            "warnings": [],
        })

        # Script should be populated
        assert result.get("script") is not None
        assert result["script"]["title"] == "Test"


def test_pipeline_empty_concept_fails(test_config_path: str) -> None:
    """Validate input rejects empty concepts."""
    cfg = ConfigLoader.load(test_config_path)

    with (
        patch("autodrama.pipeline.nodes.script_node.LLMFactory"),
        patch("autodrama.pipeline.nodes.scene_node.LLMFactory"),
        patch("autodrama.pipeline.nodes.image_node.ImageFactory"),
        patch("autodrama.pipeline.nodes.audio_node.TTSFactory"),
    ):
        pipeline = build_pipeline(cfg)

    result = pipeline.invoke({
        "concept": "",
        "max_retries": 1,
        "retry_count": 0,
        "errors": [],
        "warnings": [],
    })

    # Should have an error for empty concept
    errors = result.get("errors", [])
    assert len(errors) > 0
    assert "Empty concept" in errors[0]["error"]
