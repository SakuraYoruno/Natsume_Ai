# 正式封测批次 8 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 8**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-20` Tools / MCP / 常用 Skills 阻断复盘

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-20 | BLOCK | `ToolCatalog=0`、`/tools/execute` 返回 `TOOL_NOT_FOUND`、`MCP registry=0`，同时 `session_summary` 在完整 SkillLoader 链上命中 `UNDO_REQUIRED` |

## 关键发现

1. `F-20` 当前不是“还差几个样本”，而是计划要求的 `tools meta-pack -> governor token -> tools execute` 链路在正式运行态无法成立。
2. `/tools/health` 虽返回 `200 OK`，但 `catalog_size=0`；直接读取运行时 `ToolCatalog` 也确认数量为 `0`。
3. `/health/mcp` 返回 `STARTUP_MCP_DEGRADED`，本地 `mcp_tool_registry` 与 `mcp_sync_log` 均为 `0` 行，说明当前没有已同步的 MCP 工具面。
4. `SkillRegistry` 虽已注册 `57` 个 skills，但 `session_summary` 在完整执行链上因缺少 `manifest_ref` 被拒为 `UNDO_REQUIRED`，表明常用 skill 面也未完全闭环。

## 已落盘报告

- `30_module_matrix/F-20_20260315_batch8.md`

## 下一步建议顺序

1. 继续等待 `F-06` soak 采样完成并汇总。
2. 若要修复 `F-20`，优先补运行态 `ToolCatalog` 注册来源与 MCP provider 启动同步链。
3. 随后修复 `session_summary` 的 `manifest_ref / reversible` 口径，再重新复测 `F-20`。

## 当前结论

- `F-20` 已确认应从 `PENDING` 下调为 **BLOCK**。
