# 主动外呼安全 Outbox 闭环验收

日期：2026-05-20

## 结论

主动外呼的 host 侧闭环已接通：`DigitalLifeSupervisor` 会注册并触发 `proactive_talk_periodic`，主动话语不会直接发往真实通讯软件，而是先写入安全 outbox，等待通讯软件 gateway 后续接管真实送达。

这次验收通过的是“主动意图 -> 调度 -> 主动话语生成 -> 安全 outbox 排队 -> 观测接口识别”的闭环；通讯软件 gateway、ASR、VTS 的真实外设送达仍属于后续验收项。

## 验收样本

运行命令：

```powershell
python tools\run_digital_life_host_runtime.py --mode fixture --out-dir artifacts\codex_runs\proactive-outbox-acceptance-20260520\run --run-id proactive-outbox-acceptance-20260520 --ticks 1
```

关键结果：

- `status`: `PASS`
- `scheduler_status`: `HEALTHY`
- `core_ticks_registered`: `true`
- `proactive_task_status`: `OK`
- `proactive_outbox_queued_count`: `1`
- outbox 文件：`artifacts/codex_runs/proactive-outbox-acceptance-20260520/run/state/proactive_outbox.jsonl`

outbox 样本证明：

- `delivery_channel`: `safe_outbox`
- `delivery_status`: `QUEUED`
- `message.type`: `proactive_talk`
- `message.content`: 已生成主动问候
- `persona_self_id`: `natsume_ai`

## 代码变更

- `host_brain/runtime/digital_life_supervisor.py`
  - 将 `proactive_talk_periodic` 纳入默认 idle tick。
  - 注册主动外呼调度任务。
  - 新增 `proactive_outbox.jsonl` 安全排队。
  - snapshot 暴露 `proactive_outbox` 观测字段。

- `host_brain/app/http_hooks_runtime_helpers.py`
  - `run_proactive_intent_dispatch()` 支持传入 LLM、最近交互、动机和渲染策略 override。
  - supervisor 验收时不依赖本地 `18080` LLM 服务，使用模板降级生成主动话语。

- `host_brain/app/obs_routes_digital_life_soak.py`
  - 24h soak 观测可识别 safe outbox 证据。
  - 有 outbox 排队时，主动外呼层显示 `PASS`。
  - 无 outbox 证据时仍显示 `NOT_COVERED`，避免把未验收当成功。

- `tools/run_digital_life_host_runtime.py`
- `tools/run_digital_life_wall_clock_soak.py`
  - summary/report 增加 `proactive_task_status` 与 `proactive_outbox_queued_count`。

## 回归

```powershell
python -m pytest -q tests\unit\test_digital_life_supervisor.py tests\unit\test_obs_digital_life_soak_route.py tests\unit\test_review_digital_life_wall_clock_soak.py
```

结果：`13 passed`

```powershell
npm run build
```

结果：通过。

## 剩余边界

- 真实通讯软件 gateway 尚未接管 `safe_outbox`。
- ASR/VTS 不在本次主动 outbox 验收范围。
- 下一步应做 gateway consumer：读取 outbox，执行真实发送或 dry-run，写回 delivery receipt。
