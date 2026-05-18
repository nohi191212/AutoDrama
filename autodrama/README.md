# AutoDrama

This is the first implementation slice for AutoDrama.

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

`run pregen` covers script, reusable static assets, and BGM.

Dynamic shot-level assets now live in a separate workflow:

1. `storyboard_generation`
2. `shot_dialogue_audio_generation`
3. `ref_frame_generation`
4. `shot_video_generation`
5. `dynamic_asset_solidification`

`run generation` starts by writing storyboard slots to `slots/{episode_key}.json`, then writes dialogue audio, reference frames, shot videos, and solidified dynamic asset metadata back into those slots. It reads `generation_checklist.json` when present and supports `--episodes` to target specific episodes.

## Local commands

Use the requested conda environment:

Do not use pytest in this repository. Use focused smoke scripts and compile checks instead.

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
```

For ad-hoc CLI usage without installing:

```powershell
$env:PYTHONPATH="src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config ../config.yaml.example --title "30秒逆袭短片" --script-file ../input/story.txt
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config ../config.yaml.example --project <project_id> --provider fake
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config ../config.yaml.example --project <project_id> --only role_voice_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config ../config.yaml.example --project <project_id> --provider fake
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config ../config.yaml.example --project <project_id> --only ref_frame_generation --episodes 1,3
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
