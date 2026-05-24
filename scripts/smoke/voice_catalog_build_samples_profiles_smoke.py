from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import build_parser  # noqa: E402
from autodrama.config import Settings  # noqa: E402
from autodrama.providers.local.mock.fake import FakeAudioJudgeProvider, FakeVoiceDesignProvider  # noqa: E402
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository  # noqa: E402
from autodrama.services.voice_catalog_service import VoiceCatalogService  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class CountingAudioJudgeProvider(FakeAudioJudgeProvider):
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.calls: list[str] = []
        self.delay_seconds = delay_seconds
        self.active_calls = 0
        self.max_active_calls = 0

    async def judge_audio_json(self, prompt, schema, *, refs, temperature=0.2, metadata=None):
        metadata = metadata or {}
        self.calls.append(str(metadata.get("voice_type") or ""))
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay_seconds > 0:
                await asyncio.sleep(self.delay_seconds)
            return await super().judge_audio_json(
                prompt,
                schema,
                refs=refs,
                temperature=temperature,
                metadata=metadata,
            )
        finally:
            self.active_calls -= 1


class ManySpeakerFakeVoiceDesignProvider(FakeVoiceDesignProvider):
    _SPEAKERS = [
        {
            **FakeVoiceDesignProvider._SPEAKERS[index % len(FakeVoiceDesignProvider._SPEAKERS)],
            "name": f"Fake Bulk {index:02d}",
            "voice_type": f"fake_bulk_voice_{index:02d}",
        }
        for index in range(35)
    ]


def assert_cli_parser() -> None:
    args = build_parser().parse_args(
        [
            "voice-catalog",
            "build",
            "--config",
            "config.yaml",
            "--provider",
            "fake",
            "--miss-profiles",
        ]
    )
    require(args.miss_profiles is True, "--miss-profiles should set args.miss_profiles")
    require(args.force_profiles is False, "--miss-profiles should not set args.force_profiles")


async def assert_profile_concurrency() -> None:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "voice_catalog_profile_concurrency" / "outputs"
    catalog_root = settings.output.root_dir.parent / ".assets" / "voice_catalog"
    if catalog_root.exists():
        shutil.rmtree(catalog_root)

    repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(repo)
    speech_provider = ManySpeakerFakeVoiceDesignProvider()
    manifest = service.load_or_bootstrap_manifest(speech_provider, force_bootstrap=True)
    voice_types = {voice.voice_type for voice in manifest.voices}
    manifest = await service.build_samples(
        speech_provider,
        manifest,
        force_samples=True,
        voice_types=voice_types,
    )
    judge = CountingAudioJudgeProvider(delay_seconds=0.01)
    manifest = await service.build_profiles(
        judge,
        manifest,
        force_profiles=True,
        voice_types=voice_types,
    )
    require(len(judge.calls) == len(voice_types), "force profile build should profile every bulk voice")
    require(
        judge.max_active_calls == service.PROFILE_BUILD_CONCURRENCY,
        f"profile build should run exactly {service.PROFILE_BUILD_CONCURRENCY} concurrent judge calls",
    )
    require(
        all(voice.omni_profile is not None for voice in manifest.voices),
        "all bulk voices should have generated profiles",
    )


async def main_async() -> int:
    assert_cli_parser()
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "voice_catalog_build_samples_profiles" / "outputs"
    catalog_root = settings.output.root_dir.parent / ".assets" / "voice_catalog"
    if catalog_root.exists():
        shutil.rmtree(catalog_root)

    repo = VoiceCatalogRepository.from_settings(settings)
    service = VoiceCatalogService(repo)
    speech_provider = FakeVoiceDesignProvider()
    judge = CountingAudioJudgeProvider()
    target_voice_types = {"fake_male_voice", "fake_female_voice"}
    manifest = service.load_or_bootstrap_manifest(speech_provider)
    manifest = await service.build_samples(
        speech_provider,
        manifest,
        force_samples=True,
        voice_types=target_voice_types,
    )
    manifest = await service.build_profiles(
        judge,
        manifest,
        force_profiles=True,
        voice_types={"fake_male_voice"},
    )
    require(judge.calls == ["fake_male_voice"], "force profile build should profile only the selected voice")
    manifest = await service.build_profiles(
        judge,
        manifest,
        force_profiles=False,
        voice_types=target_voice_types,
    )
    require(
        judge.calls == ["fake_male_voice", "fake_female_voice"],
        "missing profile build should skip existing profiles and generate only missing ones",
    )
    manifest = await service.build_profiles(
        judge,
        manifest,
        force_profiles=False,
        voice_types=target_voice_types,
    )
    require(
        judge.calls == ["fake_male_voice", "fake_female_voice"],
        "second missing profile build should not regenerate existing matching profiles",
    )
    male = repo.voice_by_type(manifest, "fake_male_voice")
    female = repo.voice_by_type(manifest, "fake_female_voice")

    require(male is not None, "fake_male_voice missing")
    require(female is not None, "fake_female_voice missing")
    require(set(male.samples) == set(manifest.sample_emotions), "male samples were not generated")
    require(set(female.samples) == set(manifest.sample_emotions), "female samples were not generated")
    require(male.omni_profile is not None, "male profile was not generated")
    require(female.omni_profile is not None, "female profile was not generated by missing profile build")
    require(male.profile_hash, "male profile hash missing")
    require(female.profile_hash, "female profile hash missing")
    require("fake 评测" in male.omni_profile.summary, "male profile summary should use natural sketch text")
    for sample in male.samples.values():
        path = repo.root / sample.asset_path
        require(path.exists() and path.stat().st_size > 0, f"sample file missing: {path}")
    for sample in female.samples.values():
        path = repo.root / sample.asset_path
        require(path.exists() and path.stat().st_size > 0, f"sample file missing: {path}")
    require(
        repo.sample_manifest_path(manifest.provider, manifest.model, "fake_male_voice").exists(),
        "per-voice sample manifest missing",
    )
    profile_path = repo.voice_profile_path(manifest.provider, manifest.model, "fake_male_voice")
    require(profile_path.exists(), "per-voice profile json missing")
    require("omni_profile" in profile_path.read_text(encoding="utf-8"), "per-voice profile json missing profile payload")
    female_profile_path = repo.voice_profile_path(manifest.provider, manifest.model, "fake_female_voice")
    require(female_profile_path.exists(), "female per-voice profile json missing")
    await assert_profile_concurrency()

    print("voice_catalog_build_samples_profiles_smoke=ok")
    print(f"manifest_path={repo.manifest_path(manifest.provider, manifest.model)}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
