# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE — Batch C7（2026-04-09）

## 目标

在 C6（scheduler immune proactive gate）基础上，补齐跨观测链路一致性闭环：

- `voice_event_log` 的 immune 事件可在 `/obs/trace/{request_id}` 看到 immune 节点；
- scheduler 抑制态（`IMMUNE_PROACTIVE_SUPPRESSED`）在 `/obs/scheduler/history` 可见且带完整 immune 元信息；
- 监控前端（Automation）可直接查看任务级 `last_immune_*` 字段。

---

## 代码变更

### 1) `/obs/scheduler/history` 增强 immune 字段透出

文件：`host_brain/app/obs_routes_stats.py`

- 扩展 `SchedulerTaskItem`：
  - `last_immune_decision`
  - `last_immune_risk_level`
  - `last_immune_reason_codes`
- 在 `get_scheduler_history()` 的任务归一化阶段，补充并规范化上述字段（大小写、长度、列表兼容）。

### 2) 新增跨链路集成测试（trace + scheduler history）

文件：`tests/integration/test_obs_immune_trace_scheduler_e2e.py`

- 构造 `voice_event_log`：
  - `INPUT_ACCEPTED`
  - `IMMUNE_CHAT_GATE`
  - `INPUT_COMPLETED`
- 验证 `/obs/trace/req-immune-e2e-001`：
  - 存在 `node_type=immune` 节点；
  - `reason_code` 映射为 `PROMPT_INJECTION_DETECTED`。
- 验证 `/obs/scheduler/history`：
  - `IMMUNE_PROACTIVE_SUPPRESSED` 进入 gate blocked 统计；
  - 任务项透出 `last_immune_decision/risk_level/reason_codes`。

### 3) 监控页补齐 immune 可见性

文件：`host_brain/app/monitor_ui/src/pages/Automation.jsx`

- `TaskCard` 的告警条追加 immune 上下文：
  - `immune=<decision>/<risk>`
  - `immune_reason=<codes>`
- 历史表新增列：
  - `Immune`
  - `Immune Reason`

---

## 回归结果

执行：

```bash
pytest -q \
  tests/integration/test_obs_immune_trace_scheduler_e2e.py \
  tests/integration/test_obs_scheduler_history_stats.py \
  tests/unit/test_obs_routes_trace_status.py \
  tests/unit/test_scheduler_engine.py \
  tests/unit/test_scheduler_routes_immune_gate_state.py \
  tests/unit/test_scheduler_emotion_gate.py
```

结果：`41 passed`

---

## 收口说明

- C7 完成后，immune 风险信号已打通：
  - **审计事件层**（`voice_event_log`）  
  - **trace 展示层**（`/obs/trace` immune node）  
  - **scheduler 历史层**（`/obs/scheduler/history` + `last_immune_*`）  
  - **前端可观测层**（Automation 任务卡片与历史表）

- 本批次未改动任务调度决策逻辑本身，仅补齐“可观察一致性”。
