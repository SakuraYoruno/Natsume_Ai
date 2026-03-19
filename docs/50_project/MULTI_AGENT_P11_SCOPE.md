受众：PROJECT
标题：P11：下一轮候选基线 — 范围与验收标准

# P11：下一轮候选基线 — 范围与验收标准

**日期**：2026-03
**状态**：完成并正式验收通过（2026-03 封板）
**定位**：在 P1～P10 全部封板的前提下，将 `retrieval_agent` 以 legacy_chain 模式最小接入 Supervised Graph，不破坏七轮基线
**总原则**：先定范围与验收再实施；不把未在 P11_SCOPE 明确纳入的能力混入 P11；不得回改已封板结论。

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P11 中不回改：

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

已冻结架构事实（与 REFACTOR 一致）：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治
2. **executor 为通用执行器**，不承载业务分派或业务循环语义
3. **Supervised Graph** 主链仍为 supervisor_dispatch → agent_dispatch → judge_agent → renderer
4. **Intent 图** 外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 及 P7 预算/回退/观测契约已固化
6. **P8 新增 ledger 聚合**（p8_subgraph_failure_log / p8_graph_run_log）及 P8_EXTENDED_METRICS_ENABLED 开关已固化
7. **P9 新增 code_agent ReAct 双模式**（CODE_AGENT_REACT_ENABLED 开关、code_agent_trace 契约）已固化
8. **P10 新增 translate_agent ReAct 双模式**（TRANSLATE_AGENT_REACT_ENABLED 开关、translate_agent_trace 契约）已固化
9. **P1～P10 已全部封板**，后续工作不得以旧阶段补丁名义混入

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
| **P11** | **完成并正式验收通过（2026-03 封板）** |

---

## 2. P11 唯一目标（窄定义）— 定稿

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，新增 `retrieval_agent_handler`，以 **legacy_chain 模式**将 `retrieval_agent` 最小接入 Supervised Graph（`agent_dispatch_handler` allowlist 扩展），并补全对应的验收测试与契约文档。

### 2.1 选择 P11 方向的理由

P9 和 P10 分别完成了 code_agent 和 translate_agent 的 ReAct 化。Supervised Graph 目前只有两个专家 Agent（code_agent、translate_agent）。`retrieval_agent` 是 P10 冻结非目标中明确列出的候选方向，且 handlers 目录中尚无 `retrieval_agent_handler.py`。

**为什么先做 legacy_chain 而不是直接做 ReAct**：
- 遵循"先收口再扩能力"总原则：先让 retrieval_agent 以最小面接入 Supervised Graph，验证三图主链不退化
- legacy_chain 接入面极窄（参照 translate_agent_handler.py P10 改造前的状态），风险可控
- ReAct 化留给 P12，避免 P11 同时承载"新增 Agent + ReAct 化"两个目标

### 2.2 现状说明（重要）

`retrieval_agent_handler.py` 当前**不存在**：

- handlers 目录中无此文件
- `agent_dispatch_handler.py` 的 allowlist 中无 `retrieval_agent`
- `supervisor_dispatch_handler.py` 的 proposal 中无 `retrieval_agent` 作为可选 agent
- `graph_contract.py` 中无 `retrieval_agent_trace` 字段

**P11 需要新增的内容**：
1. 新建 `retrieval_agent_handler.py`（legacy_chain 模式，无 ReAct 骨架）
2. 扩展 `agent_dispatch_handler.py` allowlist，加入 `retrieval_agent`
3. 扩展 `supervisor_dispatch_handler.py`，使 Supervisor 可将 `retrieval_agent` 纳入 proposal 候选
4. 固化 `retrieval_agent_trace` 字段契约
5. 补全测试与文档

**参照模板**：`translate_agent_handler.py`（P10 改造前的 legacy_chain 状态，即只有 legacy_chain 模式，无 ReAct 骨架）

### 2.3 纳入范围（P11 唯一目标）

**P11-1：retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式）**

| 子项 | 说明 |
|------|------|
| P11-1a | 新建 `retrieval_agent_handler.py`：legacy_chain 模式，读取检索结果，封装为 `AgentArtifact`，写入 `retrieval_agent_trace` |
| P11-1b | 固化 `retrieval_agent_trace` 字段契约（字段名、类型、必填项） |
| P11-1c | 扩展 `agent_dispatch_handler.py` allowlist：加入 `AGENT_ID_RETRIEVAL` → `retrieval_agent_handler` 映射 |
| P11-1d | 扩展 `supervisor_dispatch_handler.py`：将 `retrieval_agent` 纳入 Supervisor proposal 候选 agent 列表 |
| P11-1e | 补全 `tests/unit/test_retrieval_agent_handler.py`（legacy_chain 模式 + trace 契约 + 异常安全） |
| P11-1f | 补全 `tests/integration/test_p11_integration.py`（Supervised Graph 含 retrieval_agent 路径不退化 + P10 基线不退化 + 文件完整性） |
| P11-1g | 封板文档（P11_SEAL_RESULT） |

### 2.4 范围外（硬约束）

P11 **不得**：

- 破坏 P1～P10 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 为 `retrieval_agent` 添加 ReAct 骨架（ReAct 化留给 P12）
- 扩展 executor 业务语义（executor 保持通用执行器定位）
- 将多 Agent 循环 handoff、并行 agent、记忆/RAG 等纳入 P11
- 以 P11 名义回改 REFACTOR 三条硬性约束或七轮基线定义
- 将未在 P11_SCOPE 明确纳入的能力以"顺带""优化"名义混入

---

## 3. P11 验收标准（定稿，共 3 条）

实施完成后须逐条满足以下验收条件：

### 3.1 retrieval_agent 可在 Supervised Graph 内被调度并产出工件

- `supervisor_dispatch_handler` 可将 `active_agent_id = "retrieval_agent"` 写入 proposal
- `agent_dispatch_handler` 可将 `retrieval_agent` 分派到 `retrieval_agent_handler`
- `retrieval_agent_handler` 可产出 `AgentArtifact`，`artifact_ref` 非空
- `agent_artifacts["retrieval_agent"]` 存在且 `artifact_ref` 非空
- `retrieval_agent_trace` 写入 state，字段符合 §3.3 契约
- 有对应单元测试验证上述字段

### 3.2 P10 基线（三图主链 + code_agent/translate_agent 路径）不退化

- `code_agent_handler`（P9 已封板）行为不变
- `translate_agent_handler`（P10 已封板）行为不变
- `code_agent_trace` 契约不变
- `translate_agent_trace` 契约不变
- P10 集成测试（`test_p10_integration.py`）全部通过
- 三图主链结构（节点与边）未被修改
- 有对应集成测试验证 P10 基线不退化

### 3.3 retrieval_agent_trace 字段契约固化

`retrieval_agent_trace` 字段结构已写死，实现不得自行增减必填字段：

**Legacy 模式（`react_enabled=False`，P11 唯一模式）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"retrieval_agent"` |
| `mode` | str | 固定为 `"legacy_chain"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `retrieval_source` | str | 检索来源标识，如 `"knowledge_base"`、`"vector_store"` 或 `"mock"` |
| `result_count` | int | 检索结果条数，≥ 0 |
| `react_enabled` | bool | 固定为 `False`（P11 不做 ReAct，预留字段供 P12 扩展） |
| `ts_ms` | int | Unix 毫秒时间戳 |

> **注意**：P11 只实现 legacy_chain 模式，`react_enabled` 固定为 `False`。P12 若做 ReAct 化，可在此基础上扩展，不需要回改 P11 契约（与 P9/P10 的扩展模式一致）。

---

## 4. 实现约束（定稿）

### 4.1 retrieval_agent_handler 实现要求（已写死）

| 参数 | 值 | 说明 |
|------|----|------|
| 模式 | legacy_chain only | P11 不实现 ReAct 骨架 |
| 工具集 | 无（legacy_chain 不调用工具循环） | 检索结果由上游 workbench 节点或 mock 提供 |
| `system_prompt_extra` | `"你是检索专家，负责从知识库中提取相关信息。"` | 不得在 P11 中修改 |
| `AGENT_ID_RETRIEVAL` | `"retrieval_agent"` | 常量，与 allowlist 保持一致 |
| 开关 | 无独立开关（P11 不需要，legacy_chain 默认启用） | P12 做 ReAct 化时再引入 `RETRIEVAL_AGENT_REACT_ENABLED` |

### 4.2 agent_dispatch_handler allowlist 扩展规则

- 只在 `WORKFLOW_SUPERVISED` 图的 allowlist 中新增 `retrieval_agent`
- 不修改 `CHAT_FAST` 和 `INTENT_EXEC` 图的 allowlist
- allowlist 扩展必须通过 graph-local config 或 graph-local registry 完成，不得绕过 graph 语义边界

### 4.3 supervisor_dispatch_handler 扩展规则

- 只在 proposal 候选 agent 列表中新增 `retrieval_agent`
- 不修改 Supervisor 的决策逻辑（rule/llm 双模行为不变）
- 不修改 `supervisor_decision_log` / `supervisor_metrics` 的字段结构

### 4.4 与 P8/P9/P10 观测的兼容性

- P8 新增的 `p8_subgraph_failure_log`、`p8_graph_run_log` ledger 表不受影响
- `P8_EXTENDED_METRICS_ENABLED` 开关行为不变
- `retrieval_agent_handler` 不直接写 P8 ledger 表（由 `graph_runtime` 统一写入）
- P9 新增的 `code_agent_trace` 契约不受影响
- P10 新增的 `translate_agent_trace` 契约不受影响

### 4.5 不破坏 P1～P10 契约

- 不改动 `graph_contract.py` 中 P1～P10 已定义的字段语义
- 不改动三图 builder 的节点与边
- 不改动 `execution_profile` 的写入逻辑与语义
- 不改动 `code_agent_handler.py`（P9 已封板）
- 不改动 `translate_agent_handler.py`（P10 已封板）

---

## 5. 冻结非目标（不纳入 P11）

以下事项**不属于 P11**，不得以任何名义混入：

- `retrieval_agent` 的 ReAct 化（留给 P12）
- 全局 ReAct 全量默认开启
- 多 Agent 循环 handoff 无界扩展
- 并行 agent 试点
- 记忆 / RAG 深度集成（P11 只做最小检索接入，不做 RAG 全链路）
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 前端改造
- P8 ledger 聚合逻辑的扩展或修改
- P9 code_agent_trace 契约的修改
- P10 translate_agent_trace 契约的修改
- P12 或更晚阶段能力

---

## 6. 文件改动清单（P11 最小实现面）

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/retrieval_agent_handler.py` | retrieval_agent legacy_chain 模式 handler |
| `tests/unit/test_retrieval_agent_handler.py` | P11 专项单元测试（验收 3.1 + 3.3 + 异常安全） |
| `tests/integration/test_p11_integration.py` | P11 集成测试（Supervised Graph 含 retrieval_agent 路径 + P10 基线不退化 + 文件完整性） |

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/handlers/agent_dispatch_handler.py` | allowlist 新增 `AGENT_ID_RETRIEVAL` → `retrieval_agent_handler` 映射 |
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | proposal 候选 agent 列表新增 `retrieval_agent` |

### 不需要修改的文件（已就绪）

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/code_agent_handler.py` | P9 已封板，P11 不改动 |
| `host_brain/app/handlers/translate_agent_handler.py` | P10 已封板，P11 不改动 |
| `host_brain/app/agents/react_loop.py` | P11 不做 ReAct，不改动 |
| `host_brain/app/agents/tool_registry.py` | P11 不调用工具循环，不改动 |

> **P11 与 P9/P10 的关键区别**：P9/P10 是对已有 handler 做 ReAct 化改造；P11 是新增一个 handler（legacy_chain 模式），并扩展 allowlist 和 Supervisor 候选列表。改动面略宽（涉及 agent_dispatch_handler 和 supervisor_dispatch_handler），但每处改动量极小。

---

## 7. 与现有文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、阶段顺序与文档关系仍以此为总表；P11 作为第八轮正式基线候选，范围与验收以本文档为准。
- **MULTI_AGENT_P10_SCOPE_2026-03.md**：P10 已封板，P11 以 P10 为基线前提，不回改 P10 结论。
- **MULTI_AGENT_P10_SEAL_RESULT_2026-03.md**：P11 不回改 P10 封板结论；P11 新增测试须与 P10 集成测试共存，不退化。
- **MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md**：M-MA5 §5 已记录各 agent handler 原始实现；P11 以此为参照，新增 retrieval_agent_handler。

若 P11_SCOPE 与其它文档在 P11 范围上冲突，以本文档为准；若涉及 P1～P10 已封板事实，以对应 seal result 为准。

---

## 8. 结论（已封板）

- **P1～P11 已全部形成完整封板链路。**
- **P11 唯一目标**：retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式，P11-1）。✅ 已完成
- **3 条验收标准**：3/3 满足。✅
- **测试覆盖**：58/58 全部通过。✅
- **第八轮正式基线 = P11**，已正式验收通过（2026-03 封板）。
- **验收结论**：见 `MULTI_AGENT_P11_SEAL_RESULT_2026-03.md`。

---

## 变更记录

- 2026-03-17 v1.0：定稿。P11 唯一目标确定为「retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式）」；3 条验收标准写死；实现约束、文件改动清单、冻结非目标补全。
- 2026-03-17 v1.1：实施完成，正式验收通过，封板。58/58 测试全通过，3/3 验收标准满足。
