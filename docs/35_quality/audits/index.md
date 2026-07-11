受众：MASTER
标题：质量审计子目录

# 质量审计子目录

版本：v1.17
维护日期：2026-07-11
标签：当前执行证据 + 历史归档

本目录存放定期审计记录，建议按审核周期查询。

## 5分钟入门（给新成员）
1. 先按文件名时间顺序确认最近一次审计。
2. 识别“建议项/必修项”与“风险等级”。
3. 回到对应 `PRD` 或 `DevSpec` 条目确认落地情况。

## 关键文件
- `NLE_WP00_BASELINE_REPORT_20260710.md`：TASK-0461 仓库整理、调用路径与测试冻结基线。
- `NLE_WP00_BASELINE_MANIFEST_20260710.json`：原 dirty 工作树的机器可读路径、状态与 SHA-256 清单。
- `NLE_WP00_VALIDATION_REPORT_20260710.md`：TASK-0461 实现、门禁与封板验收证据。
- `NLE_WP00_VALIDATION_REPORT_TEMPLATE.md`：TASK-0461 封板交付模板。
- `NLE_WP01_TASK0462_VALIDATION_20260710.md`：Trace 最小化、兼容输出与泄漏负例验收证据。
- `NLE_WP10_TASK0463_VALIDATION_20260710.md`：Pentest Domain Store、中央 migration 与 shadow-only 验收证据。
- `NLE_WP11_TASK0464_MIGRATION_MANIFEST_20260710.json`：87 份 Canon JSON 闭集扫描、83 份 Pentest eligible 的只读稳定迁移、receipt、hash/计数/检索对账证据。
- `NLE_WP11_TASK0464_VALIDATION_20260710.md`：Pentest receipt-gated domain-only、promotion candidate、回滚与静态门禁验收证据。
- `NLE_WP12_TASK0465_VALIDATION_20260710.md`：signed Canon Apply、durable token/journal、fence/lease/recovery 与 raw writer 静态闭集验收证据。
- `NLE_WP20_TASK0466_VALIDATION_20260710.md`：Canonical contracts、schema snapshot 与兼容验收证据。
- `NLE_WP21_TASK0467_VALIDATION_20260710.md`：Durable TaskStore、trusted-clock lease、Ed25519 reconciliation、cancel-wait、强绑定 outbox 与 legacy shadow 验收证据。
- `NLE_WP22_TASK0468_VALIDATION_20260710.md`：same-revision Planner adapters、dependency refs、Child WorkOrder request 与 shadow-only 验收证据。
- `NLE_WP23_TASK0469_VALIDATION_20260711.md`：read-only Canonical Scheduler shadow、durable snapshot、retry/recovery/budget 与 legacy diff 验收证据。
- `NLE_WP24_TASK0473_VALIDATION_20260711.md`：canonical runtime mode router、canary/kill switch、legacy drain、cross-executor resume guard 与 T-NLE-11 验收证据。
- `NLE_WP40_TASK0474_VALIDATION_20260711.md`：durable runtime event timeline、TaskStore outbox publisher、JSON/SSE replay、dedupe 与敏感字段 fail-closed gate 验收证据。
- `NLE_WP41_TASK0475_VALIDATION_20260711.md`：AgentContextEnvelope v2、context refs 授权、token budget/actual estimate、truncation reason、pollution fail-closed 与 T-NLE-13 验收证据。
- `NLE_WP50_TASK0476_VALIDATION_20260711.md`：Digital Life expected-version OCC、0057 migration、冲突/显式 fallback audit 与并发验收证据。
- `NLE_WP51_TASK0477_VALIDATION_20260711.md`：只读 WorldProjection、source/confidence/TTL/conflict/epistemic facts 与无反写验收证据。
- `NLE_WP52_TASK0478_VALIDATION_20260711.md`：确定性 Appraisal replay、只读 BondProjection、BondDynamics 单写收敛与 rift/repair/boundary 验收证据。
- `NLE_WP53_TASK0479_VALIDATION_20260711.md`：expiring SceneDecision、ProactiveVoice 接线、只收紧 Governor 的 ActionArbiter 与 dry-run/Runtime Event 验收证据。
- `NLE_WP70_TASK0483_VALIDATION_20260711.md`：Execution/Review versioned read model、Operator UI、token-bound resume、cursor replay、Monitor E2E 与 deterministic build 验收证据。
- `NLE_WP30_TASK0470_VALIDATION_20260711.md`：fail-closed Verification runner、required gate、TaskStore 状态门与 T-NLE-08 验收证据。
- `NLE_WP31_TASK0471_VALIDATION_20260711.md`：AcceptanceJudgement、criterion verdict、Executor acceptance 字段、TaskStore ACCEPTED 状态门与 T-NLE-09 验收证据。
- `NLE_WP32_TASK0472_VALIDATION_20260711.md`：Durable ReviewStore、绑定 resume token、一次性 PRE/POST resume 与 T-NLE-10 验收证据。
- `RUNTIME_CHAIN_ACCEPTANCE_20260531.md`
- `MEMORY_SYSTEM_INTEGRITY_TEST_20260531.md`
- `audit_report_20260311.md`
- `DIGITAL_LIFE_GAP_CHECKLIST_V2_20260403.md`
