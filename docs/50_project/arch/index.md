受众：MASTER
标题：架构档案目录

# 架构档案目录

版本：v1.2
维护日期：2026-06-30
标签：当前事实 + 历史归档

`arch/` 包含当前架构事实、专项模块说明与历史计划。主链入口优先以 `CURRENT_CODE_ARCHITECTURE.md` (v4.1) 为准；需要模块级定位时看 `CORE_MODULES_DETAILED_REFERENCE.md` (v4.1)，需要 Pentest 子系统时看 `PENTEST_MODULES_ARCHITECTURE.md` (v1.0)。

## 5分钟入门（给新成员）
1. 先读：`CURRENT_CODE_ARCHITECTURE.md` (v4.1)。
2. 设立心智模型：`diagrams/00_INDEX.md`（7 张架构/数据流/流程图）。
3. 再看：`CORE_MODULES_DETAILED_REFERENCE.md` (v4.1) 与 `dataflow_report.md` (v3.0)。
4. 如果工作涉及授权安全测试，补读：`PENTEST_MODULES_ARCHITECTURE.md` (v1.0) 与 `../../../host_brain/pentest/CAPABILITY.md` (v2.2)。
5. 其余文件按历史阶段、任务编号与时间线回溯。

## 关键文件
- `CURRENT_CODE_ARCHITECTURE.md` (v4.1)
- `CORE_MODULES_DETAILED_REFERENCE.md` (v4.1)
- `PENTEST_MODULES_ARCHITECTURE.md` (v1.0)
- `dataflow_report.md` (v3.0)
- `diagrams/00_INDEX.md` (v1.0)：架构图册总目录
