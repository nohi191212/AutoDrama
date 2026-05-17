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

The pre-generation pipeline stops after storyboard generation. It generates static role/prop/layout/BGM assets and stores per-episode storyboard JSON files under `slots/`. It does not generate reference frames, shot videos, or final edits yet.

## Local commands

Use the requested conda environment:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m pytest
```

For ad-hoc CLI usage without installing:

```powershell
$env:PYTHONPATH="src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config ../config.yaml.example --title "30秒逆袭短片" --script-file ../input/story.txt
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config ../config.yaml.example --project <project_id> --provider fake
```
