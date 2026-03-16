# P6：Intent 子图局部循环 — 范围与验收标准

**日期**：2026-03  
**状态**：完成并正式验收通过（2026-03 封板）；本文档为 P6 范围与验收的单一依据  
**定位**：在不动三图、不扩大 executor 的前提下，仅在 Intent 子图内探索局部循环能力  
**总原则**：只做 intent_agent_subgraph 内执行复杂度收敛，不做全局 ReAct、不扩权 executor。

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P6 中不回改：

- **P1～P4**：第一轮正式基线，已封板（三图、execution_profile、Intent 透明子图、契约与失败收口）
- **P5**：第二轮正式基线，已封板（Supervisor rule/llm 双模、观测与回退）

已冻结架构事实：

1. 先选图再跑图；Chat / Supervised / Intent 三图分治  
2. Intent 图外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer  
3. intent_agent_entry 内部为透明子图（core_reasoner_primary → planner_constraints → tool_planner），主图不暴露内部边  
4. executor 为通用执行器，不承载业务分派语义  
5. subgraph_commit_summary / subgraph_failure 契约已固化  

---

## 1. 项目阶段状态

| 阶段   | 状态     |
|--------|----------|
| P1     | 完成     |
| P2     | 完成     |
| P3     | 完成     |
| P3.5   | 完成并验收通过 |
| P4     | 完成并正式验收通过 |
| P5     | 完成并正式验收通过 |
| **P6** | **完成并正式验收通过**（2026-03 封板） |

**第一轮正式基线**：P1～P4，已封板。  
**第二轮正式基线**：P5，已封板。  
**第三轮正式基线**：P6，完成并正式验收通过，已封板。验收结论以 **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md** 为准。

---

## 2. P6 唯一目标（窄定义）

**一句话定位**：  
只在 **intent_agent_subgraph** 内探索局部循环能力，不改三图结构，不做全局 ReAct，不扩大 executor 职责。

- **范围内**：intent_agent_entry 所驱动的子图内部（当前为 core_reasoner_primary → planner_constraints → tool_planner）可引入**局部**循环/重试/多步工具观察或占位，且收口仍在子图内（subgraph_commit_summary / subgraph_failure 契约不变）。
- **范围外**：不改 Intent 图外层节点与边；不改 Chat / Supervised 图；不做全局 ReAct、全局动态路由、多 Agent handoff 扩展；不新增 Agent 类型；不扩 executor 业务语义。

---

## 3. P6 验收标准（实施前写死）

以下为 P6 验收必要条件，实施时不得放宽。

1. **外层 Intent 图结构不变**  
   主图仍为 intent_agent_entry → safety_verifier → judge_agent → renderer；主图 active_nodes / edges 不包含子图内部循环节点。

2. **子图出口契约不变**  
   subgraph_commit_summary / subgraph_failure 形态与消费口径仍按 P4/P5 契约；下游 safety_verifier / judge_agent 读取方式不变。

3. **局部循环仅限子图内**  
   任何“多步/重试/观察”逻辑仅发生在 intent_agent_subgraph 内部，由 intent_agent_entry 或子图内单一入口驱动；不向主图暴露新节点或新边。

4. **executor 职责不扩大**  
   executor 仍为通用执行器；不新增对“局部 ReAct/循环”的业务特判；子图内部实现由 handler 或子图自有逻辑完成。

5. **无全局 ReAct**  
   不引入全局 ReAct runtime、全局 scratchpad、跨图 handoff；不改变 execution_profile 或选图逻辑。

6. **可观测、可回退**  
   子图内局部循环产生的步骤/失败须落入 **subgraph_trace**（见 §7.3）；异常仍在 INTENT_EXEC 图内收口，不跨图逃逸。

---

## 4. 实现约束

- **不**新增业务图；**不**改 execution_profile 枚举或选图规则。  
- **不**在 executor 中按“子图类型”或“是否 ReAct”做分支；子图内部实现由 intent_agent_entry 及其调用的子图逻辑承担。  
- **不**引入 committed_route_token 或 __next_node__ 的子图内部写入；Governor 与主图控制权不变。  
- **不**新增 Agent 类型、不扩并行 agent、不扩多 Agent handoff。  

---

## 5. 与 REFACTOR / P4 / P5 / P6-1 文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、三图定义、P0～P6 实施顺序；P6 以本文档为范围与验收依据，与 REFACTOR 冲突时以本文档为准。  
- **MULTI_AGENT_P4_SCOPE_2026-03.md**：Intent 子图透明封装、subgraph_commit_summary / subgraph_failure 契约；P6 不得破坏 P4 验收结论与契约。  
- **MULTI_AGENT_P5_SCOPE_2026-03.md**：Supervisor 决策升级、8 条验收、冻结非目标；P6 不修改 P5 范围，不把 P5 冻结项当作 P6 入口。  
- **MULTI_AGENT_P6_1_LOCAL_LOOP_CONTRACT_2026-03.md**：P6-1 局部循环契约与最小闭环设计；边界默认值、失败码用法、subgraph_trace step 级结构、入口/出口不变实现方式；实现以 P6-1 为契约细化依据。  
- **MULTI_AGENT_P6_2_IMPLEMENTATION_2026-03.md**：P6-2 实现目标、最小实现面、验收最小集合（5 条）；实施与验收依据。  
- **MULTI_AGENT_P6_3_INTEGRATION_ACCEPTANCE_2026-03.md**：P6-3 集成验收补齐（4 条）；已通过并封板。  
- **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md**：P6 验收结论（6 条逐条结论、测试覆盖、观测约定、冻结非目标）。  

---

## 6. 冻结非目标（不纳入 P6 本轮）

以下**不属于 P6**，不得作为 P6 补丁或扩展纳入；若要做，须单独立项或后续阶段：

- 全局 ReAct、全局 dynamic executor routing  
- 新 Agent 类型（如 retrieval_agent、planner_agent）  
- 并行 agent、多 Agent handoff 扩展  
- executor 业务扩权、三图结构变更  
- Chat / Supervised 图的结构或语义变更  
- 以“P5 补丁”名义混入的 Supervisor 或决策面改动  

---

## 7. 局部循环冻结点（边界、失败码、审计）

以下三项在范围文档中提前锁定，避免实现时“子图内循环无硬边界”或与 P4 失败收口混为一团。

### 7.1 循环边界控制字段

局部循环须有硬上限，实现时必须至少支持以下**必选配置字段**（命名与语义冻结，具体默认值在 P6-1 设计阶段定）：

- **max_local_steps**：子图内单次局部循环最大步数  
- **max_retry_count**：子图内单步/单节点最大重试次数  
- **local_loop_timeout_ms**：子图内局部循环总超时（毫秒）  

缺一即视为未满足 P6 边界约束；不得以“无上限”或“仅靠全局超时”替代。

### 7.2 局部循环失败码（与 P4 区分）

局部循环产生的失败须与 P4 的 subgraph_failure 可区分；至少约定一组**最小失败码**，单独用于“因局部循环边界/超时/内部错误”导致的失败，不占用 P4 既有 error_code 语义：

- **LOCAL_LOOP_MAX_STEPS**：达到 max_local_steps 未收敛  
- **LOCAL_LOOP_TIMEOUT**：达到 local_loop_timeout_ms 未完成  
- **LOCAL_LOOP_INTERNAL_ERROR**：子图内循环执行过程中的内部错误  

上述失败仍通过 subgraph_failure 或子图出口契约上报，但 error_code 使用本组，便于下游与观测区分“子图节点失败”与“局部循环边界/超时/内部错误”。

### 7.3 局部循环审计落点

局部循环的审计**统一落在 subgraph_trace**，不再留“等价机制”由实现自由发挥。要求：

- 步骤级信息须写入 **subgraph_trace**  
- subgraph_trace 中须能区分每一步（step 级），便于可观测与回退分析  

具体 trace 结构（字段名、层级）在 P6-1 契约与最小闭环设计中定义。

---

## 8. 结论

- **P6 正式验收通过**；第三轮正式基线已封板。  
- **P6 定位**：只在 intent_agent_subgraph 内探索局部循环能力，不改三图结构，不做全局 ReAct，不扩大 executor 职责。  
- **P6 冻结点**：§7 边界控制字段、局部循环失败码、审计落点（subgraph_trace + step 级）已锁定；实现以 P6-1 设计文档为契约细化依据。  
- **验收结论**：见 **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md**；P6-3 集成验收 4 条已通过。
