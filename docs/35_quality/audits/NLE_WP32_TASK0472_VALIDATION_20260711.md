受众：PROJECT
标题：TASK-0472 Review / Resume 验证报告

# TASK-0472 / LTE-WP-32 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`2be41759`

## 1. 结论

TASK-0472 已把 `review_required` 布尔提示扩展为可持久化、可重启、可过期、绑定 subject version 与 checkpoint 的 ReviewStore / ReviewToken / Resume 机制。新增 review API 只创建、审批、拒绝和消费 resume token；POST resume 返回 checkpoint 决策，不调用执行器，不重放已发生副作用。

## 2. 决策边界

- `agent_runtime_review` 与 `agent_runtime_review_token` 由中央 0055 migration 创建；运行时不内建 DDL，schema 未就绪时 fail-closed。
- `ReviewTokenService` 只生成一次性 bearer 与 digest；ReviewStore 在同一 SQLite 事务里持久化 token binding、ReviewItem 状态、checkpoint 和消费结果。
- Resume token 绑定 `review_id`、`subject_ref`、`subject_version`、`resume_phase` 与 `checkpoint_id`；过期、串用、重复消费均返回稳定拒绝码。
- PRE 与 POST resume 分态：PRE 返回 `START_EXECUTION`，POST 返回 `RETURN_POST_EXECUTION_CHECKPOINT`；两者均声明不重放既有副作用。
- Legacy `confirmed=true` 兼容路径未切断；但 WorkOrder 一旦声明 durable review / review token，`confirmed=true` 不能替代 `review_resume_token_ref`。
- 本任务未切 Worker 主链，未实现完整 Review UI；AgentJobRegistry 仍是执行 authority。

## 3. 验证证据

- T-NLE-10 聚焦合同：`5 passed`，覆盖重启可查、过期拒绝、token 串用拒绝、重复 resume 拒绝、POST resume 不重放副作用、`confirmed=true` 不能绕过 durable review。
- Internal Codex review routes：`7 passed`，覆盖 create / approve / resume 与重复 resume HTTP 409。
- NLE 全组合回归：`94 passed`，覆盖 canonical contracts、ReviewStore、TaskStore、Shadow Scheduler 与 internal Codex routes。
- 文档/迁移/边界补充回归：`56 passed`，覆盖 doc freshness、NLE boundary scan 与 migration runner contract；仅出现既存 DeprecationWarning。
- `py_compile`：`review_store.py`、`tokens.py`、`internal_agent_routes.py` 通过。
- `git diff --check`：通过。

## 4. 回滚

不得回滚为“`confirmed=true` 即可授权 durable review 路径”。如需停用新入口，只关闭 `/internal/codex/reviews*` 高风险入口或不声明 `require_review_token`，保留 0055 expand-only 表和 ReviewStore 记录；POST resume 仍不得重放副作用。
