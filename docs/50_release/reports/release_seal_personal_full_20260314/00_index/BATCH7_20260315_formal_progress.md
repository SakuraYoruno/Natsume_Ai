# 正式封测批次 7 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 7**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-31` Provider / Fallback 可用性闭环
- `F-20` Tools / MCP / 常用 Skills 摸底与实跑取证

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-20 | PENDING | 已拿到 `file_list` 成功样本与 `session_summary` 无副作用拒绝样本，但 `/tools` catalog 为空、MCP 成功样本未补齐 |
| F-31 | PASS | `host_8b` / `cloud` 成功、`HOST_CHAT_FORCE_CLOUD` 受控切换、`LLM_UNAVAILABLE` 稳定拒绝、恢复后重新可用均已取证 |

## 关键发现

1. `F-31` 当前已经形成完整闭环：正常主路由、云端成功、强制切云、失败拒绝、恢复后重新可用都已拿到真实证据。
2. `F-20` 当前不是“完全没能力”，而是“SkillRegistry 有能力、ToolBroker 对外 catalog 为空”。
3. `SkillRegistry` 运行时已注册 `57` 个 skill，但 `/tools/health` 返回 `catalog_size=0`，说明 Tool 路由与 Skill 能力面当前并不一致。
4. `session_summary` 在完整 `SkillLoader` 链路中命中 `UNDO_REQUIRED`，暴露出该 skill 的执行闭环仍不完整。

## 已落盘报告

- `30_module_matrix/F-20_F-31_20260315_batch7.md`

## 下一步建议顺序

1. 继续完成 `F-20`：确认当前正式环境是否存在启用的 MCP provider / ToolBroker tools，并补成功样本。
2. 若 `session_summary` 属于正式常用 skill，需要修复其 `manifest_ref` / reversible 口径后再复测。
3. 继续等待 `F-06` soak 采样完成并汇总。

## 当前结论

- 当前新增 `F-31 = PASS`。
- `F-20` 已有初步证据，但暂未满足完整放行口径。
