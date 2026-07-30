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

每个 shot 都有一个唯一的 `narrative_angle`。场景母版图用于规划可复用的无人背景；关键帧再以该背景为图 1，按角色、道具顺序使用后续参考图。无人 shot 也会生成独立的关键帧文件，不与背景图共用路径。视频生成只提交这张剧情关键帧和该 shot 涉及人物的角色身份板；关键帧是普通参考图，不强制作为首帧，也不要求尾帧。

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

用一分钟样片控制昂贵节点的成本：

```yaml
generation:
  expected_output_seconds: 60
```

`clip_segment` 和 `clip_to_shots` 仍会完整规划整集。工作流在所有 shot 时长完成归一化后，按 clip 顺序选择累计时长首次达到目标的最小完整前缀；背景、关键帧、视频和 Postgen 只处理该前缀，不截断 clip。`-1` 表示完整生成。显式 `--shots` 的人工选择优先于自动前缀。选择结果写入 `assets/json/expected_output_selection.json`，便于逐节点核对。

后续把目标从 60 秒调大或改成 `-1` 时，重新运行 Pregen 即会只补新增前缀的背景、关键帧和 manifest；已有前缀资产不会因范围扩大而被替换。Generation checklist 会自动重新打开对应集，随后可继续运行 Generation 和 Postgen，无需 `--force`。

这是一项破坏性重构：旧 storyboard、旧关键帧和旧 manifest 产物不能复用。受影响项目必须从 `clip_to_shots` 或更早节点重新生成。

仓库不使用 pytest。可运行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_pipeline_fake_e2e_smoke.py
```
