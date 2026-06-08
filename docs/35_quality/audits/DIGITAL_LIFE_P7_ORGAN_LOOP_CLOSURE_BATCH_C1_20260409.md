受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C1_20260409

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C1_20260409

## 目标
- 把 `Immune` 决策并入 trace 可观测链路，避免在 WebUI 中被当成普通 edge 噪音。

## 本批完成项
- `obs_routes_core` 增强 voice 事件类型映射：
  - `IMMUNE_*` → `node_type=immune`、`display_name=Immune Gate`
  - 该节点 `side=host`（区别于 edge 侧 ASR/TTS/VTS）
- `obs_routes_core` 增强 reason_code 提取：
  - 当 `voice_event_log.reason_code` 为空时，若是 immune 节点则回退读取 `payload.reason_codes[0]`。
- 文件：
  - `host_brain/app/obs_routes_core.py`

## 测试
- 新增断言：
  - `tests/unit/test_obs_routes_trace_status.py`
    - `test_voice_event_node_type_recognizes_immune_gate`
    - `test_build_voice_trace_nodes_maps_immune_reason_codes`
- 结果：
  - 相关聚焦测试与回归测试全部通过。

## 收益
- Trace 视图中可直接看出 immune 门禁节点，不再与普通 voice/edge 事件混淆。
- 命中原因在节点层直接可见，排障链路更短。
