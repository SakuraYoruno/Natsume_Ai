受众：PROJECT
标题：P6：Intent 子图局部循环 — 验收结论

# P6：Intent 子图局部循环 — 验收结论

**日期**：2026-03  
**依据**：MULTI_AGENT_P6_SCOPE_2026-03.md（6 条验收标准）、P6-3 集成验收 4 条  
**结论**：**P6 正式验收通过**，第三轮正式基线已封板。

---

## 1. 验收范围

- **目标**：只在 intent_agent_subgraph 内探索局部循环能力，不改三图结构，不做全局 ReAct，不扩大 executor 职责；带硬边界（max_local_steps / max_retry_count / local_loop_timeout_ms）、局部循环失败码（LOCAL_LOOP_*）、subgraph_trace step 级审计。
- **实施项**：P6-1 契约与最小闭环设计，P6-2 最小闭环实现（intent_agent_entry_handler 内循环驱动、边界配置、失败码与 step 级 trace），P6-3 集成验收补齐（超时、重试耗尽、成功路径、结构不变）。
- **不纳入**：全局 ReAct、新 Agent、并行 agent、executor 扩权、三图结构变更（见 P6_SCOPE §6）。

---

## 2. 对 6 条验收标准的逐条结论

| 标准 | 结论 | 说明 |
|------|------|------|
| **3.1 外层 Intent 图结构不变** | ✅ 满足 | 主图仍为 intent_agent_entry → safety_verifier → judge_agent → renderer；P6-3 结构断言与集成用例均确认 active_nodes 为 4 节点、edges 不包含 local_loop 或子图内部节点。 |
| **3.2 子图出口契约不变** | ✅ 满足 | subgraph_commit_summary / subgraph_failure 形态与 P4/P5 一致；成功路径与失败路径集成用例验证下游消费口径不变。 |
| **3.3 局部循环仅限子图内** | ✅ 满足 | 循环驱动仅在 intent_agent_entry_handler 内；不向主图暴露新节点或新边；超时/重试耗尽/成功路径均在 INTENT_EXEC 图内收口。 |
| **3.4 executor 职责不扩大** | ✅ 满足 | graph_executor 无 LOCAL_LOOP 业务分支（P6-3 结构断言）；子图内部实现由 handler 承担。 |
| **3.5 无全局 ReAct** | ✅ 满足 | 未引入全局 ReAct runtime、全局 scratchpad、跨图 handoff；execution_profile 与选图逻辑未改。 |
| **3.6 可观测、可回退** | ✅ 满足 | 局部循环步骤/失败落入 subgraph_trace（step 级）；graph_runtime._finalize 将 subgraph_trace 作为可选观测输出暴露，仅用于输出/审计，不作为控制真相源。 |

**6/6 满足，无缺口。**

---

## 3. 测试覆盖摘要

- **契约与边界（P6-2）**：tests/unit/test_p6_local_loop_contract.py — LOCAL_LOOP_MAX_STEPS 触发、subgraph_trace step 级字段、失败码常量。
- **集成（P6-3）**：tests/integration/test_p6_integration_acceptance.py — 超时（LOCAL_LOOP_TIMEOUT）、重试耗尽（LOCAL_LOOP_INTERNAL_ERROR）、成功路径（subgraph_commit_summary、step 级 trace）、结构不变（4 节点、edges 无子图节点、executor 无 P6 特判）。
- **P4 回归**：tests/unit/test_p4_intent_subgraph.py、tests/integration/test_p4_intent_subgraph_failure.py 仍通过，子图失败收口与主图不变未破坏。

---

## 4. 观测输出约定（subgraph_trace）

- **graph_runtime._finalize** 将 **subgraph_trace** 作为可选字段纳入 run_graph 输出，属审计可见性增强。
- **约定**：仅用于输出/观测，不作为控制真相源；不改变主图语义，不扩大 executor 业务职责，不破坏 P4/P5 既有边界。

---

## 5. 冻结非目标（不纳入 P6 本轮）

以下**不属于 P6**，封板后不得作为 P6 补丁纳入（与 P6_SCOPE §6 一致）：

- 全局 ReAct、全局 dynamic executor routing  
- 新 Agent 类型、并行 agent、多 Agent handoff 扩展  
- executor 业务扩权、三图结构变更  
- Chat / Supervised 图的结构或语义变更  
- 以“P5 补丁”名义混入的 Supervisor 或决策面改动  

上述事项归属后续阶段，须单独规划与启动。

---

## 6. 最终结论

- **P6 正式验收通过。**
- **第一轮正式基线**：P1～P4，已封板。  
- **第二轮正式基线**：P5，已封板。  
- **第三轮正式基线**：P6，完成并正式验收通过，已封板。

---

## 7. 与基线文档的对应

- **范围与 6 条标准**：以 MULTI_AGENT_P6_SCOPE_2026-03.md 为准。  
- **契约与实现**：P6-1、P6-2、P6-3 文档见 P6_SCOPE §5。  
- **阶段状态**：P6 在 MULTI_AGENT_P6_SCOPE_2026-03.md 与 MULTI_AGENT_ARCH_REFACTOR_2026-03.md 中已更新为「完成并正式验收通过」。
