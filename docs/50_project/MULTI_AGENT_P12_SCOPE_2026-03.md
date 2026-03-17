# P12：下一轮候选基线 — 范围与验收标准

**日期**：2026-03
**状态**：定稿/待实施
**定位**：在 P1～P11 全部封板的前提下，将 `retrieval_agent` 从 legacy_chain 模式升级为受控 ReAct 模式（引入 `RETRIEVAL_AGENT_REACT_ENABLED` 开关），不破坏八轮基线
**总原则**：先定范围与验收再实施；不把未在 P12_SCOPE 明确纳入的能力混入 P12；不得回改已封板结论。

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P12 中不回改：

- **第一轮正式基线**：P1～P4，已封板
  - execution_profile / 三图分治、Supervised Graph 主路径、Intent 透明子图、subgraph_commit_summary / subgraph_failure 契约固化

- **第二轮正式基线**：P5，已封板
  - Supervisor rule/llm 双模、proposal 契约、失败码、灰度与回退、supervisor_decision_log / supervisor_metrics

- **第三轮正式基线**：P6，已封板
  - Intent 子图局部循环、local loop 边界与失败码、step 级 subgraph_trace；局部循环仅限 intent_agent_subgraph 内

- **第四轮正式基线**：P7，已封板
  - 预算治理、统一回退矩阵、观测聚合（最小统一观测面）、压测与故障注入基线；不改三图、不扩 executor、不新增 Agent

- **第五轮正式基线**：P8，已封板
  - subgraph/local loop 全量 ledger 聚合（补全 P7 观测缺口）；三图全链路均可跨请求量化

- **第六轮正式基线**：P9，已封板
  - code_agent 受控 ReAct 化（CODE_AGENT_REACT_ENABLED 开关、react_scratchpad、code_agent_trace 契约固化）

- **第七轮正式基线**：P10，已封板
  - translate_agent 受控 ReAct 化（TRANSLATE_AGENT_REACT_ENABLED 开关、translate_agent_trace 契约固化）

- **第八轮正式基线**：P11，已封板
  - retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式，retrieval_agent_trace 契约固化，allowlist 扩展）

已冻结架构事实（与 REFACTOR 一致）：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治
2. **executor 为通用执行器**，不承载业务分派或业务循环语义
3. **Supervised Graph** 主链仍为 supervisor_dispatch → agent_dispatch → judge_agent → renderer
4. **Intent 图** 外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 及 P7 预算/回退/观测契约已固化
6. **P8 新增 ledger 聚合**（p8_subgraph_failure_log / p8_graph_run_log）及 P8_EXTENDED_METRICS_ENABLED 开关已固化
7. **P9 新增 code_agent ReAct 双模式**（CODE_AGENT_REACT_ENABLED 开关、code_agent_trace 契约）已固化
8. **P10 新增 translate_agent ReAct 双模式**（TRANSLATE_AGENT_REACT_ENABLED 开关、translate_agent_trace 契约）已固化
9. **P11 新增 retrieval_agent legacy_chain 接入**（retrieval_agent_trace 契约，7 个字段，react_enabled=False 预留）已固化
10. **P1～P11 已全部封板**，后续工作不得以旧阶段补丁名义混入

---

## 1. 项目阶段状态

| 阶段 | 状态 |
|------|------|
| P1～P4 | 完成并正式验收通过 |
| P5 | 完成并正式验收通过（2026-03 封板） |
| P6 | 完成并正式验收通过（2026-03 封板） |
| P7 | 完成并正式验收通过（2026-03 封板） |
| P8 | 完成并正式验收通过（2026-03 封板） |
| P9 | 完成并正式验收通过（2026-03 封板） |
| P10 | 完成并正式验收通过（2026-03 封板） |
| P11 | 完成并正式验收通过（2026-03 封板） |
| **P12** | **定稿/待实施** |

---

## 2. P12 唯一目标（窄定义）— 定稿

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，将 `retrieval_agent_handler` 从 legacy_chain 模式升级为**受控 ReAct 模式**（引入 `RETRIEVAL_AGENT_REACT_ENABLED` 开关，双模式共存），并补全对应的验收测试与契约文档。

### 2.1 选择 P12 方向的理由

P11 已将 `retrieval_agent` 以 legacy_chain 模式最小接入 Supervised Graph，并在 `retrieval_agent_trace` 中预留了 `react_enabled` 字段（固定为 `False`）。P11 冻结非目标中明确指出：**retrieval_agent 的 ReAct 化留给 P12**。

P9（code_agent ReAct 化）和 P10（translate_agent ReAct 化）已验证了受控 ReAct 双模式的可行性与安全性。P12 沿用相同模式，将 `retrieval_agent` 升级为 ReAct 模式，完成三个专家 Agent 的 ReAct 化闭环。

**为什么 P12 只做 ReAct 化，不做 RAG 深度集成**：
- 遵循"先收口再扩能力"总原则：先让 retrieval_agent 具备 ReAct 能力，验证三图主链不退化
- RAG 全链路（记忆、向量库深度集成）属于能力扩张，留给 P13 或更晚阶段
- 避免 P12 同时承载"ReAct 化 + RAG 全链路"两个目标

### 2.2 现状说明（重要）

`retrieval_agent_handler.py` 当前为 **legacy_chain 模式**（P11 封板状态）：

- `react_enabled` 字段固定为 `False`（预留字段，P11 未实现 ReAct 骨架）
- 无 `react_scratchpad` 字段
- 无工具循环（legacy_chain 不调用工具循环）
- `RETRIEVAL_AGENT_REACT_ENABLED` 开关**不存在**

**P12 需要新增/修改的内容**：
1. 修改 `retrieval_agent_handler.py`：新增 ReAct 模式分支（`RETRIEVAL_AGENT_REACT_ENABLED=true` 时走 ReAct 路径，`false` 时走 legacy_chain 路径）
2. 引入 `RETRIEVAL_AGENT_REACT_ENABLED` 环境变量开关（默认 `false`，保持 P11 基线行为）
3. 扩展 `retrieval_agent_trace` 契约：新增 `react_scratchpad` 字段（ReAct 模式专用，legacy 模式为 `None`）
4. 补全测试与文档

**参照模板**：`code_agent_handler.py`（P9 封板状态，双模式共存，`CODE_AGENT_REACT_ENABLED` 开关）

### 2.3 纳入范围（P12 唯一目标）

**P12-1：retrieval_agent 受控 ReAct 化（双模式共存）**

| 子项 | 说明 |
|------|------|
| P12-1a | 修改 `retrieval_agent_handler.py`：新增 ReAct 模式分支；`RETRIEVAL_AGENT_REACT_ENABLED=true` 时走 ReAct 路径（工具循环），`false` 时走 legacy_chain 路径（P11 行为不变） |
| P12-1b | 引入 `RETRIEVAL_AGENT_REACT_ENABLED` 环境变量开关（默认 `false`） |
| P12-1c | 扩展 `retrieval_agent_trace` 契约：新增 `react_scratchpad` 字段（ReAct 模式专用，legacy 模式为 `None`） |
| P12-1d | 更新 `.env.example`：新增 `RETRIEVAL_AGENT_REACT_ENABLED` 说明 |
| P12-1e | 补全 `tests/unit/test_retrieval_agent_handler.py`：新增 ReAct 模式用例（开关行为 + react_scratchpad 契约 + 异常安全） |
| P12-1f | 补全 `tests/integration/test_p12_integration.py`（ReAct 路径验证 + P11 基线不退化 + 文件完整性） |
| P12-1g | 封板文档（P12_SEAL_RESULT） |

### 2.4 范围外（硬约束）

P12 **不得**：

- 破坏 P1～P11 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 将 RAG 全链路（记忆、向量库深度集成）纳入 P12
- 扩展 executor 业务语义（executor 保持通用执行器定位）
- 将多 Agent 循环 handoff、并行 agent 纳入 P12
- 以 P12 名义回改 REFACTOR 三条硬性约束或八轮基线定义
- 将未在 P12_SCOPE 明确纳入的能力以"顺带""优化"名义混入
- 修改 `code_agent_handler.py`（P9 已封板）
- 修改 `translate_agent_handler.py`（P10 已封板）

---

## 3. P12 验收标准（定稿，共 3 条）

实施完成后须逐条满足以下验收条件：

### 3.1 RETRIEVAL_AGENT_REACT_ENABLED=true 时 ReAct 路径可运行

- `RETRIEVAL_AGENT_REACT_ENABLED=true` 时，`retrieval_agent_handler` 走 ReAct 路径（工具循环）
- `retrieval_agent_trace.react_enabled` 为 `True`
- `retrieval_agent_trace.react_scratchpad` 非 `None`，包含至少一个 step 记录
- `agent_artifacts["retrieval_agent"]` 存在且 `artifact_ref` 非空
- 有对应单元测试验证上述字段

### 3.2 RETRIEVAL_AGENT_REACT_ENABLED=false 时 P11 基线（legacy_chain）不退化

- `RETRIEVAL_AGENT_REACT_ENABLED=false`（默认值）时，`retrieval_agent_handler` 行为与 P11 封板状态完全一致
- `retrieval_agent_trace.react_enabled` 为 `False`
- `retrieval_agent_trace.react_scratchpad` 为 `None`（legacy 模式不写 scratchpad）
- P11 集成测试（`test_p11_integration.py`）全部通过
- `code_agent_handler`（P9 已封板）行为不变
- `translate_agent_handler`（P10 已封板）行为不变
- 三图主链结构（节点与边）未被修改
- 有对应集成测试验证 P11 基线不退化

### 3.3 retrieval_agent_trace 字段契约扩展固化

`retrieval_agent_trace` 字段结构已扩展并写死，实现不得自行增减必填字段：

**Legacy 模式（`react_enabled=False`，P11 行为不变）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"retrieval_agent"` |
| `mode` | str | 固定为 `"legacy_chain"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `retrieval_source` | str | 检索来源标识，如 `"knowledge_base"`、`"vector_store"` 或 `"mock"` |
| `result_count` | int | 检索结果条数，≥ 0 |
| `react_enabled` | bool | 固定为 `False` |
| `react_scratchpad` | None | legacy 模式固定为 `None` |
| `ts_ms` | int | Unix 毫秒时间戳 |

**ReAct 模式（`react_enabled=True`，P12 新增）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"retrieval_agent"` |
| `mode` | str | 固定为 `"react"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `retrieval_source` | str | 检索来源标识 |
| `result_count` | int | 检索结果条数，≥ 0 |
| `react_enabled` | bool | 固定为 `True` |
| `react_scratchpad` | list[dict] | 非 `None`，至少包含一个 step 记录；每个 step 含 `step`(int)、`action`(str)、`observation`(str) |
| `ts_ms` | int | Unix 毫秒时间戳 |

> **注意**：P12 在 P11 契约基础上新增 `react_scratchpad` 字段（legacy 模式为 `None`，ReAct 模式为 list）。P11 已有的 7 个字段语义不变，P12 只做向后兼容扩展。

---

## 4. 实现约束（定稿）

### 4.1 retrieval_agent_handler 改造要求（已写死）

| 参数 | 值 | 说明 |
|------|----|------|
| 开关 | `RETRIEVAL_AGENT_REACT_ENABLED`（默认 `false`） | 与 P9/P10 模式一致；`false` 时走 legacy_chain，`true` 时走 ReAct |
| legacy_chain 行为 | 与 P11 封板状态完全一致 | 不得修改 P11 已固化的 legacy_chain 逻辑 |
| ReAct 路径 | 参照 `code_agent_handler.py` P9 封板状态 | 工具循环，写入 `react_scratchpad` |
| `react_scratchpad` 字段 | legacy 模式为 `None`，ReAct 模式为 list[dict] | 每个 step 含 `step`、`action`、`observation` |
| `system_prompt_extra` | `"你是检索专家，负责从知识库中提取相关信息。"` | 不得在 P12 中修改 |
| `AGENT_ID_RETRIEVAL` | `"retrieval_agent"` | 常量不变 |

### 4.2 开关行为约束

- `RETRIEVAL_AGENT_REACT_ENABLED=false`（默认）：行为与 P11 封板状态完全一致，`react_scratchpad=None`
- `RETRIEVAL_AGENT_REACT_ENABLED=true`：走 ReAct 路径，`react_scratchpad` 非 `None`
- 开关只影响 `retrieval_agent_handler` 内部路径，不影响三图主链结构
- 开关不影响 `code_agent_handler`（P9）和 `translate_agent_handler`（P10）的行为

### 4.3 与 P8/P9/P10/P11 观测的兼容性

- P8 新增的 `p8_subgraph_failure_log`、`p8_graph_run_log` ledger 表不受影响
- `P8_EXTENDED_METRICS_ENABLED` 开关行为不变
- P9 新增的 `code_agent_trace` 契约不受影响
- P10 新增的 `translate_agent_trace` 契约不受影响
- P11 新增的 `retrieval_agent_trace` 7 个字段语义不变（P12 只做向后兼容扩展）

### 4.4 不破坏 P1～P11 契约

- 不改动 `graph_contract.py` 中 P1～P11 已定义的字段语义
- 不改动三图 builder 的节点与边
- 不改动 `execution_profile` 的写入逻辑与语义
- 不改动 `code_agent_handler.py`（P9 已封板）
- 不改动 `translate_agent_handler.py`（P10 已封板）
- 不改动 `agent_dispatch_handler.py` 的 allowlist（P11 已扩展，P12 不需要再改）
- 不改动 `supervisor_dispatch_handler.py` 的候选 agent 列表（P11 已扩展，P12 不需要再改）

---

## 5. 冻结非目标（不纳入 P12）

以下事项**不属于 P12**，不得以任何名义混入：

- RAG 全链路（记忆、向量库深度集成）
- 全局 ReAct 全量默认开启
- 多 Agent 循环 handoff 无界扩展
- 并行 agent 试点
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 前端改造
- P8 ledger 聚合逻辑的扩展或修改
- P9 `code_agent_trace` 契约的修改
- P10 `translate_agent_trace` 契约的修改
- P11 `retrieval_agent_trace` 已有 7 个字段语义的修改（P12 只做向后兼容扩展）
- P13 或更晚阶段能力

---

## 6. 文件改动清单（P12 最小实现面）

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/handlers/retrieval_agent_handler.py` | 新增 ReAct 模式分支（`RETRIEVAL_AGENT_REACT_ENABLED` 开关）；扩展 `retrieval_agent_trace` 新增 `react_scratchpad` 字段 |
| `.env.example` | 新增 `RETRIEVAL_AGENT_REACT_ENABLED=false` 说明 |
| `tests/unit/test_retrieval_agent_handler.py` | 新增 ReAct 模式用例（开关行为 + react_scratchpad 契约 + 异常安全） |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/integration/test_p12_integration.py` | P12 集成测试（ReAct 路径验证 + P11 基线不退化 + 文件完整性） |

### 不需要修改的文件（已就绪）

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/agent_dispatch_handler.py` | P11 已扩展 allowlist，P12 不需要再改 |
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | P11 已扩展候选列表，P12 不需要再改 |
| `host_brain/app/handlers/code_agent_handler.py` | P9 已封板，P12 不改动 |
| `host_brain/app/handlers/translate_agent_handler.py` | P10 已封板，P12 不改动 |
| `host_brain/app/agents/react_loop.py` | P12 复用现有 ReAct 循环，不改动 |
| `host_brain/app/agents/tool_registry.py` | P12 复用现有工具注册，不改动 |

> **P12 与 P9/P10 的关键相似点**：P12 是对已有 handler（P11 封板状态）做 ReAct 化改造，与 P9（code_agent）、P10（translate_agent）的改造模式完全一致。改动面极窄（只改 `retrieval_agent_handler.py` + 测试文件 + `.env.example`）。

---

## 7. 与现有文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、阶段顺序与文档关系仍以此为总表；P12 作为第九轮正式基线候选，范围与验收以本文档为准。
- **MULTI_AGENT_P11_SCOPE_2026-03.md**：P11 已封板，P12 以 P11 为基线前提，不回改 P11 结论。
- **MULTI_AGENT_P11_SEAL_RESULT_2026-03.md**：P12 不回改 P11 封板结论；P12 新增测试须与 P11 集成测试共存，不退化。
- **MULTI_AGENT_P9_SCOPE_2026-03.md**：P12 ReAct 化模式参照 P9（code_agent ReAct 化）的实现模式。
- **MULTI_AGENT_P10_SCOPE_2026-03.md**：P12 ReAct 化模式参照 P10（translate_agent ReAct 化）的实现模式。

若 P12_SCOPE 与其它文档在 P12 范围上冲突，以本文档为准；若涉及 P1～P11 已封板事实，以对应 seal result 为准。

---

## 8. 结论（待实施）

- **P1～P11 已全部形成完整封板链路。**
- **P12 唯一目标**：retrieval_agent 受控 ReAct 化（RETRIEVAL_AGENT_REACT_ENABLED 开关，双模式共存，P12-1）。
- **3 条验收标准**：待实施后逐条验证。
- **第九轮正式基线候选 = P12**，待实施并验收通过后封板。

---

## 变更记录

- 2026-03-17 v1.0：定稿。P12 唯一目标确定为「retrieval_agent 受控 ReAct 化」；3 条验收标准写死；实现约束、文件改动清单、冻结非目标补全。
