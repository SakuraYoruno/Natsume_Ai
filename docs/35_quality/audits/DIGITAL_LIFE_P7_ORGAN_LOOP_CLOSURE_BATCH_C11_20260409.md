# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE — Batch C11（2026-04-09）

## 目标

补齐 immune replay 的跨页面深链，减少人工切页与输入 request_id 的摩擦成本。

---

## 本批完成

### 1) Automation 支持 replay 深链参数

文件：`host_brain/app/monitor_ui/src/pages/Automation.jsx`

- 新增 query 参数读取：
  - `immune_replay_request_id`
- `ImmuneGateOpsPanel` 新增 `initialReplayRequestId`：
  - 首次进入页面自动填充 request_id 并触发回放。

### 2) TraceRequestDetail 一键跳转 Immune Ops

文件：`host_brain/app/monitor_ui/src/pages/TraceRequestDetail.jsx`

- 在请求详情顶部加入按钮：
  - `打开 Immune Ops`
  - 跳转目标：`#/automation?immune_replay_request_id=<request_id>`

---

## 验证记录

构建验证：

```bash
cd host_brain/app/monitor_ui
npm run build -- --outDir .tmp-build
```

结果：通过（已清理临时目录）。

后端回归复验：

```bash
pytest -q \
  tests/integration/test_obs_immune_gate_replay.py \
  tests/integration/test_obs_immune_gate_stats.py \
  tests/integration/test_obs_immune_trace_scheduler_e2e.py \
  tests/integration/test_obs_scheduler_history_stats.py \
  tests/unit/test_obs_routes_trace_status.py
```

结果：`18 passed`

---

## 收口结论

C11 后，immune 排查路径已形成稳定的「详情页 → Ops 回放页」深链：

- 不再需要手工复制 request_id；
- 进入 Automation 后可直接看到同请求 replay；
- 人工排障步骤进一步压缩。
