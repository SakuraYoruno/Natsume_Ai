受众：MASTER
标题：发布与回滚目录

# 发布与回滚目录

版本：v1.1
维护日期：2026-07-11
标签：历史归档 + 持续记录

本目录记录发布方案、changelog 与回退/回放脚本。用于版本发布前后核对。

## 5分钟入门（给新成员）
1. 先读：`release_seal_test_plan_20260313.md` 与 `release_seal_execution_report_20260316.md`。
2. 看 `rollback/` 与 `changelog/` 目录确认历史回退策略与风险。
3. 运行前核对 `reports/` 对应发布批次归档。

## 关键目录
- `m48/`：TASK-0486 的 evidence、manifest、rollback matrix、known limitations、identity status 与机器可读 Seal Result；当前状态为 `BLOCKED`，不是 `SEALED`。
- `changelog/`
- `reports/`
- `rollback/`
