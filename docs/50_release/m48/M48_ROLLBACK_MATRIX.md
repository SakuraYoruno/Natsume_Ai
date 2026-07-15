受众：MASTER
标题：M48-A 回滚矩阵

# M48-A 回滚矩阵

版本：v1.0
日期：2026-07-11
状态：`BLOCKED`

## 结论

本地离线演练已覆盖 runtime mode/canary/drain/kill switch、Runtime Event replay/SSE、Life projection、ActionArbiter、Capability Maturity、DegradationState、Monitor bundle 和 Pentest Lab 门禁。真实发布仍不能执行：仓库没有受信的上一版 IdentitySnapshot，且没有获批的生产目标、迁移窗口或 live rollback target。

## 矩阵

| Rollback ID | 范围 | 结果 | 说明 |
|---|---|---|---|
| `runtime_mode_canary` | runtime mode/canary/drain/kill switch | PASS | 离线集成链验证默认 legacy、canary 回退、drain 与 kill switch。 |
| `runtime_event_replay` | durable event/replay/SSE | PASS | 验证 cursor replay、dedupe、断连恢复与敏感字段 fail-closed。 |
| `life_projection` | World/Appraisal/Bond/Scene read projection | PASS | 只读 projection 与 feature flag 回退，不反写 Life Core。 |
| `action_arbiter` | Governor/Arbiter/token gate | PASS | 无有效 route token 时 fail-closed；dry-run 不执行。 |
| `capability_maturity` | maturity/governance view | PASS | 自动流程只降级；视图关闭不关闭后端安全门。 |
| `degradation_state` | degradation/recovery | PASS | 进入、恢复、证据与受限能力均可重放。 |
| `operator_life_governance_ui` | Monitor UI | PASS | build 可复现；增量视图可由 feature flag 隐藏。 |
| `pentest_lab_gate` | Pentest readonly/high-risk gate | PASS | readonly 保留；lab/approval/continuation 未获治理授权时禁止。 |
| `identity_snapshot` | identity continuity | BLOCKED | 缺少受信 previous release ref，禁止构造假快照。 |
| `production_migration` | 生产迁移/cutover/rollback | BLOCKED | 无获批生产目标、备份、窗口和 operator authorization。 |

机器可读版本见 `M48_ROLLBACK_MATRIX.json`。
