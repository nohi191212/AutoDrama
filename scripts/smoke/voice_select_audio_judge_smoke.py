from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository  # noqa: E402
from autodrama.services.voice_catalog_service import VoiceCatalogService  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_audio_judge"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    router = ProviderRouter(settings, provider_override="fake")
    catalog_repo = VoiceCatalogRepository.from_settings(settings)
    catalog_service = VoiceCatalogService(catalog_repo)
    speech_provider = router.audio("speech")
    judge = router.judge("voice_select")
    manifest = catalog_service.load_or_bootstrap_manifest(speech_provider, force_bootstrap=True)
    manifest = await catalog_service.build_samples(speech_provider, manifest, force_samples=True)
    await catalog_service.build_profiles(judge, manifest, force_profiles=True)

    workflow = PregenWorkflow(repo=repo, router=router)
    state = ProjectState(
        project_id="voice_select_audio_judge_smoke",
        title="Voice Select Audio Judge Smoke",
        raw_script="林舟发现合同异常。",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_linz": Role(
                id="role_linz",
                name="林舟",
                intro="二十八岁男性职场青年，冷静克制。",
                personality="谨慎、隐忍",
                episode_keys=["episode_001"],
                audio={
                    "normal": RoleAudio(
                        id="role_linz_audio_normal",
                        role_id="role_linz",
                        emotion="normal",
                        desc="青年男性声音，克制清晰。",
                        sample_text="我是林舟，我会保持冷静。",
                    )
                },
            )
        },
    )

    state = await workflow._voice_node_runner("voice_select").run(project_dir, state)
    output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    item = output["selected_voices"][0]
    require(item["selection_source"] == "omni_judge", f"voice_select did not use audio judge: {item}")
    require(item["selected_voice_type"] == "fake_male_voice", "unexpected selected voice_type")
    require("audio_judge" in item["raw_response"], "audio judge raw response missing")
    require(state.roles["role_linz"].voice_type == "fake_male_voice", "selected voice not bound to state role")

    print("voice_select_audio_judge_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
