受众：PROJECT
标题：P6-1：局部循环契约与最小闭环设计

# P6-1：局部循环契约与最小闭环设计

**日期**：2026-03  
**状态**：设计稿；承接 P6_SCOPE，作为 P6 实现的契约与最小闭环依据  
**定位**：在 P6 范围内，先定契约（边界、失败码、trace）、再实现；避免实现阶段重新发散。

---

## 0. 依据文档

- **MULTI_AGENT_P6_SCOPE_2026-03.md**：P6 唯一目标、验收标准、实现约束、§7 冻结点（边界控制字段、局部循环失败码、审计落点）。  
- **MULTI_AGENT_P4_SCOPE_2026-03.md**：子图出口契约（subgraph_commit_summary / subgraph_failure）、下游消费口径。  

本设计不突破 P6_SCOPE 与 P4 契约；子图入口/出口形态不变，主图不感知“局部循环”。

---

## 1. 循环边界（与 P6_SCOPE §7.1 对齐）

以下三字段为**必选配置**，实现时必须读取并生效；默认值建议如下（可在配置层覆盖）：

| 字段 | 含义 | 建议默认值 | 约束 |
|------|------|------------|------|
| **max_local_steps** | 子图内单次局部循环最大步数 | 例如 10 | 正整数；达到即停止并报 LOCAL_LOOP_MAX_STEPS |
| **max_retry_count** | 子图内单步/单节点最大重试次数 | 例如 2 | 非负整数；超限则该步失败，按 §2 失败码收口 |
| **local_loop_timeout_ms** | 子图内局部循环总超时（毫秒） | 例如 60_000 | 正整数；超时即停止并报 LOCAL_LOOP_TIMEOUT |

- 配置来源：由 intent_agent_entry 所在图/节点的 context 或配置注入，不暴露到主图 payload。  
- 边界语义：任一边界触达即终止局部循环，并按 §2 写入失败码与 §3 写入 trace，子图出口仍为 subgraph_failure（或带 summary 的混合形态，见 P4 契约）。

---

## 2. 局部循环失败码（与 P6_SCOPE §7.2 对齐）

与 P4 的 subgraph_failure 共用同一 payload 键与必含字段（failed_node, error_code, safe_fallback_reason, trace_ref），**error_code** 使用下表，与 P4 既有码（如 HANDLER_ERROR、INVOKE_MODEL_UNAVAILABLE）区分：

| 失败码 | 含义 | 触发条件 |
|--------|------|----------|
| **LOCAL_LOOP_MAX_STEPS** | 达到 max_local_steps 未收敛 | 步数达到上限仍未满足退出条件 |
| **LOCAL_LOOP_TIMEOUT** | 达到 local_loop_timeout_ms 未完成 | 局部循环总耗时超过上限 |
| **LOCAL_LOOP_INTERNAL_ERROR** | 子图内循环执行内部错误 | 循环逻辑内部异常（非单节点业务异常） |

- **failed_node**：可填子图内实际最后执行节点，或固定为“局部循环”的虚拟节点名（如 `local_loop`），由实现统一约定。  
- **trace_ref**：必须能定位到 subgraph_trace，便于下游与观测按 trace 排查。

---

## 3. 局部循环审计落点：subgraph_trace 与 step 级（与 P6_SCOPE §7.3 对齐）

审计**统一落在 subgraph_trace**；现有 P4 已写入“节点级”条目（node_id, status, ts_ms, reason_code, detail）。P6 局部循环在**同一 subgraph_trace** 中增加 **step 级**信息，两种方式二选一或兼容即可：

- **方式 A**：在现有每条 node 级条目上增加可选字段 **steps**（list），每个 step 含：step_index, ts_ms, status, reason_code?, detail?。  
- **方式 B**：在 subgraph_trace 中增加独立条目，node_id 为“局部循环”的虚拟节点，且该条目的 **steps** 列表为 step 级明细。

约定：

- 至少有一个 **step 级**可区分（步序号 + 时间戳 + 状态）；便于“第几步超时/达上限”的可观测与回退分析。  
- 若某步重试，同一 step_index 可有多条或由 reason_code 区分重试；具体结构在实现时在本文档下补一子节，不在此扩写。

---

## 4. 子图入口/出口不变的实现方式

- **入口**：主图仍只调 **intent_agent_entry**；不新增主图节点或边。局部循环由 intent_agent_entry 内部（或其所调用的子图执行器）完成，主图无感知。  
- **出口**：  
  - 成功：仍写 **subgraph_commit_summary**（P4 形态不变）；**subgraph_trace** 含节点级 + 可选 step 级。  
  - 失败：仍写 **subgraph_failure**（P4 必含字段不变）；error_code 为 §2 中三码之一或 P4 既有码；**subgraph_trace** 同样含 step 级以便排查。  
- **不新增**：主图 payload 不新增键；不向 executor 暴露“是否局部循环”的语义；Governor/选图不变。

---

## 5. 最小闭环设计（实现顺序建议）

1. **契约落地**：在 graph_contract（或等价常量模块）中新增 P6 局部循环常量：三边界字段名、三失败码、subgraph_trace step 结构约定。  
2. **配置注入**：intent_agent_entry 的 context/配置可读 max_local_steps、max_retry_count、local_loop_timeout_ms；默认值见 §1。  
3. **循环逻辑**：在子图执行路径内实现“步数 + 超时 + 重试”的硬边界；触达边界时写 subgraph_failure（§2）并写 subgraph_trace（§3）。  
4. **测试**：单测/集成测覆盖“达 max_local_steps 报 LOCAL_LOOP_MAX_STEPS”“超时报 LOCAL_LOOP_TIMEOUT”“subgraph_trace 含 step 级”；出口形态符合 P4 契约。

---

## 6. 与 P4 / P5 的边界

- **P4**：subgraph_commit_summary / subgraph_failure 的键与下游消费口径不变；P6 仅扩展 error_code 取值与 subgraph_trace 的 step 级内容。  
- **P5**：不涉及；Supervisor 与决策面不改动。  

---

## 7. 结论

- P6-1 锁定：**循环边界**（三字段 + 默认值建议）、**局部循环失败码**（三码）、**审计落点**（subgraph_trace + step 级）、**入口/出口不变**的实现方式。  
- 实现应以本文档为契约细化依据，与 P6_SCOPE §7 一致；后续若扩展 step 结构或默认值，仅在本文档内增补，不扩大 P6 范围。
