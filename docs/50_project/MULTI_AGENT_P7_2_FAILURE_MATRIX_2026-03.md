# P7-2：三图失败收口矩阵

**日期**：2026-03  
**依据**：MULTI_AGENT_P7_SCOPE §3.3；P4/P5/P6 已固化契约  
**定位**：将 Chat / Supervised / Intent 三图的失败位置、失败类型、收口策略、可见输出固定为单一治理表，不得由实现者临场决定回退方式。

---

## 0. 原则

- **失败位置**：在哪个图、哪个节点（或子图内哪类边界）发生失败。  
- **失败类型**：统一使用已有 failure/reason_code（见 graph_contract、P4/P5/P6 文档）。  
- **收口策略**：该失败下如何回退（fallback 节点、写 payload、写 trace、不跨图）。  
- **可见输出**：下游与观测能读到的字段（payload、trace、ledger）。  
- **不跨图逃逸**：失败仅在当前 execution_profile 对应图内收口，不改写 execution_profile、不跳到其它图。

---

## 1. Chat 图

| 失败位置       | 失败类型 / reason_code     | 收口策略           | 可见输出           | 不跨图 |
|----------------|----------------------------|--------------------|--------------------|--------|
| 节点超时/依赖不可用 | ORCH_TIMEOUT / ORCH_DEPENDENCY_UNAVAILABLE | pick_fallback_node；按图 fallback_edges | payload、trace、reason_code | ✅ 仅 CHAT 图内 |
| 其它节点异常   | handler/网关返回的 reason_code | 同上               | 同上               | ✅     |

- **主链**：chat_direct → renderer（或当前 Chat 图主链）。  
- **预算**：图级 policy_timeout_ms；节点级 timeout_ms。预算突破时走图级 POLICY_TIMEOUT 或节点 ORCH_TIMEOUT，收口仍在 Chat 图内。

---

## 2. Supervised 图

| 失败位置           | 失败类型 / reason_code     | 收口策略           | 可见输出           | 不跨图 |
|--------------------|----------------------------|--------------------|--------------------|--------|
| supervisor_dispatch（LLM） | LLM_TIMEOUT / LLM_BAD_JSON / LLM_SCHEMA_INVALID / LLM_AGENT_NOT_ALLOWED / LLM_INTERNAL_ERROR | 回退到 rule；写 supervisor_dispatch（supervisor_failure_code、supervisor_decision_source=llm_fallback_rule） | payload.supervisor_dispatch、ledger supervisor_decision_log、metrics | ✅ 仅 WORKFLOW_SUPERVISED |
| supervisor_dispatch（rule） | 无 LLM 失败码              | 正常选 agent       | 同上               | ✅     |
| agent_dispatch 及下游节点 | ORCH_TIMEOUT / ORCH_DEPENDENCY_UNAVAILABLE 等 | pick_fallback_node；按图 fallback_edges | payload、trace、reason_code | ✅     |
| 图级策略超时       | POLICY_TIMEOUT / EXEC_TIMEOUT | 时钟事件、终止/降级 | clock_events、trace、status | ✅     |

- **主链**：supervisor_dispatch → agent_dispatch → judge_agent → renderer。  
- **预算**：图级 policy_timeout_ms；Supervisor LLM 超时记为 LLM_TIMEOUT；节点级 timeout_ms。  
- **契约**：P5 proposal 失败码、allowlist、supervisor_decision_log 已固化；P7 不改变其语义，只在此表统一列出。

---

## 3. Intent 图

| 失败位置           | 失败类型 / error_code      | 收口策略           | 可见输出           | 不跨图 |
|--------------------|----------------------------|--------------------|--------------------|--------|
| intent_agent_entry（子图未执行） | INVOKE_MODEL_UNAVAILABLE   | 写 subgraph_failure；写 subgraph_trace（step 级）；写 subgraph_commit_summary | payload.subgraph_failure / subgraph_commit_summary、subgraph_trace | ✅ 仅 INTENT_EXEC |
| intent_agent_entry（子图节点异常） | HANDLER_ERROR 等           | 同上               | 同上               | ✅     |
| intent_agent_entry（局部循环） | LOCAL_LOOP_MAX_STEPS / LOCAL_LOOP_TIMEOUT / LOCAL_LOOP_INTERNAL_ERROR | 写 subgraph_failure（error_code=上述）；subgraph_trace 含 step 级 | 同上               | ✅     |
| safety_verifier / judge_agent / renderer | 节点自身 reason_code       | 按图 fallback_edges 或终止 | payload、trace     | ✅     |
| 图级策略超时       | POLICY_TIMEOUT / EXEC_TIMEOUT | 时钟事件、终止/降级 | clock_events、trace、status | ✅     |

- **主链**：intent_agent_entry → safety_verifier → judge_agent → renderer。  
- **子图**：intent_agent_subgraph 内局部循环；边界 max_local_steps / local_loop_timeout_ms / max_retry_count；突破时使用 LOCAL_LOOP_* 失败码。  
- **契约**：P4 subgraph_commit_summary / subgraph_failure；P6 step 级 subgraph_trace、LOCAL_LOOP_*；P7 不改变其语义，只在此表统一列出。

---

## 4. 预算突破与观测

- **预算突破**：凡属于 graph_contract.BUDGET_BREACH_FAILURE_CODES 的 reason_code / error_code，均视为“预算突破”类失败，用于观测聚合（P7-1、P7-3）。  
- **观测**：三图均通过 payload、trace、ledger（及可选 subgraph_trace、supervisor_decision_log）提供“失败位置、失败类型、收口结果”的可追踪性；不得临场发明新字段破坏既有消费口径。

---

## 5. 与契约的对应

- **P4**：subgraph_failure 必含 failed_node、error_code、safe_fallback_reason、trace_ref；下游仅读 subgraph_commit_summary / subgraph_failure。  
- **P5**：SUPERVISOR_PROPOSAL_FAILURE_CODES；supervisor_decision_log；回退到 rule 时写 supervisor_failure_code。  
- **P6**：LOCAL_LOOP_FAILURE_CODES；subgraph_trace step 级。  
- **P7-1**：BUDGET_BREACH_FAILURE_CODES、is_budget_breach_failure_code。  

本矩阵不新增失败码，不改变上述契约字段含义；仅将三图收口策略与可见输出固定为单一表格，供实现与验收一致遵循。
