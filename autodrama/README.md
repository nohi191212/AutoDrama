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
17. `storyboard_generation`
18. `shot_dialogue_audio_generation`
19. `ref_frame_generation`
20. `shot_video_generation`
21. `dynamic_asset_solidification`

The pre-generation pipeline now covers static assets and dynamic shot-level assets. It stores per-episode storyboard JSON files under `slots/`, then writes generated dialogue audio, reference frames, shot videos, and solidified dynamic asset metadata back into those slot files. It does not generate final edit plans or composed final videos yet.

## Local commands

Use the requested conda environment:

Do not use pytest in this repository. Use focused smoke scripts and compile checks instead.

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config ../config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
```

For ad-hoc CLI usage without installing:

```powershell
$env:PYTHONPATH="src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config ../config.yaml.example --title "30秒逆袭短片" --script-file ../input/story.txt
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config ../config.yaml.example --project <project_id> --provider fake
```
