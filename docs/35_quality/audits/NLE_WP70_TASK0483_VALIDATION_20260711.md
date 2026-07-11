受众：PROJECT
标题：TASK-0483 Execution、Trace 与 Review UI 验证报告

# TASK-0483 / LTE-WP-70 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
集成基线：`a48269c25`
来源提交：`2d6719f531334d773924861e9d8bf8cb764d716d`

## 1. 结论

TASK-0483 已在现有 Monitor UI 上新增 Execution/Review Operator 视图，没有重写 Console。后端通过 versioned read model 和只读 facade 聚合 TaskStore、ReviewStore、RuntimeEventStore；`/obs/task-runtime` 提供 index/detail、DAG/node attempt、budget、verification、Review 状态与 cursor timeline，不暴露内部表名、SQLite 或 raw thought。

前端展示任务从计划到终态的 DAG、attempt/budget/verification、Review approve/reject/expired、resume 绑定信息与 replay cursor。resume 缺 token 时禁用，提交必须携带 subject/version/phase/checkpoint/token，不存在 `confirmed=true` 旁路。`HOST_NLE_OPERATOR_VIEWS_ENABLED=false` 在依赖构造前 404，可隐藏整个增量视图而不影响后端任务状态机。

静态资产由预期的 Monitor build 生成；连续两次 build 的 140 个静态文件 SHA256 完全一致，批量 Vite hash 重命名属于确定性构建结果。

## 2. 主要变更

| 路径 | 说明 |
|---|---|
| `host_brain/agent_runtime/operator_read_model.py` | versioned execution index/detail read model 与三 Store 只读 facade |
| `host_brain/app/obs_routes_task_runtime.py` | feature-gated Operator read routes |
| `host_brain/app/monitor_ui/src/features/execution/` | API、query、model、tests 与 TaskRuntimeConsole |
| `host_brain/app/monitor_ui/src/pages/TaskRuntime.jsx` | Execution/Review 页面 |
| `host_brain/app/monitor_ui/e2e/monitor-interactions-smoke.spec.js` | approve/reject/expired、resume 与 replay 交互覆盖 |
| `host_brain/app/static/monitor/` | 已审计 deterministic build 资产 |
| `tests/contract/test_nle_operator_read_model_contract.py` | T-NLE-21 合同 |

## 3. 验证证据

- T-NLE-21 focused：`7 passed`；与 T-NLE-17 联合专项：`19 passed`。
- Task/Review/Event Store 与 routes 相关回归：`88 passed`。
- Monitor read-model Node tests：`4 passed`。
- Monitor full E2E：`96/96 passed`，原 93 项无回归并新增 3 项。
- ESLint、TypeScript typecheck、Vite build、Python `py_compile`、`git diff --check`：通过。
- 静态 build：连续两次 140 文件 SHA256 完全一致。
- 合入 TASK-0479 后的全量 NLE 合同：`206 passed / 17 warnings`；warnings 均为既有 invalid-escape deprecation。
- 文档 audience/delivery scoped gates：通过；freshness scoped gate：`4 checked / 2 skipped / 0 failures`，并同步 Operator 增量到前端架构说明。

## 4. 停止条件与回滚

未发现 UI 读取未版本化内部表、展示 raw thought、直接写数据库、用空 token/`confirmed` 绕过 resume 或改变后端状态机。关闭 `HOST_NLE_OPERATOR_VIEWS_ENABLED` 即可在依赖构造前隐藏 routes/页面；TaskStore、ReviewStore、RuntimeEventStore 与既有 Trace 不受影响。
