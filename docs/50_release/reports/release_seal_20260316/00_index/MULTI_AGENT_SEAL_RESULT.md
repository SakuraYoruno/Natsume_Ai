# 多智能体（Multi-Agent）封测实测结论

**日期**：2026-03-16  
**依据**：`docs/50_project/MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md`、封测计划 §10  
**架构重构**：可执行方案见 **`docs/50_project/MULTI_AGENT_ARCH_REFACTOR_2026-03.md`**（先选图再跑图、三张图 + execution_profile、P0～P6）。P0 已约定：暂停 M-MA5/M-MA6 扩张，先做图拆分与开关收敛。**阶段边界**：第一轮正式基线 P1～P4 已封板；下一轮正式基线仅 P5（Supervisor 决策升级），P6 再下一轮单独规划、不并入 P5。

---

## 1. 结论摘要

| 项 | 结果 |
|----|------|
| **多 agent 代码是否落地** | ✅ 是 |
| **P1/P2 图拆分与开关收敛** | ✅ 已完成：路由由 execution_profile 决定，三张图（chat / supervised / intent） |
| **完整多 agent 治理** | ✅ 在 **WORKFLOW_SUPERVISED** 图内闭环（supervisor_dispatch → agent_dispatch_node → judge_agent_node → renderer） |

选图由入口层 **execution_profile** 决定，不再使用 `MULTI_AGENT_ENABLED`。非 chat 且非执行意图（WORK_CODE / WORK_TRANSLATE / AUTO）进入 **supervised_graph**，经 agent_dispatch_node → judge_agent_node → renderer。

---

## 2. 已实现部分（P1/P2 后）

- **入口与路由**：按 **execution_profile** 选图（CHAT_FAST→chat_graph，WORKFLOW_SUPERVISED→supervised_graph，INTENT_EXEC→intent_graph）；payload 写入 execution_profile，graph_runtime 仅从 payload 注入 state。Supervisor 分派由 **SUPERVISOR_MODE**（rule|llm）控制，不再用 SUPERVISOR_AUTO_ENABLED。
- **Supervised 图治理拓扑**：
  - Supervisor 固定下一跳 `agent_dispatch_node`（本 handler 仅在此图内运行）。
  - 图中包含 **agent_dispatch_node**（虚拟节点）、**judge_agent_node**（绑定 judge_agent_handler），边 judge_agent_node → renderer。
  - **agent_dispatch_node** 执行逻辑（_run_agent_dispatch）：先跑对应 model 再跑 agent handler，写 agent_artifacts；按 need_judge 进 judge_agent_node 或 renderer。
  - **judge_agent_node** 运行 judge_agent_handler，读 agent_artifacts、写 judge_result，再进 renderer。
- **状态与观测**：GraphState 顶层 execution_profile、active_agent_id、handoff_history、agent_budget、agent_artifacts、judge_result；metrics/logs 使用 execution_profile、SUPERVISOR_MODE。

---

## 3. 变更留痕（缺口补全实现）

| 原缺口 | 实现方式 |
|--------|----------|
| 子 agent 以 model 节点运行、未写 agent_artifacts | 非 chat 时 supervisor 指向 agent_dispatch_node；_run_agent_dispatch 内先 _invoke_model_node(agent 对应节点)，再 _invoke_handler(agent handler)；handler 在 payload 中写 agent_artifacts，并同步到 state.agent_artifacts。 |
| 未走 agent_dispatch_node / judge_agent_node | graph_config_builder 增加 AGENT_DISPATCH_NODE_ID、node_bindings 中增加 agent_dispatch_node（虚拟）与 judge_agent_node（handler_id=judge_agent）；增加边 judge_agent_node → CHAT_RENDER_NODE_ID；budget_routes 增加 [supervisor_dispatch, agent_dispatch_node, judge_agent_node, renderer]。 |
| Supervisor 直连 workbench_* | supervisor_dispatch_handler：当 multi_agent_enabled 且 selected_agent != chat_agent 时，__next_node__ = AGENT_DISPATCH_NODE_ID；否则沿用 _AGENT_TO_NODE。 |
| need_judge 与 Judge 收口 | _run_agent_dispatch 从 payload.supervisor_dispatch.need_judge 读取；next_node = JUDGE_AGENT_NODE_ID 或 CHAT_RENDER_NODE_ID；judge_agent_handler 读 state.payload.agent_artifacts（及顶层 agent_artifacts）、写 judge_result。 |

**涉及文件**：

- `host_brain/app/graph_config_builder.py`：AGENT_DISPATCH_NODE_ID；scene_node_sequence / node_bindings / edges / budget_routes 增加 agent_dispatch_node、judge_agent_node。
- `host_brain/app/handlers/supervisor_dispatch_handler.py`：引入 AGENT_DISPATCH_NODE_ID；非 chat 且 multi_agent_enabled 时 next_node = AGENT_DISPATCH_NODE_ID。
- `host_brain/integrations/graph_executor.py`：_AGENT_TO_MODEL_NODE；_run_agent_dispatch 先跑 model 再跑 handler、合并 payload、从 supervisor_dispatch 取 need_judge、写回 agent_artifacts 到 state；使用 AGENT_DISPATCH_NODE_ID 常量判断。

---

## 4. 配置与开关（P2 收敛后）

- **execution_profile**：由入口层写入，决定跑哪张图；不再使用 MULTI_AGENT_ENABLED / HOST_GRAPH_SCENE_ROUTER_ENABLED。
- **SUPERVISOR_MODE**：`rule`（默认）= 规则分派，`llm` = LLM 提案分派（原 SUPERVISOR_AUTO_ENABLED 收敛为此）。
- **INTENT_EXEC_MODE**：（可选）执行意图链式/子图模式。

---

## 5. 主子多级 agent 与子图是否重复

**结论：当前不重复，且需保持互斥约定。**

| 图 | 何时走 | 跑的节点/子图 |
|----|--------|----------------|
| **chat_graph** | execution_profile=CHAT_FAST | chat_direct_ja → renderer |
| **supervised_graph** | execution_profile=WORKFLOW_SUPERVISED（WORK_CODE / WORK_TRANSLATE / AUTO） | supervisor_dispatch → agent_dispatch_node → judge_agent_node → renderer；agent_dispatch 内跑单节点 model + agent handler，未调用子图。 |
| **intent_graph** | execution_profile=INTENT_EXEC | intent_agent_entry → safety_verifier → judge_agent_node → renderer（P4 可替换 entry 内为 subgraph） |
| **子图**（intent_agent_subgraph / code_refine_subgraph） | 当前主图**未接入** | 需由某节点显式 invoke；主图约定「同一请求只走主图链 **或** 只走子图」。 |

---

## 6. 建议后续动作

1. **P1/P2 集成 smoke**：已通过 `tests/integration/test_p1_p2_execution_profile_smoke.py`（CHAT_FAST→chat_graph，WORKFLOW_SUPERVISED→supervised_graph，INTENT_EXEC→intent_graph）。
2. **P3**：将 code/translate 从旧主图彻底迁入 supervised_graph，立起 agent_dispatch_handler 为唯一显式 Agent 分派边界。
3. **可选**：intent_agent_entry 内 P4 替换为 intent_agent_subgraph（子图内跑 core_reasoner_primary→planner_constraints→tool_planner）。
