受众：PROJECT
标题：P4：意图子图透明封装 — 范围与验收标准

# P4：意图子图透明封装 — 范围与验收标准

**日期**：2026-03  
**状态**：P4 已验收封板；本文档为 P4 实施与验收的单一依据  
**原则**：只做收口，不顺手扩能力。

---

## 1. 项目阶段状态（验收结论）

| 阶段   | 状态     | 说明 |
|--------|----------|------|
| P1     | 完成     | execution_profile、三图选图、入口写 profile |
| P2     | 完成     | 开关收敛、scene_router 退场、文档与 smoke |
| P3     | 完成     | 监督图主路径、agent_dispatch_handler、code/translate 迁入 |
| P3.5   | 完成并可验收 | executor 无 agent_dispatch 业务特判；handler 自跑 model；allowlist 图配置；结构断言测试 |
| **P4** | **完成并正式验收通过** | 意图子图透明封装；主图 4 节点、契约与失败收口已固化（§4/§8），封板 |

---

## 2. P4 唯一目标（窄定义）

**把 Intent 图外层语义固定不变，把 intent_agent_entry 的内部实现替换为透明子图。**

- **外层保持不变**：  
  `START → intent_agent_entry → safety_verifier → judge_agent → renderer → END`
- **内部替换**：  
  `intent_agent_entry` 从当前「旧链实现」切到 **intent_agent_subgraph**（透明子图）。

P4 **不**做：新增智能、ReAct 全局化、新 Agent、新路由能力。只做「主图收口 + 审计保留」。

---

## 3. P4 验收标准（实施前写死，不可放宽）

以下 6 条为 P4 验收必要条件，缺一即不通过。

1. **外层 Intent 图节点不变**  
   仍以 `intent_agent_entry` 为入口；主图 edges 仍为  
   `intent_agent_entry → safety_verifier → judge_agent → renderer`。

2. **主图 active_nodes 不再暴露内部链**  
   主图 `active_nodes` / 主图 edges **不得**再出现  
   `core_reasoner_primary`、`planner_constraints`、`tool_planner`。  
   上述节点仅存在于子图内部。

3. **intent_agent_entry 可输出统一摘要**  
   `intent_agent_entry`（或子图出口）可输出统一的 **subgraph_commit_summary** 或等价摘要，供主图下游（safety_verifier / judge）消费。

4. **子图失败仅在 INTENT_EXEC 图内收口**  
   子图失败后只在 Intent 图内安全收口，**不跨图逃逸**；不污染 Chat / Supervised 图状态。

5. **committed_route_token 仍只由 Governor 控制**  
   子图内部**不得**写入或改写 `committed_route_token`；该字段仍仅由 Host Governor 写入。

6. **子图内部节点全 trace、可审计，不污染主图 edges**  
   子图内部节点执行均进入 trace / subgraph_trace（或等价机制），可审计；  
   主图 edges 仅包含主图节点，不被子图内部节点「撑开」。

---

## 4. 冻结项（实施口径）

以下两项写死，所有实现必须遵守，不得各自发明字段或形态。

### 4.1 subgraph_commit_summary 落点与消费口径

- **写入**：由 `intent_agent_entry`（或子图出口 handler）写入 **payload["subgraph_commit_summary"]**（或项目内约定的同一 state 顶层字段，仅此一个）。
- **消费**：safety_verifier / judge_agent **只**从该固定字段读取子图摘要；不得从其它字段或别名读取。
- **禁止**：不同实现不得各自发明不同摘要字段名；不得出现 `intent_summary`、`subgraph_result` 等并行字段供下游消费。

### 4.2 子图失败归一化输出

子图失败时，出口必须产出**归一化结构**，最少包含：

| 字段 | 含义 |
|------|------|
| **failed_node** | 子图内失败节点 ID |
| **error_code** | 归一化错误码（如 HANDLER_ERROR / TIMEOUT / POLICY_DENIED） |
| **safe_fallback_reason** | 可读的收口原因，便于日志与审计 |
| **trace_ref** | 或等价引用，指向本次子图 trace（便于审计） |

该结构可放在 payload 的固定键下（如 `payload["subgraph_failure"]`）或与现有 failure 契约统一；形态一旦定稿，所有子图失败路径均按此输出，便于「仅在 INTENT_EXEC 图内收口」的稳定实现与测试。

---

## 5. 实现约束（防止边界从另一侧长回来）

- **不得**把 Intent Agent 做成**全局 ReAct**。  
- **不得**在 P4 中引入：`react_scratchpad`、全局循环 handoff、全局动态 agent routing。  
- **最多**允许：子图内部「局部循环能力」的**占位**（如占位节点/占位分支），不实现完整 ReAct 或跨图 handoff。  

目的：避免刚在 P3.5 收好的「分派边界」从 Intent 侧重新模糊。

---

## 6. 与 REFACTOR 文档的对应关系

- 架构总则、三图定义、概念拆开：以 `MULTI_AGENT_ARCH_REFACTOR_2026-03.md` 为准。  
- P4 的**范围、验收标准、实现约束**：以本文档为准；若与 REFACTOR 中「P4 / 意图图」描述有冲突，以本文档为实施与验收依据。

---

## 7. 验收方式建议

- **结构 / 配置**：主图 config 的 `active_nodes` 与 `edges` 不包含 core_reasoner_primary / planner_constraints / tool_planner。  
- **行为**：INTENT_EXEC 请求仍走 `intent_agent_entry → safety_verifier → judge_agent → renderer`；子图失败时状态与 trace 仅限 Intent 图。  
- **审计**：子图内部节点有 trace 或 subgraph_trace，可查；committed_route_token 仅在 Governor 路径被写入。

---

## 8. 下游消费口径唯一化（P4 封板）

以下为 **safety_verifier / judge_agent** 等下游节点读取子图结果的**唯一约定**，禁止从其它字段或别名消费。

### 8.1 读取优先级

| 下游节点           | 优先读取字段 | 说明 |
|--------------------|--------------|------|
| **safety_verifier** | `payload["subgraph_failure"]` 若存在则先据此判断失败；否则看 `payload["subgraph_commit_summary"]` | 失败时以 subgraph_failure 为准 |
| **judge_agent**     | 同上：先 `subgraph_failure`，再 `subgraph_commit_summary` | 与 safety_verifier 一致，避免分歧 |

实现上：下游**仅允许**从 `payload["subgraph_commit_summary"]` 与 `payload["subgraph_failure"]` 读取；不得使用 `intent_summary`、`subgraph_result` 等其它字段。

### 8.2 subgraph_commit_summary 与 subgraph_failure 关系

- **成功路径**：仅有 `subgraph_commit_summary`，无 `subgraph_failure`。
- **失败路径**：**必有** `subgraph_failure`；同时可存在 `subgraph_commit_summary`（此时 `fail_count` ≥ 1，便于审计）。  
即两者**不互斥**：失败时两字段可同时存在，下游以 `subgraph_failure` 优先判定为失败。

### 8.3 契约落地

- 字段名与必含键以 `host_brain/integrations/graph_contract.py` 中常量为准：  
  `PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY`、`PAYLOAD_KEY_SUBGRAPH_FAILURE`、  
  `SUBGRAPH_FAILURE_KEYS`、`SUBGRAPH_COMMIT_SUMMARY_KEYS`。  
- 新增 handler 或 verifier 不得扩展上述两结构的字段集合；扩展须走契约变更。
