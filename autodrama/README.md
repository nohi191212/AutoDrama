# AutoDrama

This is the first implementation slice for AutoDrama.

Implemented scope:

1. `script_outline`
2. `script_detail`
3. `script_polish`
4. `role_design`
5. `role_voice_design`
6. `role_voice_generation`

The pipeline stops after role voice generation. It does not generate images, video, BGM, reference frames, or edits.

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
