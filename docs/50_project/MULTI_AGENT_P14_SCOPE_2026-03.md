受众：PROJECT
标题：P14 SCOPE：judge_agent ReAct 化

# P14 SCOPE：judge_agent ReAct 化

**版本**：v1.0
**日期**：2026-03-17
**状态**：实施中
**基线**：P13（第十轮正式基线，85 用例全通过）

---

## 背景

P1～P13 已完成三专家 Agent（code_agent / translate_agent / retrieval_agent）的 ReAct 化，以及所有工具占位的真实落地。

P14 的目标是将 `judge_agent` 从固定链式裁判逻辑升级为 ReAct 模式，使其能够通过 LLM 推理选择最优工件，而不是纯数值比较。

---

## 1. 项目阶段状态

| 阶段 | 状态 |
|------|------|
| P1～P4 | 完成并正式验收通过 |
| P5～P8 | 完成并正式验收通过（2026-03 封板） |
| P9 | 完成并正式验收通过（2026-03 封板） |
| P10 | 完成并正式验收通过（2026-03 封板） |
| P11 | 完成并正式验收通过（2026-03 封板） |
| P12 | 完成并正式验收通过（2026-03 封板） |
| P13 | 完成并正式验收通过（2026-03 封板） |
| **P14** | **实施中** |

---

## 2. P14 唯一目标（窄定义）

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，将 `judge_agent_handler` 从固定链式裁判逻辑直接替换为 **ReAct 模式**（无开关），新增 `evaluate_artifact` 和 `compare_artifacts` 两个裁判工具，并补全对应的验收测试与契约文档。

### 2.1 现状说明

`judge_agent_handler.py` 在 P13 封板时为**固定链式逻辑**：

- 读取 `agent_artifacts` → 过滤错误 → 按置信度排序 → 写 `judge_result`
- 无 LLM 推理，无工具调用，无 `react_scratchpad`
- `judge_agent` 工具列表为空（`_AGENT_TOOLS["judge_agent"] = []`）

**P14 与 P12 的关键相似点**：
- 直接替换，不引入双模式开关
- 参照 `retrieval_agent_handler.py`（P12 纯 ReAct 模式）作为模板

### 2.2 纳入范围

**P14-1：judge_agent ReAct 化（直接替换）**

| 子项 | 说明 |
|------|------|
| P14-1a | `tool_registry.py`：新增 `_tool_evaluate_artifact`、`_tool_compare_artifacts` 实现；注册到 `_TOOL_REGISTRY`；更新 `_AGENT_TOOLS["judge_agent"]` 为 `["evaluate_artifact", "compare_artifacts"]` |
| P14-1b | `judge_agent_handler.py`：删除固定链式逻辑，接入 `run_react_loop`，写入 `react_scratchpad`，更新 `judge_agent_trace` 契约（新增 `mode`、`total_steps`、`stopped_reason`） |
| P14-1c | `tests/unit/test_judge_agent_handler.py`：新增单元测试（ReAct 模式 + 工具调用 + 降级路径 + trace 字段契约） |
| P14-1d | `tests/integration/test_p14_integration.py`：新增集成测试（ReAct 路径 + P13 基线不退化 + 文件完整性） |
| P14-1e | `docs/50_project/MULTI_AGENT_P14_SCOPE_2026-03.md`（本文件） |
| P14-1f | `docs/50_project/MULTI_AGENT_P14_SEAL_RESULT_2026-03.md`（封板后创建） |

### 2.3 范围外（硬约束）

P14 **不得**：

- 破坏 P1～P13 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 将 `react_loop` 并行工具调用纳入 P14
- 将多 Agent 循环 handoff、并行 agent 等纳入 P14
- 以 P14 名义回改 REFACTOR 三条硬性约束

---

## 3. 验收标准（写死，不得自行增减）

| 编号 | 验收标准 |
|------|----------|
| 3.1 | `judge_agent_handler` 走 ReAct 路径，`react_scratchpad` 非 None，`mode="react_loop"`，`judge_agent_trace` 包含 `total_steps`、`stopped_reason`、`artifact_ref`；有单元测试验证 |
| 3.2 | `evaluate_artifact` 和 `compare_artifacts` 工具均有真实实现（非占位），可通过 `execute_tool` 调用；有单元测试验证 |
| 3.3 | P13 基线（85 用例）不退化，`tool_registry` 接口契约不变，`judge_agent` 工具 allowlist 更新为 `["evaluate_artifact", "compare_artifacts"]`；有集成测试验证 |

---

## 4. judge_agent_trace 字段契约（P14，写死）

**ReAct 模式（P14 起唯一模式）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | str | 固定为 `"judge_agent"` |
| `mode` | str | 固定为 `"react_loop"` |
| `artifact_ref` | str | 非空，格式 `artifact://{request_id}/{agent_id}_{ts}` |
| `artifacts_evaluated` | int | 评估的工件总数 |
| `selected_agent` | str \| None | 选中的最优 agent_id |
| `judge_status` | str | `OK` 或 `DEGRADED` |
| `total_steps` | int | ReAct 循环步数（无工件时为 0） |
| `stopped_reason` | str | `FINAL_ANSWER` / `MAX_STEPS` / `ERROR` / `NO_ARTIFACTS` 之一 |
| `ts_ms` | int | Unix 毫秒时间戳 |

---

## 5. 工具规格

### evaluate_artifact

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `artifact` | object | ✅ | 工件对象（含 confidence、summary、risk_flags 等字段） |
| `agent_id` | string | ❌ | 工件所属 agent ID（用于标识） |

返回字段：`quality`（HIGH/MEDIUM/LOW）、`score`（0.0-1.0）、`issues`（问题列表）、`has_error`（bool）

### compare_artifacts

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `artifacts` | object | ✅ | 工件字典，key 为 agent_id，value 为工件对象 |

返回字段：`recommended`（推荐的 agent_id 或 None）、`scores`（各工件评分）、`valid_count`、`total_count`

---

## 6. 实现约束

| 参数 | 值 | 说明 |
|------|----|------|
| `allow_side_effects` | `False` | 裁判场景无副作用，只读工具 |
| `max_steps` | `3`（默认）| 可通过 `JUDGE_AGENT_REACT_MAX_STEPS` 环境变量覆盖 |
| 工具集 | `["evaluate_artifact", "compare_artifacts"]` | 只读工具 |
| `system_prompt_extra` | 裁判专家指令 | 不得在 P14 中修改 |

---

## 7. 改动范围（最小实现面）

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/app/agents/tool_registry.py` | 新增 `_tool_evaluate_artifact`、`_tool_compare_artifacts`；注册到 `_TOOL_REGISTRY`；`_AGENT_TOOLS["judge_agent"]` 更新为 `["evaluate_artifact", "compare_artifacts"]` |
| `host_brain/app/handlers/judge_agent_handler.py` | 删除固定链式逻辑，接入 `run_react_loop`，更新 trace 契约 |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/unit/test_judge_agent_handler.py` | P14 单元测试 |
| `tests/integration/test_p14_integration.py` | P14 集成测试 |
| `docs/50_project/MULTI_AGENT_P14_SCOPE_2026-03.md` | 本文件 |
| `docs/50_project/MULTI_AGENT_P14_SEAL_RESULT_2026-03.md` | 封板结论（实施后创建） |

---

## 变更记录

- 2026-03-17 v1.0：P14 SCOPE 初稿，基于 P13 封板基线，judge_agent ReAct 化。
