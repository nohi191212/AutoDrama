from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.aliyun.audio.qwen_tts import QwenVoiceDesignProvider
from autodrama.providers.aliyun.image.wanxiang import WanxiangImageProvider
from autodrama.providers.aliyun.music.fun_music import BailianMusicProvider
from autodrama.providers.aliyun.text.qwen import QwenTextProvider
from autodrama.providers.aliyun.video.wanxiang import WanxiangVideoProvider
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider
from autodrama.providers.router import ProviderRouter
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider


def test_router_supports_qwen_wanxiang_and_rightcode(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
project:
  id: demo
  title: Demo
  script_chapters_dir: ./chapters
output:
  root_dir: ./outputs
providers:
  aliyun:
    base_url: https://dashscope.aliyuncs.com
    api_key_env: ALIYUN_API_KEY
    models:
      text: qwen-plus
      voice_design: qwen-voice-design
      voice_clone: qwen-voice-enrollment
      target_model: qwen3-tts-vc-2026-01-22
      clone_target_model: qwen3-tts-vc-2026-01-22
      image: wan2.7-image-pro
      text_to_video: wan2.7-t2v-2026-04-25
      music: fun-music-v1
  rightcode:
    base_url: https://www.right.codes/draw
    api_key_env: RIGHTCODE_API_KEY
    models:
      image: gpt-image-2
  volcengine:
    base_url: https://openspeech.bytedance.com
    api_key_env: VOLCENGINE_API_KEY
    models:
      speech_synthesis: seed-tts-2.0
      speech_synthesis_fallback: seed-tts-1.0
      voice_design: voice_design
      voice_clone: voice_clone
      tts: seed-icl-2.0
    options:
      speaker_id_pool:
        - S_demo_001
routing:
  text:
    script: aliyun
  video:
    shot: aliyun
  audio:
    speech: volcengine
  image:
    role: rightcode
    prop: aliyun
  music:
    bgm: aliyun
""",
        encoding="utf-8",
    )

    settings = load_settings(config)
    router = ProviderRouter(settings)

    assert isinstance(router.text("script"), QwenTextProvider)
    assert isinstance(router.video("shot"), WanxiangVideoProvider)
    assert isinstance(router.audio("speech"), VolcengineSeedTTSProvider)
    assert isinstance(router.image("role"), RightCodeImageProvider)
    assert isinstance(router.image("prop"), WanxiangImageProvider)
    assert isinstance(router.music("bgm"), BailianMusicProvider)


def test_router_keeps_aliyun_audio_alias(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
project:
  id: demo
  title: Demo
  script_chapters_dir: ./chapters
output:
  root_dir: ./outputs
providers:
  aliyun:
    base_url: https://dashscope.aliyuncs.com
    api_key_env: ALIYUN_API_KEY
    models:
      voice_design: qwen-voice-design
      voice_clone: qwen-voice-enrollment
      target_model: qwen3-tts-vd-2026-01-26
      clone_target_model: qwen3-tts-vc-2026-01-22
routing:
  audio:
    speech: aliyun
""",
        encoding="utf-8",
    )

    settings = load_settings(config)
    router = ProviderRouter(settings)

    assert isinstance(router.audio("speech"), QwenVoiceDesignProvider)
