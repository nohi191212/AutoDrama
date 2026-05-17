from pathlib import Path

from autodrama.config import load_settings
from autodrama.providers.bailian_music import BailianMusicProvider
from autodrama.providers.wanxiang import WanxiangImageProvider
from autodrama.providers.qwen import QwenTextProvider
from autodrama.providers.qwen_tts import QwenVoiceDesignProvider
from autodrama.providers.router import ProviderRouter
from autodrama.providers.wanxiang import WanxiangVideoProvider


def test_router_supports_qwen_and_wanxiang(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
project:
  id: demo
  title: Demo
  script_outline_file: ./story.md
output:
  root_dir: ./outputs
providers:
  aliyun:
    base_url: https://dashscope.aliyuncs.com
    api_key_env: DASHSCOPE_API_KEY
    models:
      text: qwen-plus
      voice_design: qwen-voice-design
      voice_clone: qwen-voice-enrollment
      target_model: qwen3-tts-vc-2026-01-22
      clone_target_model: qwen3-tts-vc-2026-01-22
      image: wan2.7-image-pro
      text_to_video: wan2.7-t2v-2026-04-25
      music: fun-music-v1
routing:
  text:
    script: aliyun
  video:
    shot: aliyun
  audio:
    speech: aliyun
  image:
    role: aliyun
  music:
    bgm: aliyun
""",
        encoding="utf-8",
    )

    settings = load_settings(config)
    router = ProviderRouter(settings)

    assert isinstance(router.text("script"), QwenTextProvider)
    assert isinstance(router.video("shot"), WanxiangVideoProvider)
    assert isinstance(router.audio("speech"), QwenVoiceDesignProvider)
    assert isinstance(router.image("role"), WanxiangImageProvider)
    assert isinstance(router.music("bgm"), BailianMusicProvider)
