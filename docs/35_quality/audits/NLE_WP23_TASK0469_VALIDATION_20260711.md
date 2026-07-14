受众：PROJECT
标题：TASK-0469 Canonical Scheduler Shadow 验证报告

# TASK-0469 / LTE-WP-23 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`b3fa657c67086c340dc61f68ff8efc23b8c8748f`

## 1. 结论

TASK-0469 已建立确定性 Canonical Scheduler shadow。默认 `HOST_TASK_RUNTIME_MODE=legacy` 为严格 no-op；进入 `shadow` 必须配置并精确确认 runtime owner。shadow 只读取同一 SQLite 事务内的 graph/node/attempt/active lease snapshot，输出可摘要、可重放的决策与 legacy action diff，不调用 Worker、Tool、Provider、子进程或现有 executor 主链。

## 2. 决策边界

- dependency 未达到 `VERIFIED/ACCEPTED` 时启动数为 0；依赖产物缺失时阻断，产物 ref 未被 Planner 的 `input_refs` 绑定时 fail-closed。
- retry 支持冻结的 `max_attempts` 和新 `retry_limit/retry_backoff_ms`，二者混用拒绝；指数 backoff 使用精确整数与闭区间时间边界，attempt 编号必须从 1 连续且唯一。
- `LEASED/RUNNING` 缺 active lease 或 lease 到期时只输出 `RECOVER`；fence/expiry 类型漂移拒绝。已有成功 attempt 永不再次 READY。
- `CANCEL_REQUESTED` 输出 CANCEL；同一 snapshot 内的 durable budget 非 ACTIVE 或调用方预算拒绝均输出 BUDGET_BLOCKED。失败 attempt 仅对显式 transient reason code 重试，其余稳定归类为 terminal。所有输出都带 graph revision、node status version、稳定 reason code 和 decision digest。
- 所有 legacy action 差异均保守计为 unexplained；停止条件仍是上线观测中出现无法解释的依赖顺序差异。TASK-0469 不获得切流权限。

## 3. 验证证据

- T-NLE-07 定向合同：18 passed；覆盖顺序依赖、产物绑定、retry classifier/backoff、耗尽、取消、恢复、成功防重放、durable 预算、legacy diff、owner、legacy no-op、旧 retry policy 与精确整数。
- TASK-0467～0469 联合合同：87 passed。
- 实际临时 SQLite 验证读取前后数据库字节一致；测试未使用真实 Worker、实际数据目录或生产数据库。

## 4. 回滚

设置 `HOST_TASK_RUNTIME_MODE=legacy` 即停止 shadow 读取；保留 TaskStore 和 shadow 报告供分析。不得用回滚开启任何 legacy 之外的新执行 authority。
