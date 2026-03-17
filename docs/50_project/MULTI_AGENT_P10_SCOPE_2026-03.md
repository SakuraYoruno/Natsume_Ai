# P10：下一轮候选基线 — 范围与验收标准

**日期**：2026-03
**状态**：完成并正式验收通过（2026-03 封板）
**定位**：在 P1～P9 全部封板的前提下，将 `translate_agent` 纳入受控 ReAct 循环，不破坏六轮基线
**总原则**：先定范围与验收再实施；不把未在 P10_SCOPE 明确纳入的能力混入 P10；不得回改已封板结论。

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P10 中不回改：

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

已冻结架构事实（与 REFACTOR 一致）：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治
2. **executor 为通用执行器**，不承载业务分派或业务循环语义
3. **Supervised Graph** 主链仍为 supervisor_dispatch → agent_dispatch → judge_agent → renderer
4. **Intent 图** 外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 及 P7 预算/回退/观测契约已固化
6. **P8 新增 ledger 聚合**（p8_subgraph_failure_log / p8_graph_run_log）及 P8_EXTENDED_METRICS_ENABLED 开关已固化
7. **P9 新增 code_agent ReAct 双模式**（CODE_AGENT_REACT_ENABLED 开关、code_agent_trace 契约）已固化
8. **P1～P9 已全部封板**，后续工作不得以旧阶段补丁名义混入

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
| **P10** | **完成并正式验收通过（2026-03 封板）** |

---

## 2. P10 唯一目标（窄定义）— 定稿

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，为 `translate_agent_handler` 开启**受控 ReAct 循环**（只读工具，`max_steps=3`，独立开关 `TRANSLATE_AGENT_REACT_ENABLED`），并补全对应的验收测试与契约文档。

### 2.1 现状说明（重要）

`translate_agent_handler.py` 当前**只有 legacy_chain 模式**：

- 读取 `workbench_translate` 节点输出，封装为 `AgentArtifact`
- 写入 `translate_agent_trace`（仅含 `agent_id`、`artifact_ref`、`gateway_status`、`ts_ms`）
- **无 ReAct 骨架**，无 `mode` 字段，无 `react_enabled` 字段

**P10 与 P9 的关键差异**：
- P9 的 `code_agent_handler.py` 在 M-MA6-FIX 阶段已实现 ReAct 双模式骨架，P9 只补测试
- P10 的 `translate_agent_handler.py` 当前无 ReAct 骨架，**P10 需要先改造 handler，再补测试**
- 改造参照模板：`code_agent_handler.py`（P9 已封板）

### 2.2 纳入范围（P10 唯一目标）

**P10-1：translate_agent 受控 ReAct 化（handler 改造 + 验收 + 契约固化 + 封板）**

| 子项 | 说明 |
|------|------|
| P10-1a | 改造 `translate_agent_handler.py`：新增 ReAct 双模式（`TRANSLATE_AGENT_REACT_ENABLED` 开关），参照 `code_agent_handler.py` 模板 |
| P10-1b | 固化 `translate_agent_trace` 字段契约（字段名、类型、必填项，与 `code_agent_trace` 对称） |
| P10-1c | 确认 `get_tools_for_agent(AGENT_ID_TRANSLATE, allow_side_effects=False)` 工具集（只读，翻译场景） |
| P10-1d | 补全 `tests/unit/test_translate_agent_handler.py`（ReAct 模式 + legacy 模式 + 开关行为） |
| P10-1e | 补全 `tests/integration/test_p10_integration.py`（Supervised Graph 主链含 translate_agent ReAct 模式不退化） |
| P10-1f | 封板文档（P10_SEAL_RESULT） |

### 2.3 范围外（硬约束）

P10 **不得**：

- 破坏 P1～P9 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 扩展 executor 业务语义（executor 保持通用执行器定位）
- 将全局 ReAct 默认开启纳入 P10
- 将多 Agent 循环 handoff、并行 agent、记忆/RAG 等纳入 P10
- 以 P10 名义回改 REFACTOR 三条硬性约束或六轮基线定义
- 将未在 P10_SCOPE 明确纳入的能力以"顺带""优化"名义混入

---

## 3. P10 验收标准（定稿，共 3 条）

实施完成后须逐条满足以下验收条件：

### 3.1 TRANSLATE_AGENT_REACT_ENABLED=true 时 ReAct 循环可运行

- `TRANSLATE_AGENT_REACT_ENABLED=true` 时，`translate_agent_handler` 调用 `run_react_loop()`
- `react_scratchpad.steps` 非空（至少 1 步）
- `translate_agent_trace.stopped_reason` 为有效值：`FINAL_ANSWER`、`MAX_STEPS`、`ERROR` 之一
- `translate_agent_trace.mode == "react_loop"`
- `agent_artifacts[translate_agent]` 存在且 `artifact_ref` 非空
- 有对应单元测试验证上述字段

### 3.2 TRANSLATE_AGENT_REACT_ENABLED=false 时行为与 P9 基线完全一致（不退化）

- `TRANSLATE_AGENT_REACT_ENABLED=false`（默认）时，`translate_agent_handler` 走 legacy_chain
- `translate_agent_trace.mode == "legacy_chain"`
- `react_scratchpad` 不写入（或为空）
- `agent_artifacts[translate_agent]` 存在且 `artifact_ref` 非空
- P9 基线测试（三图主链不退化）全部通过
- 有对应单元测试验证开关关闭时的行为

### 3.3 translate_agent_trace 字段契约固化

`translate_agent_trace` 字段结构已写死，实现不得自行增减必填字段：

**ReAct 模式（`react_enabled=True`）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"translate_agent"` |
| `mode` | str | 固定为 `"react_loop"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `total_steps` | int | ≥ 1 |
| `stopped_reason` | str | `FINAL_ANSWER` / `MAX_STEPS` / `ERROR` 之一 |
| `react_enabled` | bool | 固定为 `True` |
| `ts_ms` | int | Unix 毫秒时间戳 |

**Legacy 模式（`react_enabled=False`）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"translate_agent"` |
| `mode` | str | 固定为 `"legacy_chain"` |
| `artifact_ref` | str | 非空 |
| `gateway_status` | str | `OK` 或错误码 |
| `react_enabled` | bool | 固定为 `False` |
| `ts_ms` | int | Unix 毫秒时间戳 |

> **注意**：P10 对 `translate_agent_trace` 的 legacy 模式新增了 `mode` 和 `react_enabled` 两个字段，与 P9 之前的旧格式（仅含 `agent_id`、`artifact_ref`、`gateway_status`、`ts_ms`）不同。这是 P10 的**有意扩展**，不属于回改 P9 契约（P9 未固化 `translate_agent_trace` 格式）。

---

## 4. 实现约束（定稿）

### 4.1 ReAct 循环参数（已写死，不得在实现中自行解释）

| 参数 | 值 | 说明 |
|------|----|------|
| `allow_side_effects` | `False` | 翻译场景无副作用，只读工具 |
| `max_steps` | `3`（默认）| 可通过 `TRANSLATE_AGENT_REACT_MAX_STEPS` 环境变量覆盖，但测试以 3 为基准 |
| 工具集 | `get_tools_for_agent(AGENT_ID_TRANSLATE, allow_side_effects=False)` | 只读工具，不含副作用工具 |
| `system_prompt_extra` | `"你是翻译专家，负责生成高质量的翻译草案。"` | 不得在 P10 中修改 |

### 4.2 与 P8/P9 观测的兼容性

- P8 新增的 `p8_subgraph_failure_log`、`p8_graph_run_log` ledger 表不受影响
- `P8_EXTENDED_METRICS_ENABLED` 开关行为不变
- `translate_agent_handler` 不直接写 P8 ledger 表（由 `graph_runtime` 统一写入）
- P9 新增的 `code_agent_trace` 契约不受影响

### 4.3 可关闭 / 可灰度

- `TRANSLATE_AGENT_REACT_ENABLED` 默认 `false`，不影响现有链路
- 开关关闭时，行为与 P9 基线完全一致
- 开关开启时，走 ReAct 循环，`react_scratchpad` 写入 state

### 4.4 不破坏 P1～P9 契约

- 不改动 `graph_contract.py` 中 P1～P9 已定义的字段语义
- 不改动三图 builder 的节点与边
- 不改动 `execution_profile` 的写入逻辑与语义
- 不改动 `agent_dispatch_handler` 的 allowlist 与分派逻辑
- 不改动 `supervisor_dispatch_handler` 的分派逻辑
- 不改动 `code_agent_handler.py`（P9 已封板）

---

## 5. 冻结非目标（不纳入 P10）

以下事项**不属于 P10**，不得以任何名义混入：

- 全局 ReAct 全量默认开启
- 多 Agent 循环 handoff 无界扩展
- 新 Agent 类型（如 retrieval_agent ReAct）
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 记忆 / RAG / 新业务场景 / 前端改造等
- P8 ledger 聚合逻辑的扩展或修改
- P9 code_agent_trace 契约的修改
- P11 或更晚阶段能力

---

## 6. 文件改动清单（P10 最小实现面）

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/handlers/translate_agent_handler.py` | 新增 ReAct 双模式（`TRANSLATE_AGENT_REACT_ENABLED` 开关），参照 `code_agent_handler.py` |
| `.env.example` | 新增 `TRANSLATE_AGENT_REACT_ENABLED=false` 和 `TRANSLATE_AGENT_REACT_MAX_STEPS=3` 说明 |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/unit/test_translate_agent_handler.py` | P10 专项单元测试（验收 3.1 + 3.2 + 3.3 + 开关行为） |
| `tests/integration/test_p10_integration.py` | P10 集成测试（Supervised Graph 含 translate_agent ReAct 模式不退化 + P9 基线不退化 + 异常安全） |

### 不需要修改的文件（已就绪）

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/agents/react_loop.py` | P6 已封板，P10 不改动 |
| `host_brain/app/agents/tool_registry.py` | P10 只确认工具集，不改动注册逻辑 |
| `host_brain/app/handlers/code_agent_handler.py` | P9 已封板，P10 不改动 |

> **P10 与 P9 的关键区别**：P9 的 handler 已就绪，只补测试；P10 需要先改造 `translate_agent_handler.py`，再补测试。改造量约等于 `code_agent_handler.py` 的 ReAct 部分，参照模板直接实施即可。

---

## 7. 与现有文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、阶段顺序与文档关系仍以此为总表；P10 作为第七轮正式基线候选，范围与验收以本文档为准。
- **MULTI_AGENT_P9_SCOPE_2026-03.md**：P9 已封板，P10 以 P9 为基线前提，不回改 P9 结论。
- **MULTI_AGENT_P9_SEAL_RESULT_2026-03.md**：P10 不回改 P9 封板结论；P10 新增测试须与 P9 集成测试共存，不退化。
- **MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md**：M-MA5 §5 已记录 `translate_agent_handler` 原始实现；P10 以此为改造起点，参照 M-MA6-FIX §13 的 ReAct 接入方案。
- **MULTI_AGENT_P6_SEAL_RESULT / react_loop 契约**：P10 的 ReAct 循环复用 P6 已封板的 `run_react_loop()`，不重新设计。

若 P10_SCOPE 与其它文档在 P10 范围上冲突，以本文档为准；若涉及 P1～P9 已封板事实，以对应 seal result 为准。

---

## 8. 结论（待实施）

- **P1～P9 已全部形成完整封板链路。**
- **P10 唯一目标**：translate_agent 受控 ReAct 化（P10-1）。
- **3 条验收标准**：实施完成后须 3/3 满足。
- **实现面极窄**：改造 1 个 handler + 新增 2 个测试文件 + 修改 `.env.example`。
- **参照模板**：`code_agent_handler.py`（P9 已封板）。
- **第七轮正式基线候选 = P10**，封板条件：3 条验收标准全部满足。

---

## 变更记录

- 2026-03-17 v1.0：定稿。P10 唯一目标确定为「translate_agent 受控 ReAct 化」；3 条验收标准写死；实现约束、文件改动清单、冻结非目标补全。
