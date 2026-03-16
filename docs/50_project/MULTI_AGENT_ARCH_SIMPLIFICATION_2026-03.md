# 多 Agent 编排架构简化建议

**日期**：2026-03  
**背景**：当前主/子多级 agent + 主图长链 + 子图并存，路径与开关较多，架构认知负担大。  
**定版方案**：简化与收敛已并入 **`MULTI_AGENT_ARCH_REFACTOR_2026-03.md`**（先选图再跑图、三张图 + execution_profile、开关收敛、P0～P6 实施顺序；下一轮正式基线仅 P5，P6 单独规划）。本文档保留为「混乱点归纳」与「为何要简化」的简要说明；**可执行方案以 REFACTOR 文档为准**。

---

## 1. 当前混乱点

| 问题 | 表现 |
|------|------|
| **双套路径** | MULTI_AGENT_ENABLED=false 时走「主图长链」（supervisor → core_reasoner_primary → scene_router_post_core → planner_constraints → …）；=true 时走「agent_dispatch_node → 单节点+handler → judge_agent_node」。同一张图里两套逻辑，靠一个 env 切换。 |
| **图大而杂** | 一张图里既有 scene_router_entry、supervisor_dispatch、agent_dispatch_node、judge_agent_node，又有 planner_constraints、tool_planner、workbench_*、consistency_verifier 等主图节点；部分边在完整治理路径下永远不会走到，但仍在 edges/budget_routes 里。 |
| **概念叠加** | 「场景路由」「Supervisor 分派」「agent 节点」「model 节点」「子图」多套概念叠加；子图已实现但未接入，进一步增加心智负担。 |
| **开关多** | HOST_GRAPH_SCENE_ROUTER_ENABLED、MULTI_AGENT_ENABLED、SUPERVISOR_AUTO_ENABLED、INTENT_SUBGRAPH_ENABLED 等，行为组合多，难以一眼看出「当前请求会走哪条路」。 |

---

## 2. 简化目标（建议）

- **单一主路径**：只保留「统一入口 → Supervisor → 单点执行单元（agent 或子图）→ Judge（如需）→ Renderer」一条主路径；不再在同一张图里并存「主图长链」和「agent_dispatch 短链」两套。
- **图即所见**：图上的边与节点与真实执行路径一致；不再保留「永远不会走到的边」仅用于 budget 或历史兼容。
- **子图或主图二选一**：复杂链（如 intent 的 core→planner→tool_planner）要么全部进子图由一处 invoke，要么全部在主图上显式节点；不混用导致「同一批节点可能被两条路径跑」。

---

## 3. 可选收敛方案（供排期）

### 方案 A：全面走「治理路径」，主图只做路由 + 单点执行 + Judge

- 去掉 MULTI_AGENT_ENABLED 开关；**始终** supervisor → agent_dispatch_node（或 chat 直连 chat_direct_ja）。
- 主图不再保留「scene_router_post_core → planner_constraints → tool_planner → …」等长链边；intent 的链要么在 agent_dispatch 内调用 **intent_agent_subgraph**，要么由 intent_agent_handler 内部完成（如 ReAct），主图不显式挂 planner_constraints/tool_planner。
- 主图拓扑收敛为：`scene_router_entry → [chat_direct_ja \| supervisor_dispatch → agent_dispatch_node → (judge_agent_node \| renderer) → renderer]`。

### 方案 B：保留「轻量路径」与「治理路径」但拆成两张图

- **轻量图**：仅 scene_router_entry → chat_direct_ja / workbench_* / core_reasoner_primary 等少量节点，无 supervisor、无 judge、无 agent_dispatch；用于低风险或性能优先。
- **治理图**：当前完整治理拓扑（supervisor → agent_dispatch_node → judge_agent_node → renderer），用于需要审计/多 agent 的场景。
- 入口根据策略选图，而不是一张图里用 env 切换路径；两图共享节点实现，但拓扑清晰分离。

### 方案 C：先文档化「当前唯一推荐路径」，再逐步删冗余

- 在代码与文档中明确标注：**推荐生产路径** = MULTI_AGENT_ENABLED=true + 统一图（scene_router_entry → supervisor_dispatch → agent_dispatch_node → judge_agent_node → renderer）；其余边/路径视为兼容或待废弃。
- 后续迭代：逐步将未走到的边从「必建」改为「按需建」或移除，收敛到方案 A。

---

## 4. 建议下一步

- 短期：在封测/发布说明中写清「当前推荐架构 = 完整治理单路径」，并指向本文档，减少「两套路径同时记」的认知负担。
- 中期：选定方案 A/B/C 之一，在升级计划中拆成小步（先删冗余边或拆图，再动执行器），避免大爆炸式重写。
- 长期：子图若接入，严格约定「主图不重复跑子图内节点」，保持「图即所见」。

---

---

**可执行方案**：见 **`docs/50_project/MULTI_AGENT_ARCH_REFACTOR_2026-03.md`**（图拆分、概念收口、开关收敛、P0～P6 顺序；下一轮仅 P5，P6 单独规划）。  
*本文档仅作混乱点归纳与简化动机；具体排期与实施以 REFACTOR 文档为准。*
