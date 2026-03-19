受众：PROJECT
标题：P12：下一轮候选基线 — 范围与验收标准

# P12：下一轮候选基线 — 范围与验收标准

**日期**：2026-03
**状态**：完成并正式验收通过（2026-03 封板）
**定位**：在 P1～P11 全部封板的前提下，将 `retrieval_agent` 从 legacy_chain 模式**直接替换**为 ReAct 模式，不引入双模式开关，不破坏已封板基线
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
  - code_agent ReAct 化（react_scratchpad、code_agent_trace 契约固化）

- **第七轮正式基线**：P10，已封板
  - translate_agent ReAct 化（translate_agent_trace 契约固化）

- **第八轮正式基线**：P11，已封板
  - retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式，retrieval_agent_trace 契约固化，allowlist 扩展）

已冻结架构事实（与 REFACTOR 一致）：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治
2. **executor 为通用执行器**，不承载业务分派或业务循环语义
3. **Supervised Graph** 主链仍为 supervisor_dispatch → agent_dispatch → judge_agent → renderer
4. **Intent 图** 外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 及 P7 预算/回退/观测契约已固化
6. **P8 新增 ledger 聚合**（p8_subgraph_failure_log / p8_graph_run_log）及 P8_EXTENDED_METRICS_ENABLED 开关已固化
7. **P9 新增 code_agent ReAct 模式**（code_agent_trace 契约）已固化
8. **P10 新增 translate_agent ReAct 模式**（translate_agent_trace 契约）已固化
9. **P11 新增 retrieval_agent legacy_chain 接入**（retrieval_agent_trace 契约，6 个字段）已固化
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
| **P12** | **完成并正式验收通过（2026-03 封板）** |

---

## 2. P12 唯一目标（窄定义）— 定稿

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，将 `retrieval_agent_handler` 从 legacy_chain 模式**直接替换**为 ReAct 模式，不引入双模式开关，补全对应的验收测试与契约文档。

### 2.1 选择直接替换而非双模式的理由

P9/P10 引入了双模式开关（`CODE_AGENT_REACT_ENABLED`、`TRANSLATE_AGENT_REACT_ENABLED`），这是为了在生产环境灰度验证 ReAct 路径的安全性。但该项目**尚未正式上线**，双模式开关的核心价值（生产灰度 + 快速回滚）不存在，保留 if/else 分支只会增加代码复杂度和维护成本。

因此 P12 决策：
- **直接替换**：`retrieval_agent_handler` 只保留 ReAct 路径，删除 legacy_chain 分支
- **不引入开关**：不新增 `RETRIEVAL_AGENT_REACT_ENABLED` 环境变量
- **同步清理**：P9/P10 的双模式开关（`CODE_AGENT_REACT_ENABLED`、`TRANSLATE_AGENT_REACT_ENABLED`）也已在 P12 前统一清理，三个 handler 均为纯 ReAct 模式

### 2.2 现状说明（重要）

`retrieval_agent_handler.py` 当前为 **legacy_chain 模式**（P11 封板状态）：

- 无 `react_scratchpad` 字段
- 无工具循环（legacy_chain 不调用工具循环）
- `retrieval_agent_trace` 包含 6 个字段（`agent_id`、`mode`、`artifact_ref`、`retrieval_source`、`result_count`、`ts_ms`）

**P12 需要修改的内容**：
1. 改造 `retrieval_agent_handler.py`：删除 legacy_chain 逻辑，接入 `run_react_loop`，写入 `react_scratchpad`
2. 更新 `retrieval_agent_trace` 契约：`mode="react_loop"`，新增 `total_steps`、`stopped_reason`，写入 `react_scratchpad`
3. 补全测试与文档

**参照模板**：`code_agent_handler.py`（P12 清理后状态，纯 ReAct 模式）

### 2.3 纳入范围（P12 唯一目标）

**P12-1：retrieval_agent ReAct 化（直接替换）**

| 子项 | 说明 |
|------|------|
| P12-1a | 改造 `retrieval_agent_handler.py`：删除 legacy_chain 逻辑，接入 `run_react_loop`（只读工具，`allow_side_effects=False`） |
| P12-1b | 更新 `retrieval_agent_trace` 契约：`mode="react_loop"`，新增 `total_steps`、`stopped_reason`，写入 `react_scratchpad` |
| P12-1c | 更新 `tests/unit/test_retrieval_agent_handler.py`：改造为 ReAct 模式用例（react_scratchpad 契约 + 异常安全） |
| P12-1d | 新增 `tests/integration/test_p12_integration.py`（ReAct 路径验证 + P11 基线不退化 + 文件完整性） |
| P12-1e | 封板文档（P12_SEAL_RESULT） |

### 2.4 范围外（硬约束）

P12 **不得**：

- 破坏 P1～P11 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 将 RAG 全链路（记忆、向量库深度集成）纳入 P12
- 扩展 executor 业务语义（executor 保持通用执行器定位）
- 将多 Agent 循环 handoff、并行 agent 纳入 P12
- 以 P12 名义回改 REFACTOR 三条硬性约束或八轮基线定义
- 将未在 P12_SCOPE 明确纳入的能力以"顺带""优化"名义混入

---

## 3. P12 验收标准（定稿，共 2 条）

实施完成后须逐条满足以下验收条件：

### 3.1 retrieval_agent_handler ReAct 路径可运行

- `retrieval_agent_handler` 走 ReAct 路径（工具循环）
- `retrieval_agent_trace.mode` 为 `"react_loop"`
- `react_scratchpad` 非 `None`，包含至少一个 step 记录
- `agent_artifacts["retrieval_agent"]` 存在且 `artifact_ref` 非空
- 有对应单元测试验证上述字段

### 3.2 P11 基线不退化

- P11 集成测试（`test_p11_integration.py`）全部通过
- `code_agent_handler`（P9 已封板）行为不变
- `translate_agent_handler`（P10 已封板）行为不变
- 三图主链结构（节点与边）未被修改
- 有对应集成测试验证 P11 基线不退化

---

## 4. retrieval_agent_trace 字段契约（P12 定稿）

`retrieval_agent_trace` 字段结构（P12 后唯一模式）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"retrieval_agent"` |
| `mode` | str | 固定为 `"react_loop"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `retrieval_source` | str | 检索来源标识，如 `"knowledge_base"` |
| `result_count` | int | 检索结果条数，≥ 0 |
| `total_steps` | int | ReAct 循环实际步数，≥ 1 |
| `stopped_reason` | str | 停止原因：`FINAL_ANSWER`、`MAX_STEPS`、`ERROR`、`NO_LLM` |
| `ts_ms` | int | Unix 毫秒时间戳 |

---

## 5. 实现约束（定稿）

### 5.1 retrieval_agent_handler 改造要求

| 参数 | 值 | 说明 |
|------|----|------|
| 模式 | 纯 ReAct 模式 | 无开关，无 legacy 分支 |
| 工具 | 只读工具，`allow_side_effects=False` | 与 code_agent/translate_agent 一致 |
| `react_scratchpad` | 写入 `out["react_scratchpad"]` | 供 JudgeAgent 和 trace 读取 |
| `system_prompt_extra` | `"你是检索专家，负责从知识库中提取相关信息。"` | 不得在 P12 中修改 |
| `AGENT_ID_RETRIEVAL` | `"retrieval_agent"` | 常量不变 |
| max_steps 配置 | `RETRIEVAL_AGENT_REACT_MAX_STEPS`（默认 3） | 与 code_agent/translate_agent 模式一致 |

### 5.2 与 P8/P9/P10/P11 观测的兼容性

- P8 新增的 ledger 表不受影响
- P9 新增的 `code_agent_trace` 契约不受影响
- P10 新增的 `translate_agent_trace` 契约不受影响
- P11 新增的 `retrieval_agent_trace` 6 个字段中，`agent_id`、`artifact_ref`、`retrieval_source`、`result_count`、`ts_ms` 语义不变；`mode` 从 `"legacy_chain"` 改为 `"react_loop"`（P12 唯一破坏性变更，可接受，因未上线）

---

## 6. 文件改动清单（P12 最小实现面）

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/handlers/retrieval_agent_handler.py` | 删除 legacy_chain 逻辑，接入 `run_react_loop`，更新 trace 契约 |
| `.env.example` | 新增 `RETRIEVAL_AGENT_REACT_MAX_STEPS=3` 说明 |
| `tests/unit/test_retrieval_agent_handler.py` | 改造为 ReAct 模式用例 |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/integration/test_p12_integration.py` | P12 集成测试 |

### 不需要修改的文件（已就绪）

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/agent_dispatch_handler.py` | P11 已扩展 allowlist，P12 不需要再改 |
| `host_brain/app/handlers/code_agent_handler.py` | P9 已封板（P12 前已清理双模式），P12 不改动 |
| `host_brain/app/handlers/translate_agent_handler.py` | P10 已封板（P12 前已清理双模式），P12 不改动 |
| `host_brain/app/agents/react_loop.py` | P12 复用现有 ReAct 循环，不改动 |
| `host_brain/app/agents/tool_registry.py` | P12 复用现有工具注册，不改动 |

---

## 7. 冻结非目标（不纳入 P12）

以下事项**不属于 P12**，不得以任何名义混入：

- RAG 全链路（记忆、向量库深度集成）
- 多 Agent 循环 handoff 无界扩展
- 并行 agent 试点
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 前端改造
- P8 ledger 聚合逻辑的扩展或修改
- P9 `code_agent_trace` 契约的修改
- P10 `translate_agent_trace` 契约的修改
- P13 或更晚阶段能力

---

## 8. 结论（待实施）

- **P1～P11 已全部形成完整封板链路。**
- **P12 唯一目标**：retrieval_agent 直接替换为 ReAct 模式（无开关，无双模式）。
- **2 条验收标准**：待实施后逐条验证。
- **第九轮正式基线候选 = P12**，待实施并验收通过后封板。

---

## 变更记录

- 2026-03-17 v1.0：定稿。P12 唯一目标确定为「retrieval_agent ReAct 化」；原双模式方案调整为直接替换，不引入开关；同步记录 P9/P10 双模式清理结论。
