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

`run pregen` covers script, reusable static assets, BGM, and storyboard slots. After storyboard generation it writes `generation_checklist.json` in the project directory. Edit each episode's `generate` boolean to decide which episodes the next dynamic generation run should process.

Dynamic shot-level assets now live in a separate workflow:

1. `shot_dialogue_audio_generation`
2. `ref_frame_generation`
3. `shot_video_generation`
4. `dynamic_asset_solidification`

`run generation` reads `generation_checklist.json`, writes dialogue audio, reference frames, shot videos, and solidified dynamic asset metadata back into `slots/{episode_key}.json`, then resets successfully processed episodes to `"generate": false`. It does not generate final edit plans or composed final videos yet.

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
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config ../config.yaml.example --project <project_id> --provider fake
```
