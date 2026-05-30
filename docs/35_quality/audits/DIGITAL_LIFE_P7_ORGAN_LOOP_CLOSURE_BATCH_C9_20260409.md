受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE — Batch C9（2026-04-09）

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE — Batch C9（2026-04-09）

## 目标

补齐「误拦截回放工具链」：支持按 `request_id` 回放 immune 决策链，查看文本上下文与规则命中详情。

---

## 本批完成

### 1) 后端回放端点

文件：`host_brain/app/obs_routes_stats.py`

- 新增 `GET /obs/immune-gate/replay/{request_id}`。
- 返回结构：
  - `decisions`：按时间排序的 immune 决策事件（`decision/risk/score/reason_codes/matched_rules/stage/detail`）
  - `summary`：`first_decision/last_decision/max_score/reason_codes/matched_rules`
  - `input_preview`、`session_id`
- 无数据时返回 `404`：
  - `reason_code=IMMUNE_REPLAY_NOT_FOUND`

### 2) immune 事件提取增强

文件：`host_brain/app/obs_routes_stats.py`

- 复用现有 `_extract_immune_gate_event_fields`，扩展提取字段：
  - `matched_rules`
  - `input_preview`
  - `stage / direction / phase`
- 保持旧统计接口兼容（`/obs/immune-gate/stats` 无破坏性变更）。

### 3) 前端最小可用回放入口

文件：
- `host_brain/app/monitor_ui/src/api/obs.js`
- `host_brain/app/monitor_ui/src/pages/Automation.jsx`

- 新增 API：
  - `fetchImmuneGateReplay(requestId, { limit })`
- `Immune Gate Ops` 面板新增：
  - `request_id` 输入 + `Replay` 按钮
  - Recent Events 每行一键 `Replay`
  - 回放结果区：摘要、命中 reason/rule 标签、逐事件决策流
  - 一键跳转 `#/trace/{request_id}`

---

## 验证记录

### 测试

新增：`tests/integration/test_obs_immune_gate_replay.py`

- 校验回放链返回决策顺序与规则命中细节。
- 校验未知 request 的 404 错误码。

回归执行：

```bash
pytest -q \
  tests/integration/test_obs_immune_gate_replay.py \
  tests/integration/test_obs_immune_gate_stats.py \
  tests/integration/test_obs_immune_trace_scheduler_e2e.py \
  tests/integration/test_obs_scheduler_history_stats.py \
  tests/unit/test_obs_routes_trace_status.py
```

结果：`18 passed`

### 前端构建

```bash
cd host_brain/app/monitor_ui
npm run build -- --outDir .tmp-build
```

结果：通过（已清理临时目录）。

---

## 收口结论

C9 后，immune 从“统计可见”升级为“可回放可追责”：

- 可按 `request_id` 回看完整免疫决策链；
- 可直接看到误拦截相关 `reason_codes / matched_rules`；
- 可一键跳转 Trace 做跨链路核对。
