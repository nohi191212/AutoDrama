# 预生成节点

默认预生成链路在 `shot_manifest_generation` 结束：

1. `script_import`
2. `script_detail_expand`
3. `script_novel_extract`
4. `key_vision_prompt`
5. `key_vision_image_generation`
6. 角色、道具和场景静态资产节点
7. `clip_segment`
8. `clip_to_shots`
9. `layout_to_background_prompt`
10. `shot_background_image_generation`
11. `shot_keyframe_prompt`
12. `shot_keyframe_image_generation`
13. `shot_manifest_generation`

镜头链路合同：每个 shot 必须有唯一、非空的 `narrative_angle`，并且只映射到一个背景。背景可被兼容 shot 复用；背景图与关键帧图分别写入 `assets/images/shot_backgrounds/` 和 `assets/images/shot_keyframes/`。

`--episodes` 可用于单节点重跑。`--shots` 仅可用于背景、关键帧和 manifest 节点；选择共享背景的任一 shot 都会解析该背景。强制重建背景会使其关联关键帧和 manifest 失效。

示例：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only layout_to_background_prompt --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_background_image_generation --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_keyframe_image_generation --episodes 1 --shots 1-3 --force
```
