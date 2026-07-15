受众：CODEX
标题：当前架构程序注册表

# 当前架构程序注册表

版本：v4.0
维护日期：2026-07-15
状态：`ONE_ACTIVE_PROGRAM`

## 唯一活动程序

- 目标：`../50_project/arch/TARGET_ARCHITECTURE.md`
- 当前事实：`../50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 差距：`../50_project/arch/ARCHITECTURE_GAP_MATRIX.md`
- 执行合同：`NEXT_GENERATION_EVOLUTION_EXECUTION.md`
- 机器状态：`../../configs/release/current_architecture_status.json`

当前执行波为 NE-00～NE-07。NE 是端到端 Architecture Wave，不拆回逐个 TASK Seal。

## 历史编号

- TASK-0000～TASK-0622、M48～M57、AW-00～08、GR-00～06 已冻结。
- 原文位于 `../_history/programs/2026-07/` 或 Git 历史。
- 历史编号仍可用于搜索，不得决定当前完成度、优先级或 Goal。

## 记录规则

1. 小工作片只写 PR checklist 和定向测试，不扩张本文件。
2. Architecture Wave 状态写在唯一执行合同和机器状态中。
3. 一个波只有 production cutover、旧 authority 删除和 wave integration 同时成立才可完成。
4. 提交信息与 PR 全部使用中文。
