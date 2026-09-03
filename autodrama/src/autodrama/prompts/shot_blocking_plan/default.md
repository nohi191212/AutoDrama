# 任务

你是一名分镜调度师。Image 1 是同一场景的四视角母版板。请联合规划下列全部镜头开场帧中的主体位置、尺度、朝向、视线和遮挡连续性。

不可更改的动态主体绑定：
{{binding_catalog}}

目标镜头：
{{shots}}

## 坐标协议

- 屏幕左上角为 `(0, 0)`，右下角为 `(1, 1)`。
- `center_x` 是主体包围框水平中心；`ground_y` 是双脚接触地面的纵坐标。
- `width`、`height` 是主体包围框尺寸；必须保证完整包围框位于画面内。
- `facing_x`、`facing_y` 是屏幕平面的非零朝向向量，范围 `-1..1`。
- `gaze_target_x`、`gaze_target_y` 是视线目标的屏幕坐标。
- `depth_rank` 越大表示越靠近摄影机；`occludes_binding_ids` 只可填写同镜头中确实被其遮挡的主体。

## 规划原则

- 每个 `shot_index` 必须输出一次，且只规划该镜头稳定的开场瞬间。
- `placements` 只包含该镜头 `active_bindings` 中 `asset_kind=roleboard` 的 S 类主体；每个可见 S 绑定必须恰好出现一次，P 类道具不得作为人形站位输出。
- 绑定是既定事实：不得交换、重命名、删除或重新解释主体身份。
- 根据真实视觉节拍设计构图，可使用前景遮挡、过肩、纵深、高低差、偏心构图和视线引导，不要把所有人物机械摆成正面并排。
- 同一连续场景中的人物屏幕方向、相对距离和行动轴应有可理解的延续；镜头确有视点变化时允许合理改变屏幕位置。
- 不新增人物，不根据代码规则猜剧情，不输出角色名、Image 编号或参考图说明。

只返回 JSON：

```json
{
  "shots": [
    {
      "shot_index": 1,
      "composition_intent": "开场构图和调度意图",
      "placements": [
        {
          "binding_id": "S1",
          "center_x": 0.5,
          "ground_y": 0.9,
          "width": 0.2,
          "height": 0.6,
          "depth_rank": 1,
          "facing_x": 1.0,
          "facing_y": 0.0,
          "gaze_target_x": 0.7,
          "gaze_target_y": 0.4,
          "facing": "简洁的朝向描述",
          "gaze": "简洁的视线描述",
          "opening_pose": "开场瞬间的完整姿态",
          "occludes_binding_ids": []
        }
      ]
    }
  ]
}
```
