受众：PROJECT
标题：TASK-0471 Acceptance Judge 验证报告

# TASK-0471 / LTE-WP-31 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`c9df4248`

## 1. 结论

TASK-0471 已把 acceptance criteria 从 prompt 文本提升为可回放的 criterion-level verdict 与 final judge decision。执行结果现在可携带 `acceptance` 字段；`ACCEPTED` 状态必须由 `AcceptanceJudgement(decision=ACCEPT)`、全部 PASS verdict、graph/node/work_order 绑定和 outbox evidence refs 驱动。

## 2. 决策边界

- `AcceptanceCriterionVerdict` 为每条 criterion 记录 `PASS/FAIL/UNKNOWN`、reason code、deterministic 标记与 evidence refs。
- `AcceptanceJudgement` 输出 `ACCEPT|RETRY|SPLIT|REVIEW|FAIL`；`ACCEPT` 必须满足所有 criterion PASS。
- Deterministic FAIL 优先级高于 Critic candidate；Critic `ACCEPT` 不能覆盖 deterministic FAIL。
- Executor 只在存在 acceptance criteria 时运行 judge；无 criteria 的旧 WorkOrder 不受影响。
- `DurableTaskStore.transition_node(... ACCEPTED ...)` 强制 acceptance judgement 与 outbox evidence refs；本任务不授予 Worker 主链切流权限。

## 3. 验证证据

- T-NLE-09 聚焦合同：`6 passed`。
- NLE 全组合与 runtime policy：`142 passed`，覆盖 Acceptance Judge、Verification Gate、TaskStore、canonical contracts、Planner adapter、Shadow scheduler、Codex runtime executor/policies 与 runtime integration。
- `py_compile`：`acceptance_judge.py`、`executor.py`、`task_store.py`、`result_contract.py`、`contract_schema.py` 与新增/修改测试通过。
- `git diff --check`：通过。

## 4. 回滚

不得回滚为“执行成功即可自动 ACCEPT”。如需停止新行为，只能保持 Worker 主链未切流或阻止自动 ACCEPT；ReviewStore、resume token 和 UI 仍由 TASK-0472 后续实现。
