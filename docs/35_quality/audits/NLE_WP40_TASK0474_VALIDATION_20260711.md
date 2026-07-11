受众：PROJECT
标题：TASK-0474 Runtime Event / SSE Replay 验证报告

# TASK-0474 / LTE-WP-40 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`bd611a0f6`

## 1. 结论

TASK-0474 已新增 durable runtime event timeline：`runtime_event_timeline` 由 0056 migration 管理，`RuntimeEventStore` 只在 schema ready 后运行，不做运行期 DDL。TaskStore outbox 通过 append-then-mark publisher 接入 timeline，Tribe EventBus 和 Graph/Pentest/BioOps/Review/Verification 等领域可通过 reference adapter 写入只含 refs/metadata 的事件。内部 Codex 提供 JSON cursor replay 与 SSE replay，客户端以 `dedupe_key` 去重；replay API 默认受 `HOST_RUNTIME_EVENT_REPLAY_ENABLED=false` 保护。

## 2. 决策边界

- `TribeEventBus` 仍是内存通知/投影，不作为恢复真源；恢复真源是 `runtime_event_timeline.cursor`。
- TaskStore outbox 发布顺序为“先写 runtime event，再把 outbox 标记为 `PUBLISHED`”。若标记失败，重试使用同一 dedupe key，不重复改变客户端状态。
- Runtime event payload 只允许 JSON-safe、bounded、redacted metadata；`raw_thought`、`private_body`、`secret`、`cookie`、`resume_token`、Bearer、私钥等字段或值 fail-closed。
- Retention 与历史清理未执行；任务卡要求该动作需 MASTER 批准。本任务只提供 schema 与 replay 基础，不删除历史事件。
- SSE replay 支持 `after` cursor 与 `Last-Event-ID`；每条事件使用 cursor 作为 SSE `id`，并追加 cursor summary event。

## 3. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/audit/migrations/0056_runtime_events.sql` | ADD | central expand-only runtime event timeline schema 与索引 |
| `host_brain/integrations/runtime_events.py` | ADD | `RuntimeEventStore`、publisher、Tribe/reference adapters、redaction/dedupe |
| `host_brain/app/internal_agent_routes.py` | MODIFY | `/internal/codex/runtime/events` 与 `/runtime/events/sse` |
| `host_brain/app/settings/core.py` | MODIFY | `HOST_RUNTIME_EVENT_REPLAY_ENABLED` readiness field |
| `tests/contract/test_nle_runtime_event_contract.py` | ADD | T-NLE-12 合同测试 |
| `tests/integration/test_codex_internal_routes.py` | MODIFY | cursor/SSE route 与 flag 负例 |

## 4. 验证证据

- T-NLE-12 聚焦合同：`7 passed`，覆盖 migration ready、cursor replay、dedupe、敏感字段拒绝、outbox at-least-once、pending duplicate retry 与 Tribe adapter redaction。
- 内部 Codex 路由：`12 passed`，覆盖 work-order/review/runtime route 既有行为、JSON replay、`Last-Event-ID` SSE replay 与 replay flag disabled 负例。
- TaskStore + RuntimeEvent 组合：`47 passed`，确认 outbox 既有合同与新增 publisher 共存。
- migration/boundary 组合：`49 passed`，仅出现既存 pentest/persona regex DeprecationWarning。
- settings/readiness 组合：`6 passed, 15 subtests passed`，确认新增开关进入 CoreSettings readiness。
- M48-A 主要 NLE 合同组合：`127 passed`，覆盖 canonical contracts、Planner adapter、TaskStore、Verification、Acceptance、Review、Runtime cutover 与 Runtime Event。
- 文档 freshness / NLE boundary / migration runner：`56 passed`，仅出现既存 pentest/persona regex DeprecationWarning。
- `py_compile`：`runtime_events.py`、`internal_agent_routes.py`、`core.py`、T-NLE-12 与 internal route tests 通过。

## 5. 回滚

运行时回滚先保持 `HOST_RUNTIME_EVENT_REPLAY_ENABLED=false`，停止 replay UI/调用方使用；若 publisher 尚未接入生产调度，则无需停止后台任务。已写入的 `runtime_event_timeline` 事件保留用于审计和后续清理，历史清理必须另行获得 MASTER 批准。
