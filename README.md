# AutoDrama

AutoDrama 将剧本拆成可生成的 shot，并为每个 shot 生成可审计的背景、关键帧和视频输入。

默认预生成镜头链路为：

```text
clip_segment
→ clip_to_shots
→ layout_to_background_prompt
→ shot_background_image_generation
→ shot_keyframe_prompt
→ shot_keyframe_image_generation
→ shot_manifest_generation
→ shot_video_generation
```

每个 shot 都有一个唯一的 `narrative_angle`。场景母版图用于规划可复用的无人背景；关键帧再以该背景为图 1，按角色、道具顺序使用后续参考图。无人 shot 也会生成独立的关键帧文件，不与背景图共用路径。

所有实际发送给模型的提示词会在调用边界原样保存到：

```text
logs/prompts/<asset_type>/<asset_name>.prompt.txt
logs/prompts/<asset_type>/history/<asset_name>.attempt-XX.prompt.txt
```

模板位于 `autodrama/src/autodrama/prompts/<node>/`。`default.md` 描述内容任务；可选的 `render.py` 只做确定性的最终提示词组装。

## 运行

使用仓库指定的 Python：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id>
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id>
```

局部重跑背景、关键帧或 manifest：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only shot_keyframe_image_generation --episodes 1 --shots 1-3 --force
```

这是一项破坏性重构：旧 storyboard、旧关键帧和旧 manifest 产物不能复用。受影响项目必须从 `clip_to_shots` 或更早节点重新生成。

仓库不使用 pytest。可运行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_pipeline_fake_e2e_smoke.py
```
