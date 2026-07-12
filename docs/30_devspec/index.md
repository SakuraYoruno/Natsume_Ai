受众：CODEX
标题：开发实现规范目录

# 开发实现规范目录

版本：v2.3
维护日期：2026-07-12
标签：当前事实

本目录是实现层主要入口。任务执行前先确认主链入口与工程口径。

## 5分钟入门（给新成员）
1. 先读完整程序：`NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`。
2. 需要追溯 M48 历史实现时再读：`NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md`；它不再是执行入口。
3. 通用实现先读：`DevSpec.md` 的“2. Host Main 主链契约”。
4. 再读 `ModelPlane.md` 的“0.1 当前链路入口”；当前代码事实入口见 `DevSpec.md` 第 14 节。

## 关键文件
- `NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`（Architecture Waves v2 唯一执行合同；AW-00～AW-08）
- `NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md`（M48-A 历史实现记录）
- `DevSpec.md`（v2.6 — 当前代码工程契约参考，含 Architecture Waves 目标合同）
- `ModelPlane.md`（v1.7 — 模型平面规格）
- `desktop_pet_runtime.md`（Desktop Pet 运行面契约）
- `mateengine_bridge.md`（MateEngine Bridge 契约）
- `DevTasks.md`（TASK 历史需求与实现追踪，不再调度架构重构）
