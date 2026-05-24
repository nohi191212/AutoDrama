from __future__ import annotations

import base64
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, Settings  # noqa: E402
from autodrama.providers.aliyun.omni.qwen_omni import QwenOmniAudioJudgeProvider  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "qwen_omni_router_payload"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    settings.providers = {
        "aliyun": ProviderSettings(
            base_url="https://dashscope.aliyuncs.com",
            api_key_env="ALIYUN_API_KEY",
            models={"omni": "qwen-omni-turbo"},
        )
    }
    provider = ProviderRouter(settings).judge("voice_catalog_profile")
    require(isinstance(provider, QwenOmniAudioJudgeProvider), "default judge should use Qwen Omni")
    require(provider.model == "qwen3.5-omni-plus", f"default alias should prefer qwen3.5 judge model: {provider.model}")
    require(
        provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1",
        f"default alias should normalize compatible base_url: {provider.base_url}",
    )
    require(provider.use_response_format is False, "Qwen Omni should not send response_format unless explicitly enabled")

    settings.routing = {"judge": {"voice_catalog_profile": "aliyun"}}
    aliyun_provider = ProviderRouter(settings).judge("voice_catalog_profile")
    require(
        aliyun_provider.model == "qwen3.5-omni-plus",
        f"aliyun judge alias should also prefer qwen3.5 judge model: {aliyun_provider.model}",
    )

    settings.providers["aliyun_omni"] = ProviderSettings(
        base_url="https://dashscope-intl.aliyuncs.com",
        api_key_env="ALIYUN_API_KEY",
        models={"audio_judge": "qwen3.5-omni-flash"},
        options={"omni_response_format": True},
    )
    settings.routing = {"judge": {"voice_catalog_profile": "qwen_omni"}}
    explicit_provider = ProviderRouter(settings).judge("voice_catalog_profile")
    require(
        isinstance(explicit_provider, QwenOmniAudioJudgeProvider),
        "qwen_omni alias should use Qwen Omni",
    )
    require(
        explicit_provider.model == "qwen3.5-omni-flash",
        f"qwen_omni alias should reuse providers.aliyun_omni settings: {explicit_provider.model}",
    )
    require(
        explicit_provider.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        f"explicit omni base_url should be normalized: {explicit_provider.base_url}",
    )
    require(explicit_provider.use_response_format is True, "explicit omni_response_format should be honored")

    sample_path = tmp_dir / "sample.mp3"
    sample_bytes = b"ID3" + bytes(range(16))
    sample_path.write_bytes(sample_bytes)
    content = QwenOmniAudioJudgeProvider._audio_content(
        AssetRef(id="sample", type="audio", path=str(sample_path), metadata={"format": "mp3"})
    )
    input_audio = content["input_audio"]
    data = input_audio["data"]
    require(input_audio["format"] == "mp3", f"local audio format mismatch: {input_audio}")
    require(data.startswith("data:;base64,"), f"local audio data must use data URI base64 prefix: {data[:32]}")
    require(base64.b64decode(data.split(",", 1)[1]) == sample_bytes, "local audio base64 payload changed")

    url_content = QwenOmniAudioJudgeProvider._audio_content(
        AssetRef(id="remote", type="audio", url="https://example.com/voice.wav")
    )
    require(
        url_content["input_audio"] == {"data": "https://example.com/voice.wav", "format": "wav"},
        f"remote audio URL payload mismatch: {url_content}",
    )
    content_parts = QwenOmniAudioJudgeProvider._content_parts(
        "请评价音色",
        [AssetRef(id="sample", type="audio", path=str(sample_path), metadata={"format": "mp3"})],
    )
    require(content_parts[0]["type"] == "input_audio", f"audio content should precede text prompt: {content_parts}")
    require(content_parts[-1] == {"type": "text", "text": "请评价音色"}, f"text prompt should be last: {content_parts}")

    print("qwen_omni_router_payload_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
