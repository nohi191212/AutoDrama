# AutoDrama

AutoDrama builds short-drama projects in two stages: pre-generation for script/static assets/BGM, then generation for per-episode dynamic shot assets.

Implemented scope:

1. `script_outline`
2. `script_detail`
3. `script_polish`
4. `role_design`
5. `role_voice_design`
6. `role_voice_generation`
7. `role_appearance_design`
8. `role_appearance_generation`
9. `prop_design`
10. `prop_image_generation`
11. `script_compress`
12. `layout_design`
13. `layout_dedupe_review`
14. `layout_image_generation`
15. `bgm_design`
16. `bgm_generation`

`run pregen` covers script, reusable static assets, BGM design, and BGM audio generation. It stops at `bgm_generation` by default.

Dynamic shot-level assets now live in a separate workflow:

1. `storyboard_generation`
2. `shot_dialogue_audio_generation`
3. `ref_frame_generation`
4. `shot_video_generation`
5. `dynamic_asset_solidification`

`run generation` processes selected episodes in episode order. For each episode it writes the storyboard slot to `slots/{episode_key}.json`, generates dialogue audio, reference frames, shot videos, and solidified dynamic asset metadata back into that slot before moving to the next episode. Completed storyboard summaries are stored in `assets/json/storyboard_history.json` and injected into later storyboard prompts so following episodes can preserve continuity. It reads `generation_checklist.json` when present and supports `--episodes` to target specific episodes.

## Provider routing

The default production routing in `config.yaml` is:

- `text.bgm_plan: aliyun` for `bgm_design`.
- `music.bgm: minimax` for `bgm_generation` with MiniMax `music-2.6`.
- `audio.speech: volcengine` for role and shot dialogue TTS.
- `image.ref_frame: rightcode` for reference frames.
- `video.shot: volcengine` for shot videos.

Volcengine TTS should keep `instruction_mode: none` unless a provider-level instruction carrier is verified. This prevents instruction-prefix text from being synthesized as speech.

MiniMax music generation uses its own long timeout option, `providers.minimax.options.music_timeout_seconds`, because BGM generation can take longer than normal text or image calls.

## Local commands

Use the requested conda environment:

Do not use pytest in this repository. Use focused smoke scripts and compile checks instead.

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama scripts/smoke/dynamic_assets_fake_smoke.py scripts/smoke/only_node_episode_smoke.py scripts/smoke/episode_serial_generation_smoke.py scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
```

For ad-hoc CLI usage from the repository root without installing:

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config config.yaml.example --title "30秒逆袭短片" --script-file input/story.txt
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml.example --project <project_id> --provider fake
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml.example --project <project_id> --only role_voice_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml.example --project <project_id> --provider fake
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml.example --project <project_id> --episodes 1,3
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml.example --project <project_id> --only storyboard_generation --episodes 1
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml.example --project <project_id> --only ref_frame_generation --episodes 1,3
```

Windows shortcut:

```powershell
run\start.cmd --config config.yaml --project <project_id>
run\start.cmd --config config.yaml --project <project_id> --only role_voice_generation
run\start.cmd --generation --config config.yaml --project <project_id> --only storyboard_generation --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id>
run\start.cmd --generation --config config.yaml --project <project_id> --episodes episode_001,episode_003
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_dialogue_audio_generation --episodes 1
```

`--episodes` accepts comma lists, ranges, and episode keys, for example `1,3`, `1-3`, and `episode_001,episode_003`. It is only used by the generation workflow.

`--only` can target one node in either workflow. Common examples:

```powershell
run\start.cmd --config config.yaml --project <project_id> --only bgm_generation
run\start.cmd --generation --config config.yaml --project <project_id> --only storyboard_generation --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_dialogue_audio_generation --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1-2
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes episode_001
```
