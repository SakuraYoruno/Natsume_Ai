受众：PROJECT
标题：P13 SCOPE：tool_registry 工具实现升级（search_knowledge 真实接入）

# P13 SCOPE：tool_registry 工具实现升级（search_knowledge 真实接入）

**日期**：2026-03
**状态**：规划中（待实施）
**对应 SEAL_RESULT**：MULTI_AGENT_P13_SEAL_RESULT_2026-03.md（待创建）
**前置基线**：P12（第九轮正式基线，85 用例全通过，已封板）

---

## 1. 背景与动机

P9/P10/P12 完成了三个专家 Agent（code_agent、translate_agent、retrieval_agent）的 ReAct 化改造，架构上已正确。但 `tool_registry.py` 中所有工具实现均为**占位实现**（返回 `[知识库检索占位]` 等字符串），导致：

- ReAct 循环虽然结构正确，但工具调用返回的是无意义的占位结果
- `retrieval_agent` 的 `search_knowledge` 工具是最核心的检索能力，目前完全无效
- P9/P10/P12 的 ReAct 化从"架构正确"到"功能可用"之间存在明显缺口

P13 的目标是**填补这一缺口**：将 `search_knowledge` 从占位实现替换为真实的知识库检索接入，同时为 `retrieval_agent` 扩展专属工具集（`search_by_keyword`、`search_by_vector`），让 P12 的 ReAct 化真正产生实际检索价值。

---

## 2. 唯一目标（P13-1）

**P13-1**：`tool_registry` 工具实现升级

- 将 `search_knowledge` 从占位实现替换为真实检索接入（接入 `host_brain` 现有知识库检索后端）
- 为 `retrieval_agent` 扩展两个专属工具：`search_by_keyword`（关键词检索）、`search_by_vector`（向量检索）
- 更新 `_AGENT_TOOLS` 中 `retrieval_agent` 的工具 allowlist，纳入新工具
- 保持 `tool_registry.py` 的接口契约不变（`ToolSpec`、`execute_tool`、`get_tools_for_agent`、`is_tool_allowed`）

---

## 3. 验收标准（写死，不得自行增减）

| 编号 | 验收标准 |
|------|----------|
| 3.1 | `search_knowledge` 工具执行后返回真实检索结果（非占位字符串），`result` 字段非空且不包含 `[知识库检索占位]`；有单元测试验证 |
| 3.2 | `retrieval_agent` 的工具 allowlist 包含 `search_by_keyword` 和 `search_by_vector`；两个新工具均有真实实现（非占位）；有单元测试验证 |
| 3.3 | P12 基线（85 用例）不退化，`tool_registry` 接口契约（`ToolSpec`、`execute_tool`、`get_tools_for_agent`、`is_tool_allowed`）不变；有集成测试验证 |

---

## 4. tool_registry_trace 字段契约（P13 新增，写死）

`search_knowledge` 真实实现的返回结构（`execute_tool` 返回值）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `result` | str | 检索结果文本，非空，非占位字符串 |
| `source` | str | 检索来源，如 `"knowledge_base"` |
| `confidence` | float | 检索置信度，0.0～1.0 |
| `items` | list | 检索命中条目列表（可为空列表） |

`search_by_keyword` 返回结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `result` | str | 关键词检索结果文本 |
| `source` | str | `"keyword_index"` |
| `matched_count` | int | 命中条目数 |

`search_by_vector` 返回结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `result` | str | 向量检索结果文本 |
| `source` | str | `"vector_store"` |
| `top_k` | int | 返回条目数 |

---

## 5. 冻结非目标（P13 不做）

- **不改** `react_loop.py`（ReAct 循环核心逻辑不变）
- **不改** `agent_registry.py`（agent 元数据不变）
- **不改** `retrieval_agent_handler.py`（handler 逻辑不变）
- **不改** `code_agent_handler.py`、`translate_agent_handler.py`（其他 handler 不变）
- **不做** `judge_agent` ReAct 化（P14 或更晚阶段）
- **不做** `react_loop` 并行工具调用（P14 或更晚阶段）
- **不做** `agent_contract.py` 置信度规则重构
- **不做** 三图主链结构修改

---

## 6. 改动范围（最小实现面）

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/agents/tool_registry.py` | `_tool_search_knowledge` 替换为真实检索实现；新增 `_tool_search_by_keyword`、`_tool_search_by_vector`；`_TOOL_REGISTRY` 注册新工具；`_AGENT_TOOLS["retrieval_agent"]` 扩展新工具 |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/unit/test_tool_registry_p13.py` | P13 工具实现单元测试（覆盖 3.1、3.2 验收标准） |
| `tests/integration/test_p13_integration.py` | P13 集成测试（覆盖 3.3 验收标准，P12 基线不退化） |
| `docs/50_project/MULTI_AGENT_P13_SEAL_RESULT_2026-03.md` | 封板结论（实施后创建） |

---

## 7. 测试基线

| 测试文件 | 预期用例数 |
|---------|-----------|
| `tests/unit/test_tool_registry_p13.py` | ≥ 20 |
| `tests/integration/test_p13_integration.py` | ≥ 15 |
| P12 基线（85 用例）不退化 | 85 |

---

## 8. 与前序阶段的兼容性

- **P12 retrieval_agent_trace 契约**：不变（`mode`、`artifact_ref`、`retrieval_source`、`result_count`、`total_steps`、`stopped_reason`、`ts_ms`）
- **P9 code_agent_trace**：不受影响
- **P10 translate_agent_trace**：不受影响
- **P8 ledger 聚合**：不受影响
- **三图主链结构**：未修改

---

## 变更记录

- 2026-03-17 v1.0：P13 SCOPE 初稿，基于 P12 封板基线，方向确认为工具实现升级（首选）。
