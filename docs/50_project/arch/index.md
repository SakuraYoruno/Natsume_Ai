受众：MASTER
标题：架构档案目录

# 架构档案目录

版本：v1.4
维护日期：2026-07-10
标签：当前事实 + 历史归档

`arch/` 包含当前架构事实、专项模块说明与历史计划。主链入口优先以 `CURRENT_CODE_ARCHITECTURE.md` (v4.2) 为准；需要模块级定位时看 `CORE_MODULES_DETAILED_REFERENCE.md` (v4.2)，需要 Pentest 子系统时看 `PENTEST_MODULES_ARCHITECTURE.md` (v1.1)。

## 5分钟入门（给新成员）
1. 先读当前事实：`CURRENT_CODE_ARCHITECTURE.md` (v4.2)。
2. 再读目标归宿：`NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`。
3. 设立心智模型：`diagrams/00_INDEX.md`（7 张架构/数据流/流程图）。
4. 再看：`CORE_MODULES_DETAILED_REFERENCE.md` (v4.2) 与 `dataflow_report.md` (v4.0)。
5. 如果工作涉及授权安全测试，补读：`PENTEST_MODULES_ARCHITECTURE.md` (v1.1) 与 `../../../host_brain/pentest/CAPABILITY.md` (v2.3)。
6. 其余文件按历史阶段、任务编号与时间线回溯。

## 关键文件
- `CURRENT_CODE_ARCHITECTURE.md` (v4.2)
- `CORE_MODULES_DETAILED_REFERENCE.md` (v4.2)
- `PENTEST_MODULES_ARCHITECTURE.md` (v1.1)
- `dataflow_report.md` (v4.0)
- `diagrams/00_INDEX.md` (v1.0)：架构图册总目录
- `NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`：M48～M57 完整目标架构、边界与最终 Gate。
- `NATSUME_AI_V1_LONG_TERM_EVOLUTION_REFACTOR_PLAN_20260710.md`：M48-A 安全地基的代码事实与局部裁决。
