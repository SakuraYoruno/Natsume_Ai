受众：PROJECT
标题：TASK-0470 Verification Gate 验证报告

# TASK-0470 / LTE-WP-30 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`a1304c4ac`

## 1. 结论

TASK-0470 已把 Verification 从执行报告字段升级为 fail-closed 状态门。命令验证不再直接运行任意 argv；仅允许受控的 `python -m pytest` 与只读 Git 子命令，统一绑定 workspace/cwd、timeout、sanitized env、结构化 reason code 和 durable evidence refs。

## 2. 决策边界

- `VerificationResult` 现在要求精确 boolean/integer 类型、稳定 `PASS/FAIL/ERROR/NOT_RUN`、非 PASS reason code、bounded durable evidence refs 和 checks。
- `DurableTaskStore.transition_node` 对 `VERIFIED` 与 `VERIFICATION_FAILED` 强制携带 VerificationResult，并校验 `work_order_id`、`task_graph_id`、`task_node_id` 与 outbox evidence refs；required `FAIL/ERROR/NOT_RUN` 不能进入 `VERIFIED`，因此也不能继续进入 `ACCEPTED`。
- `AgentRuntimeExecutor` 对 `verification_plan.required=true` 的非 PASS 或 invalid verification 统一返回 `BLOCKED`，保留原 verification 详情并附加 `VERIFICATION_REQUIRED_NOT_PASSED`。
- Forbidden command、workspace escape、timeout 与 provider exception 均为结构化 `ERROR`，不会被当作 PASS。

## 3. 验证证据

- T-NLE-08 定向合同：`10 passed`。
- NLE 扩大合同与 runtime policy：`134 passed`，覆盖 canonical contracts、TaskStore、Planner adapter、Shadow scheduler、Codex runtime executor/policies。
- Codex runtime 集成：`3 passed`。
- TASK-0470 聚焦组合：`76 passed`。
- `py_compile`：`verification_policy.py`、`task_store.py`、`executor.py`、新增/修改测试通过。
- `git diff --check`：通过。
- 静态命令面：`verification_policy.py` 无 raw `subprocess.run`、`shell=True`、`os.system` 或旧式 `command.split()`；仅保留 executor 既有 Codex `Popen` 执行面。

## 4. 回滚

不得回滚为“required verification 失败仍可验收”。如需停止新行为，只能关闭新任务执行或保持 Worker 主链未切流状态；现有 AgentJobRegistry authority 未改变，TASK-0470 不授予主链切流权限。

## 5. 已知外部质量债

额外运行 `tests/contract/test_quality_contract.py` 时，既存 Pentest 文件的裸 `print()` 扫描失败：`host_brain/pentest/oob_server.py`、`host_brain/pentest/reverse_shell.py`、`host_brain/pentest/tools/zerologon_exploit.py`。这些文件不在 TASK-0470 改动范围内，本任务未修改该质量债；后续应由独立 Pentest/quality 任务处理。
