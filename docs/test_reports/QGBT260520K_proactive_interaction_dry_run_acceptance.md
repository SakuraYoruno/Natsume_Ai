# 主动交互 Dry-Run 闭环验收

日期：2026-05-20

## 结论

主动交互 dry-run 闭环已通过：系统可以从主动外呼 outbox 取出消息，生成 dry-run 送达回执，再把模拟用户回复交给 Thalamus。Thalamus 会把这条入站消息识别为主动会话续接，并路由回聊天主链。

这次证明的是“主动外呼 -> dry-run 送达 -> 用户回复 -> Thalamus 主动续接识别 -> 主链处理”的协议闭环。真实通讯软件 gateway 的实际发送和真实用户回复接入仍是下一步。

## 验收样本

运行命令：

```powershell
python tools\validate_proactive_interaction_loop.py --out-dir artifacts\codex_runs\proactive-interaction-dry-run-20260520\run --run-id proactive-interaction-dry-run-20260520
```

关键结果：

- `status`: `PASS`
- `proactive_outbox_queued_count`: `1`
- `delivery_receipt_count`: `1`
- `interaction_count`: `1`
- `delivery_status`: `DELIVERED_DRY_RUN`
- `gateway_mode`: `dry_run`
- `thalamus_route`: `chat`
- `thalamus_reason_code`: `THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION`
- `mapped_organ_count`: `23`

证据文件：

- `artifacts/codex_runs/proactive-interaction-dry-run-20260520/run/state/proactive_outbox.jsonl`
- `artifacts/codex_runs/proactive-interaction-dry-run-20260520/run/state/delivery_receipts.jsonl`
- `artifacts/codex_runs/proactive-interaction-dry-run-20260520/run/state/proactive_interactions.jsonl`

## 代码变更

- `host_brain/tribe/thalamus_agent.py`
  - 新增主动回复续接规则。
  - 当 `tribe_context.proactive_interaction.in_reply_to_proactive=true` 时，Thalamus 输出 `route=chat` 与 `THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION`。

- `host_brain/runtime/digital_life_supervisor.py`
  - 新增 `run_proactive_interaction_dry_run()`。
  - 自动消费或生成主动 outbox 消息。
  - 写入 `delivery_receipts.jsonl`。
  - 编译 Thalamus intent draft。
  - 将用户回复回灌到 foreground 主链。
  - snapshot 暴露 `proactive_interaction`。

- `tools/validate_proactive_interaction_loop.py`
  - 新增可复跑验收工具。
  - 输出 summary/details/report 与三类 JSONL 证据。

- `host_brain/app/obs_routes_digital_life_soak.py`
  - 新增“主动交互”验收层。
  - dry-run 送达回执与 Thalamus 续接证据齐全时显示 `PASS`。

## 回归

```powershell
python -m pytest -q tests\unit\test_digital_life_supervisor.py tests\unit\test_thalamus_agent.py tests\unit\test_obs_digital_life_soak_route.py
```

结果：`73 passed`

## 剩余边界

- 真实通讯软件 gateway 尚未消费 outbox。
- 真实 gateway receipt 尚未写回。
- 真实用户回复尚未从 gateway inbound 进入 Host。
- ASR/VTS 仍不属于本次主动交互 dry-run 覆盖面。
