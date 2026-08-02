受众：PROJECT
标题：VTS 热键配置指南

# VTS 热键配置指南

`configs/vts/vts_hotkey_config.yaml` 描述 Edge 可解析的 VTube Studio hotkey 与表达映射。配置是设备能力目录，不是 Host 的直接动作入口。

## 结构

```yaml
meta:
  model_name: "..."
  config_version: "..."
defaults:
  trigger_delay_ms: 0
  priority: 0
  fallback_tag: "NEUTRAL"
emotion_mapping:
  HAPPY: "exp_happy_01"
hotkey_groups:
  - group: "表情"
    hotkeys:
      - name: "开心"
        hotkey_id: "..."
        emotion_tag: "HAPPY"
```

`hotkey_id` 必须与目标 VTS 实例完全一致。emotion tag 只用于 Edge 侧能力映射；实际动作仍由 `ExpressionPlan` 选择，并经 Host outbox、Edge session/fence 和 physical receipt。

## 修改流程

1. 在目标 Edge 的 VTS 中确认模型、hotkey id 和动作效果。
2. 只修改非密钥 YAML，不提交本机 token 或 WebSocket credential。
3. 运行 YAML 与 mapping contract 测试。
4. 用户允许后，在 Edge 单独触发每个候选动作。
5. 再验证 Voice／Avatar 同一 `synchronization_id` 和启动偏差。

## 降级

未知 tag 使用明确 fallback，但 fallback 不能反向把七海 self affect 改成 `NEUTRAL`。VTS 不可用时保留文本和可用 Voice，并在 Monitor 显示 `DEGRADED` 或 `NOT_TESTED`。

详细排障见 [VTS/Voice 排障](VTS_VOICE_TROUBLESHOOTING.md)。
