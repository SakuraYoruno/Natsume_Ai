受众：CODEX
标题：当前程序与历史任务注册表

# 当前程序与历史任务注册表

版本：v3.0
维护日期：2026-07-15
状态：`CURRENT_PROGRAM_REGISTRY`

## 当前执行口径

- 完整目标：`../50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`
- 当前正式执行合同：`NATSUME_AI_UNIVERSAL_GOVERNED_PLANNING_EXECUTION_20260714.md`
- Core Infrastructure 历史合同与封板纠错：`NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`
- M48-A 安全地基合同：`NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md`
- 当前代码事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`

GR-00～GR-06 已完成 Generic Planning Search、原生多节点 Durable TaskGraph、chat/skill/delegate/plan 路线切流和 live route matrix 验收。验收结果见：

- `../45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json`
- `../45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`

该结果不等于 Full Architecture 封板。全局 service locator 等剩余缺口仍使完整架构状态保持 `INCOMPLETE`。

## 历史编号

- `TASK-0000～TASK-0622` 的完整正文、旧状态与逐任务验收记录已停止在当前文档中重复维护。
- 完整历史可从 Git 提交 `9b6db346` 及其父提交追溯。
- 旧 `DONE` 仅说明当时的局部交付，不证明当前 production authority 已完成切换。
- 历史 FR/TASK/T 编号仍可用于检索，但不得重新成为逐任务调度或架构封板依据。

## 后续记录规则

1. 新工作以目标架构缺口和 production cutover 为单位，不再扩张本文件。
2. 实施状态写入对应 PR/Issue；长期合同写入 DevSpec；机器证据写入固定 evidence/release 路径。
3. 完成判据必须包含单一 authority、旧路径删除、真实路线验收与回滚证据。
4. 文档数量、测试数量、任务数量和 diff 行数均不计为架构完成度。
