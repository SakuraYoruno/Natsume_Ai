受众：PROJECT
标题：TASK-0473 Canonical Runtime 主链切流验证报告

# TASK-0473 / LTE-WP-24 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`f1967c94`

## 1. 结论

TASK-0473 已新增 canonical runtime cutover 控制面：`legacy|shadow|canonical` 模式、new-task runtime assignment、canonical canary owner gate、kill switch、legacy drain report、cross-executor resume guard 与 duplicate side-effect detector。内部 Codex work-order 入口会把 assignment 写入 WorkOrder metadata 并在响应中返回；生产默认仍为 `legacy`，本提交未提升 canonical canary 比例。

## 2. 决策边界

- `CanonicalRuntimeRouter` 只做控制面 assignment / guard / report，不直接执行 worker，不重放副作用。
- `canonical` canary 大于 0 时必须匹配 `HOST_TASK_RUNTIME_CANONICAL_OWNER` 或 shadow owner；缺失 owner confirmation fail-closed。
- Kill switch 对新任务立即回退到 legacy；既有任务使用原 assignment，不因后续配置变化跨执行器 resume。
- `build_legacy_drain_report()` 对 legacy registry 的 open/terminal job 可对账；`detect_duplicate_side_effects()` 对相同业务 fingerprint 的重复副作用返回稳定拒绝码。
- `CoreSettings` 支持 `HOST_TASK_RUNTIME_MODE=canonical`、canary percent、kill switch 和 canonical owner，但默认仍为 `legacy` / `0%` / kill switch off。

## 3. 验证证据

- T-NLE-11 聚焦合同：覆盖默认 legacy、canonical owner gate、kill-switch rollback、existing assignment preservation、cross-executor resume reject、legacy drain、canary metrics、duplicate side-effect guard、settings canonical 字段和 configured canonical scheduler。
- 受影响组合回归：`39 passed`，覆盖 runtime cutover、shadow scheduler、internal Codex routes 与 ReviewStore。
- NLE 扩展回归：`108 passed`，覆盖 canonical contracts、Planner adapter、TaskStore、Verification Gate 与 Acceptance Judge。
- 文档/迁移/边界补充回归：`56 passed`，覆盖 doc freshness、NLE boundary scan 与 migration runner contract；仅出现既存 DeprecationWarning。
- `py_compile`：`runtime_router.py`、`core.py`、`task_scheduler.py`、`internal_agent_routes.py`、`job_registry.py` 通过。
- `git diff --check`：通过。

## 4. 回滚

运行时回滚只需保持或设置 `HOST_TASK_RUNTIME_KILL_SWITCH=true` 或 `HOST_TASK_RUNTIME_MODE=legacy`；既有 canonical assignment 不得改派到 legacy resume。若关闭新控制面，保留 metadata 中的 assignment 作为后续 drain 和审计依据。
