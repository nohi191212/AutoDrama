from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import OutputSettings, ProviderSettings, RuntimeSettings, Settings
from autodrama.core.schemas import Role
from autodrama.core.voice_catalog import RoleVoiceSelectionItem
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.workflows.nodes.role_subject_nodes import RoleKlingVoiceGenerationNode
import autodrama.providers.kling.video.omni as kling_module
import autodrama.services.voice_catalog_service as catalog_module


PRESET_BODY = {
    "code": 0,
    "data": [
        {
            "task_result": {
                "voices": [
                    {
                        "voice_id": "voice-girl",
                        "voice_name": "温柔少女",
                        "trial_url": "https://trials.example/voice-girl.mp3",
                        "owned_by": "kling",
                    }
                ]
            }
        },
        {
            "task_result": {
                "voices": [
                    {
                        "voice_id": "voice-man",
                        "voice_name": "沉稳大叔",
                        "trial_url": "https://trials.example/voice-man.mp3",
                        "owned_by": "kling",
                    }
                ]
            }
        },
    ],
}


class FakeAsyncClient:
    fail_preset = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        request = httpx.Request("GET", str(url))
        if "presets-voices" in str(url):
            if self.fail_preset:
                raise httpx.ConnectError("simulated gateway failure", request=request)
            return httpx.Response(200, json=PRESET_BODY, request=request)
        if "trials.example" in str(url):
            return httpx.Response(
                200,
                content=b"ID3-official-trial-audio",
                headers={"content-type": "audio/mpeg"},
                request=request,
            )
        if "tts.example" in str(url):
            return httpx.Response(
                200,
                content=b"ID3-synthesized-voice-audio",
                headers={"content-type": "audio/mpeg"},
                request=request,
            )
        raise AssertionError(f"unexpected URL: {url}")

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", str(url))
        if str(url).endswith("/v1/audio/tts"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "tts-task",
                        "task_status": "succeed",
                        "task_result": {
                            "audios": [
                                {
                                    "id": "audio-id",
                                    "url": "https://tts.example/result.mp3",
                                    "duration": "8.0",
                                }
                            ]
                        },
                    },
                },
                request=request,
            )
        raise AssertionError(f"unexpected POST URL: {url}")


async def main() -> None:
    original_provider_client = kling_module.httpx.AsyncClient
    original_catalog_client = catalog_module.httpx.AsyncClient
    kling_module.httpx.AsyncClient = FakeAsyncClient
    catalog_module.httpx.AsyncClient = FakeAsyncClient
    try:
        provider = KlingOmniVideoProvider(
            ProviderSettings(
                api_key_env="KLING_API_KEY",
                models={"video": "kling-v3-omni"},
                options={"api_schema": "official_v3"},
                api_keys={"KLING_API_KEY": "smoke-only-key"},
            ),
            RuntimeSettings(),
        )
        speakers = await provider.list_preset_voices()
        assert [item["voice_type"] for item in speakers] == ["voice-girl", "voice-man"]
        assert speakers[0]["trial_url"].endswith("voice-girl.mp3")
        tts_result = await provider.synthesize_speech(
            voice="avatar-tts-voice",
            text="这是一段用于验证接口契约的试听文本。",
        )
        assert tts_result.audio_format == "mp3"
        assert tts_result.raw_response["tts_audio"]["duration"] == "8.0"

        run_root = ROOT / ".tmp" / f"kling-official-voice-catalog-{uuid.uuid4().hex}"
        settings = Settings(output=OutputSettings(root_dir=run_root / "outputs"))
        repo = VoiceCatalogRepository.from_settings(settings)
        service = VoiceCatalogService(repo)
        manifest = await service.sync_official_preset_catalog(provider)
        assert len(manifest.voices) == 2
        for voice in manifest.voices:
            sample = voice.samples["normal"]
            sample_path = repo.root / sample.asset_path
            assert sample_path.exists()
            assert sample_path.read_bytes().startswith(b"ID3")
            assert repo.sample_manifest_path(manifest.provider, manifest.model, voice.voice_type).exists()

        girl = Role(id="role_girl", name="小晞", intro="年轻女性，温柔少女，女主角")
        man = Role(id="role_man", name="九叔", intro="成熟男性，沉稳大叔，父亲")
        girl_pool, _ = service.role_voice_select_candidate_pool(girl, manifest)
        man_pool, _ = service.role_voice_select_candidate_pool(man, manifest)
        assert girl_pool[0].voice_type == "voice-girl"
        assert man_pool[0].voice_type == "voice-man"

        selection = RoleVoiceSelectionItem(
            role_id=girl.id,
            role_name=girl.name,
            selected_voice_label="温柔少女",
            selected_voice_type="voice-girl",
            selected_voice_resource_id="voice-girl",
            selected_voice_model_family="kling-3.0-omni",
            selected_voice_catalog_key="kling_omni:kling-v3-omni:voice-girl",
            selected_reason="角色画像匹配",
            selection_source="catalog_heuristic",
            role_profile_hash="role-hash",
            catalog_version=manifest.catalog_version,
            catalog_hash=repo.manifest_hash(manifest),
            provider=manifest.provider,
            model=manifest.model,
        )
        RoleKlingVoiceGenerationNode._bind_catalog_selection(
            girl,
            selection,
            trial_url="https://trials.example/voice-girl.mp3",
            owned_by="kling",
        )
        assert girl.kling_voice_id == "voice-girl"
        assert girl.kling_voice_source == "preset"
        assert girl.kling_voice_raw_response["voice_selection"]["selection_source"] == "catalog_heuristic"

        provider.settings.options["role_voice_map"] = {girl.id: "manual-voice"}
        assert RoleKlingVoiceGenerationNode._voice_spec(provider, girl)["voice_id"] == "manual-voice"

        FakeAsyncClient.fail_preset = True
        cached = await service.sync_official_preset_catalog(
            provider,
            refresh_manifest=True,
            download_trials=False,
        )
        assert [voice.voice_type for voice in cached.voices] == ["voice-girl", "voice-man"]
    finally:
        kling_module.httpx.AsyncClient = original_provider_client
        catalog_module.httpx.AsyncClient = original_catalog_client

    print("kling official voice catalog smoke: ok")


if __name__ == "__main__":
    asyncio.run(main())
