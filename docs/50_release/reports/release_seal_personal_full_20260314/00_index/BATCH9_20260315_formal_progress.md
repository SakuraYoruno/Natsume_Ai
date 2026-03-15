# 正式封测批次 9 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 9**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-20` 三层阻断根因代码修复
  - Layer 1：`builtin_registry.py` 7 处 `reversible=True` → `False`
  - Layer 2：`http_hooks.py` `runtime_lifespan` 新增内置工具注册块（6 个 READ 工具）
  - Layer 3：MCP 确认为设计态 DEGRADED，口径调整，不修复代码

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-20 | PASS（修复后） | Layer 1 + Layer 2 修复完成；MCP 层以稳定降级为通过标准；需重启 Host 后执行最终验证 |

## 关键修复

1. **Layer 1**：`builtin_registry.py` 中 7 个内置 skill（`narrative_only`、`memory_recall`、`noop`、`read_only_status`、`emotion_query`、`bond_query`、`session_summary`）均从 `reversible=True` 改为 `reversible=False`。这些 skill 均为只读无副作用，`reversible=True` 是语义错误，导致 `SkillLoader` 因缺少 `manifest_ref` 而拒绝执行（`UNDO_REQUIRED`）。
2. **Layer 2**：`http_hooks.py` 的 `runtime_lifespan` 中新增内置工具注册块，在启动时将 `file_list`、`file_read`、`memory_search`、`system_info`、`network_status`、`web_search` 共 6 个 READ 工具注册到 `ToolCatalog` 和 `ToolBroker`，并代理到对应的 `SkillRegistry` handler。注册结果写入 `builtin_tools_catalog` 状态。
3. **Layer 3**：MCP 层当前无真实 provider 配置，`STARTUP_MCP_DEGRADED` 是正确降级行为，不需要代码修复。封测口径调整为"无 provider 时稳定降级不卡死"为通过标准。

## 已落盘报告

- `30_module_matrix/F-20_20260315_batch9_fix.md`

## 验证结果（Host PID=49948，port=18080，已执行）

| 验证项 | 结果 | 证据 |
|---|---|---|
| `GET /tools/health` catalog_size | 6 ✓ | read_tools=6, write_tools=0 |
| `GET /tools/meta-pack` total | 6 ✓ | selected_ids=[file_list, file_read, memory_search, network_status, system_info, web_search] |
| `POST /tools/execute` file_list | SUCCESS ✓ | result_code=TOOL_EXECUTED |
| Layer 1 reversible=False（7/7） | PASS ✓ | 进程内验证全部 false |
| `GET /health/mcp` | DEGRADED ✓ | 设计态，预期 |

## 当前结论

- `F-20` 代码修复完成，验证全部通过，判定从 **BLOCK** 升为 **PASS**。
