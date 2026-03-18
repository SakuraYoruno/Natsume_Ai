受众：PROJECT
标题：多 Agent 架构重构：图拆分与概念收口（可执行方案）

# 多 Agent 架构重构：图拆分与概念收口（可执行方案）

**日期**：2026-03  
**状态**：**多智能体重构升级已完成最终封板**（P1～P16 已全部封板）；定版方案，作为 P0～P16 实施依据  
**原则**：先选图再跑图；先收口再扩能力；先让路径可解释，再让 Agent 更聪明。

---

## 0. 三条硬性约束（不可违反）

1. **不再维护「一张万能主图」**。系统由多张图组成，每张图只承载一种执行哲学。
2. **不再用 MULTI_AGENT_ENABLED 切换完全不同的路由模型**。路由由 execution_profile 决定，profile 由入口层写入。
3. **M-MA5 / M-MA6 暂停往前推**。必须先完成「图拆分 + 概念收口 + 开关收敛」，再考虑 ReAct / 多 Agent 循环 handoff 等能力扩张。

---

## 1. 新的总原则：两层路由决策

系统只允许两层路由决策：

- **第 1 层**：Host Governor / 入口层决定「进入哪一种执行模式」→ 写入 **execution_profile**，选图。
- **第 2 层**：该执行模式内部，按该模式自己的规则运行（不在一张图里切路径）。

即：**不是「同一图里切路径」，而是「先选图，再跑图」。**

由此直接消除的四个乱点：

| 原问题 | 解决方式 |
|--------|----------|
| 两套路径挤在一张图里 | 先选 graph profile，再跑对应图 |
| 图太大太杂 | 每张图只保留一种执行哲学 |
| 概念叠在一起 | 入口路由 / Supervisor / Agent / 子图分层明确 |
| 开关太多 | 多个 bool 收敛为单一 execution profile + 少量能力开关 |

---

## 2. 目标架构：三张业务图 + 极薄入口

### 2.1 Entry Layer（唯一入口，极薄）

职责仅三件事：

1. Airlock / Governor 前置校验
2. 基于 scene_type / 风险级别 / narrative_only 判定 **execution_profile**
3. 写入 execution_profile，将请求发往对应 graph

**不负责**：编排、任务拆解、Agent handoff。

**Profile 建议**（最终收敛为前三个）：

| Profile | 含义 | 过渡期可选 |
|---------|------|------------|
| **CHAT_FAST** | 显式聊天快路径 | — |
| **WORKFLOW_SUPERVISED** | AUTO / code / translate 统一监督流 | — |
| **INTENT_EXEC** | 执行意图专用 | — |
| — | — | WORK_CODE_DIRECT、WORK_TRANSLATE_DIRECT（过渡后删除） |

### 2.2 Chat Graph（独立快路径图）

- **专门处理显式聊天**，不混入 Supervisor、planner、tool_planner、intent 子图。
- **固定链路**：`START → chat_agent → light_guard(optional) → renderer → END`
- **约束**：不进 Supervisor、不进 Judge（除非后续明确需求）、不进执行级安全链、不使用 active_agent_id 动态分发。
- **目标**：低延迟、低心智负担、强边界。

### 2.3 Supervised Graph（AUTO / code / translate 统一图）

- **只承载**「Supervisor → 单专家 Agent → Judge → Renderer」。
- **固定链路**：`START → supervisor_dispatch → agent_dispatch → judge_agent → renderer → END`
- **agent_dispatch**：trusted handler 入口，根据 agent_runtime.active_agent_id 调度到 code_agent_handler / translate_agent_handler（及后续 retrieval_agent_handler 等）；**不是** model node。
- **本图内不包含**：planner_constraints、tool_planner、safety_verifier 长链（执行意图走 Intent Graph）。

### 2.4 Intent Graph（执行意图专用图）

- **执行意图单独一张图**，不与 AUTO/多 Agent 图混用。
- **固定链路**：`START → intent_agent_entry → intent_agent_subgraph → safety_verifier → judge_agent → renderer → END`
- **intent_agent_subgraph** 内部封装：`core_reasoner_primary → planner_constraints → tool_planner`，不暴露在主图 edges 上。

---

## 3. 概念拆开：五类概念不再混名

| 概念 | 定义 | 示例 |
|------|------|------|
| **Scene** | 入口语义分类，只决定 execution_profile 或默认偏好 | SCENE_CHAT, SCENE_WORK_CODE, SCENE_EXEC_INTENT, SCENE_AUTO |
| **Execution Profile** | 运行时真入口，决定「跑哪张图」 | CHAT_FAST, WORKFLOW_SUPERVISED, INTENT_EXEC |
| **Agent** | 语义角色，只代表职责；不是 LangGraph node / handler_id / model_id | chat_agent, code_agent, translate_agent, intent_agent, judge_agent |
| **Handler** | 可信执行器，负责控图和状态写入；只有 handler 可写控制字段 | supervisor_dispatch_handler, agent_dispatch_handler, judge_agent_handler |
| **Subgraph** | 某 Agent 的内部执行实现，不与主图 scene 路由混在一起 | intent_agent_subgraph, code_refine_subgraph |

---

## 4. 开关收敛

### 4.1 必删

- **MULTI_AGENT_ENABLED**
- **HOST_GRAPH_SCENE_ROUTER_ENABLED**

二者本质是在切换「系统哲学」，不应为 bool 开关。

### 4.2 新/改为主控 + 能力开关

| 新/改 | 含义 |
|-------|------|
| **execution_profile**（运行时） | 由入口层写入，唯一真相；决定跑哪张图 |
| **HOST_EXECUTION_PROFILE_STRATEGY** | 入口策略（如 stable） |
| **SUPERVISOR_MODE** | rule \| llm（原 SUPERVISOR_AUTO_ENABLED 收敛为此） |
| **INTENT_EXEC_MODE** | chain \| subgraph（原 INTENT_SUBGRAPH_ENABLED 收敛为此） |
| **READONLY_PARALLEL_MODE** | off \| shadow \| on |

---

## 5. 状态契约收口

以下提升为一等运行时字段，避免一半在 payload、一半在 GraphState、一半隐式：

- **execution_profile**: str — 入口层写，agent 不可改
- **scene_type**: str
- **active_agent_id**: str | None — 仅 Supervisor / trusted handler 可改
- **need_judge**: bool — 由 profile 或 supervisor plan 决定
- **risk_mode**: str
- **agent_runtime**: dict
- **judge_result**: dict | None — 仅 judge_agent_handler 可写
- **committed_route_token** — 仅 Governor 可写；**完全退出 Agent 路由语义**，不参与多 Agent 内部 handoff

原则：execution_profile 决定跑哪张图；active_agent_id 只在 Supervised Graph 内有效。

---

## 6. M-MA5 / M-MA6：暂停扩张，回退到稳定最小面

### 6.1 现在只保留（且仅存在于 WORKFLOW_SUPERVISED 图内）

- supervisor_dispatch_handler
- agent_dispatch_handler（见下）
- code_agent_handler、translate_agent_handler、judge_agent_handler

### 6.2 暂缓（冻结，不进入主线）

- ~~code_agent ReAct~~（**P9 已封板**）
- ~~translate_agent ReAct~~（**P10 纳入，待实施**）
- graph_executor 的全局动态 agent 路由
- 通用 react_scratchpad
- 多 Agent 循环 handoff

### 6.3 唯一例外

- **intent_agent** 可在其**子图内部**做局部 ReAct（工具循环）；不做「全系统 ReAct 运行时」。

---

## 7. 落地改法（按文件）

| 文件 | 方向 |
|------|------|
| **graph_config_builder.py** | 不再构建一张万能图。拆成：`build_entry_graph()`、`build_chat_graph()`、`build_supervised_graph()`、`build_intent_graph()`；入口按 execution_profile 选 graph。删除 `if MULTI_AGENT_ENABLED: ... else: ...` 式双轨。 |
| **graph_runtime.py** | 职责：初始化统一 runtime state；按 execution_profile 加载对应 graph contract。不再塞「多 agent 开关标记」。 |
| **graph_executor.py** | 不做全局动态图调度器。只保留：trusted handler 控制字段白名单；**仅在 Supervised Graph 内**支持 agent_dispatch 调度到具体 agent handler。动态调度不是 GraphExecutor 的全局默认行为。 |
| **supervisor_dispatch_handler.py** | 职责收敛：读 scene/user_input/profile → 输出并校验 proposal → 写 agent_runtime.plan、active_agent_id、need_judge。不承担 graph 选择（已在入口层完成）。 |
| **agent_dispatch_handler.py**（新增） | 将 executor 内 _dispatch_to_agent 逻辑收口到此。职责：active_agent_id → handler_id → invoke handler → 结果 sanitize → 写 artifact。Agent 分派为显式节点，非 executor 隐藏逻辑。 |
| **intent_agent_handler.py** | 只负责调用 intent_agent_subgraph；不与 code/translate 共用同一套 agent runtime handoff。 |
| **chat_handler.py** | 双层判定：第一层强规则 fast-pass（narrative_only、无工具/确认/执行动词/结构化任务诉求）；第二层落不到 fast-pass 再走 classifier。目标：把纯聊天锁死在 CHAT_FAST。 |

---

## 8. 最小运行路径：三句话可说清

任意请求的执行路径必须可用下面三句话之一说清：

1. **聊天**：「显式聊天在入口层打成 CHAT_FAST，只跑 chat_agent → light_guard → renderer。」
2. **AUTO / 工作辅助**：「AUTO 或代码/翻译类在入口层进入 WORKFLOW_SUPERVISED，Supervisor 选一个专家 Agent，专家产出工件，Judge 收口，Renderer 输出。」
3. **执行意图**：「执行意图在入口层进入 INTENT_EXEC，跑 Intent 子图、安全校验、Judge、Renderer，不与聊天快路径混图。」

若无法用这三句解释，则设计未收口。

---

## 9. 路由定版（入口层映射）

| Scene / 输入 | Execution Profile | Graph |
|--------------|-------------------|-------|
| SCENE_CHAT（且 fast-pass 命中） | CHAT_FAST | chat_graph |
| SCENE_WORK_CODE / SCENE_WORK_TRANSLATE / SCENE_AUTO | WORKFLOW_SUPERVISED | supervised_graph |
| SCENE_EXEC_INTENT | INTENT_EXEC | intent_graph |

---

## 10. 实施顺序（P0～P16）

| 阶段 | 内容 | 验收状态 |
|------|------|----------|
| **P0** | 冻结扩张：暂停 M-MA6 全局 ReAct、全局 dynamic executor routing、多 Agent 循环 handoff、code_agent ReAct。 | — |
| **P1** | 图拆分：实现 chat_graph、supervised_graph、intent_graph；入口层按 profile 选图。先规则版 Supervisor。 | 完成 |
| **P2** | 开关收敛：删除 MULTI_AGENT_ENABLED、HOST_GRAPH_SCENE_ROUTER_ENABLED；引入 execution_profile + SUPERVISOR_MODE + INTENT_EXEC_MODE。 | 完成 |
| **P3** | 将 code/translate 从旧主图迁入 supervised_graph，多 Agent 语义仅在监督图内成立。 | 完成 |
| **P4** | Intent 子图透明封装：core_reasoner_primary → planner_constraints → tool_planner 折叠进 intent_agent_subgraph，主图不再暴露这些边。 | **完成并正式验收通过**（2026-03 封板） |
| **P5** | Supervisor 决策升级：rule/llm 双模，灰度与回退（不改三图、不引入 ReAct）。 | **完成并正式验收通过**（2026-03 封板） |
| **P6** | 仅在 intent_agent_subgraph 内试点局部循环（硬边界、失败码、step 级 trace）。 | **完成并正式验收通过**（2026-03 封板） |
| **P7** | 治理与稳定性基线：预算契约、统一回退矩阵、观测聚合、压测与故障注入；不改三图、不扩 executor、不新增 Agent。 | **完成并正式验收通过**（2026-03 封板） |
| **P8** | 第五轮正式基线：subgraph/local loop 全量 ledger 聚合，补全 P7 遗留观测缺口；范围与验收以 P8_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P9** | 第六轮正式基线：code_agent 受控 ReAct 化；范围与验收以 P9_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P10** | 第七轮正式基线：translate_agent 受控 ReAct 化；范围与验收以 P10_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P11** | 第八轮正式基线：retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式）；范围与验收以 P11_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P12** | 第九轮正式基线：retrieval_agent 直接替换为 ReAct 模式（无开关，无双模式）；范围与验收以 P12_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P13** | 第十轮正式基线：tool_registry 工具实现升级（search_knowledge 真实接入 + retrieval_agent 专属工具扩展）；范围与验收以 P13_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P14** | 第十一轮正式基线：judge_agent ReAct 化；范围与验收以 P14_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P15** | 第十二轮正式基线：Supervisor LLM 提案 + graph_executor 动态路由；范围与验收以 P15_SCOPE 为准。 | **完成并正式验收通过**（2026-03 封板） |
| **P16** | 第十三轮正式基线：**最终封板**（技术债清理、agent_dispatch active_agent_id 同步与 allowlist 对齐、数据流收口）；范围见 dataflow_report §十三。 | **完成并正式验收通过**（2026-03 封板） |

**基线说明**：
- **第一轮正式基线**：P1～P4，已封板。P4 范围与契约以 **MULTI_AGENT_P4_SCOPE_2026-03.md** 为准。
- **第二轮正式基线**：P5，已封板。P5 范围、8 条验收标准与实施建议以 **MULTI_AGENT_P5_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P5_SEAL_RESULT_2026-03.md** 为准。
- **第三轮正式基线**：P6，完成并正式验收通过，已封板。P6 范围与验收以 **MULTI_AGENT_P6_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md** 为准。
- **第四轮正式基线**：P7，完成并正式验收通过，已封板。P7 范围与验收以 **MULTI_AGENT_P7_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P7_SEAL_RESULT_2026-03.md** 为准。
- **第五轮正式基线**：P8，完成并正式验收通过，已封板。P8 范围与验收以 **MULTI_AGENT_P8_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P8_SEAL_RESULT_2026-03.md** 为准。
- **第六轮正式基线**：P9，完成并正式验收通过，已封板。P9 范围与验收以 **MULTI_AGENT_P9_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P9_SEAL_RESULT_2026-03.md** 为准。
- **第七轮正式基线**：P10，完成并正式验收通过，已封板。P10 范围与验收以 **MULTI_AGENT_P10_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P10_SEAL_RESULT_2026-03.md** 为准。
- **第八轮正式基线**：P11，完成并正式验收通过，已封板。P11 范围与验收以 **MULTI_AGENT_P11_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P11_SEAL_RESULT_2026-03.md** 为准。
- **第九轮正式基线**：P12，完成并正式验收通过，已封板。P12 范围与验收以 **MULTI_AGENT_P12_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P12_SEAL_RESULT_2026-03.md** 为准。
- **第十轮正式基线**：P13，完成并正式验收通过，已封板。P13 范围与验收以 **MULTI_AGENT_P13_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P13_SEAL_RESULT_2026-03.md** 为准。
- **第十一轮正式基线**：P14，完成并正式验收通过，已封板。P14 范围与验收以 **MULTI_AGENT_P14_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P14_SEAL_RESULT_2026-03.md** 为准。
- **第十二轮正式基线**：P15，完成并正式验收通过，已封板。P15 范围与验收以 **MULTI_AGENT_P15_SCOPE_2026-03.md** 为准；验收结论以 **MULTI_AGENT_P15_SEAL_RESULT_2026-03.md** 为准。
- **第十三轮正式基线**：P16，完成并正式验收通过，已封板（**多智能体重构升级终点封板**）。P16 范围与数据流收口见 **dataflow_report_20260311.md** §十三。
- **当前阶段**：**多智能体重构升级已完成最终封板**；P1～P16 已全部形成完整封板链路。

---

## 11. 与现有文档的关系

- **MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md**：保留为能力与契约参考；**实施顺序与架构形态以本文档为准**，先图拆分再扩能力。
- **MULTI_AGENT_ARCH_SIMPLIFICATION_2026-03.md**：合并进本文档，简化方案以「三张图 + execution_profile」为准。
- **MULTI_AGENT_P4_SCOPE_2026-03.md**：P4 范围、验收标准与下游契约；P4 已验收封板。
- **MULTI_AGENT_P5_SCOPE_2026-03.md**：P5 范围、8 条验收标准与实施建议；P5 已验收封板。
- **MULTI_AGENT_P5_SEAL_RESULT_2026-03.md**：P5 验收结论（8 条逐条结论、测试覆盖、冻结非目标）。
- **MULTI_AGENT_P6_SCOPE_2026-03.md**：P6 范围、唯一目标（窄定义）、验收标准与冻结非目标；P6 已验收封板。
- **MULTI_AGENT_P6_SEAL_RESULT_2026-03.md**：P6 验收结论（6 条逐条结论、测试覆盖、观测约定、冻结非目标）。
- **MULTI_AGENT_P6_1_LOCAL_LOOP_CONTRACT_2026-03.md**：P6-1 局部循环契约与最小闭环设计；边界字段、失败码、subgraph_trace step 级、入口/出口不变；P6 实现契约依据。
- **MULTI_AGENT_P6_2_IMPLEMENTATION_2026-03.md**：P6-2 实现目标、最小实现面、验收 5 条；实施与验收依据。
- **MULTI_AGENT_P6_3_INTEGRATION_ACCEPTANCE_2026-03.md**：P6-3 集成验收补齐（4 条）；已通过并封板。
- **MULTI_AGENT_P7_SCOPE_2026-03.md**：P7 治理与稳定性基线；范围、8 条验收标准、实现约束、冻结非目标；P7 已验收封板。
- **MULTI_AGENT_P7_SEAL_RESULT_2026-03.md**：P7 验收结论（8 条逐条结论、3.4/3.6 收窄口径说明、测试与文档覆盖、冻结非目标）。
- **MULTI_AGENT_P7_2_FAILURE_MATRIX_2026-03.md**：P7-2 三图失败收口矩阵；失败位置、类型、收口策略、可见输出。
- **MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE_2026-03.md**：P7-4 压测与故障注入基线；三图故障注入用例索引、压测结果摘要模板与最小结果示例。
- **MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE_2026-03.md**：P7-3 当前统一观测面范围边界；最小观测面 vs subgraph/local loop 全量聚合。
- **MULTI_AGENT_P7_ACCEPTANCE_PREVIEW_2026-03.md**：P7 验收汇总预审；8 条逐条定性、3.4/3.6 口径；已由 P7_SEAL_RESULT 正式验收取代。
- **MULTI_AGENT_P8_SCOPE_2026-03.md**：P8 第五轮正式基线；范围与验收（定稿）；唯一目标为 subgraph/local loop 全量 ledger 聚合（P8-1），5 条验收标准已写死；P8 已封板。
- **MULTI_AGENT_P8_SEAL_RESULT_2026-03.md**：P8 验收结论（5/5 验收满足、ledger 与聚合范围、与 P7 兼容性说明、冻结非目标）；第五轮正式基线 = P8。
- **MULTI_AGENT_P9_SCOPE_2026-03.md**：P9 第六轮正式基线；范围与验收（定稿）；唯一目标为 code_agent 受控 ReAct 化（P9-1），3 条验收标准已写死；P9 已封板。
- **MULTI_AGENT_P9_SEAL_RESULT_2026-03.md**：P9 验收结论（3 条逐条结论、44 用例全通过、与 P8 兼容性说明、冻结非目标）；第六轮正式基线 = P9。
- **MULTI_AGENT_P10_SCOPE_2026-03.md**：P10 第七轮正式基线；范围与验收（定稿）；唯一目标为 translate_agent 受控 ReAct 化（P10-1），3 条验收标准已写死；已封板。
- **MULTI_AGENT_P10_SEAL_RESULT_2026-03.md**：P10 验收结论（3 条逐条结论、45 用例全通过、与 P9 兼容性说明、冻结非目标）；第七轮正式基线 = P10。
- **MULTI_AGENT_P11_SCOPE_2026-03.md**：P11 第八轮正式基线；范围与验收（定稿）；唯一目标为 retrieval_agent 最小接入 Supervised Graph（legacy_chain 模式，P11-1），3 条验收标准已写死；已封板。
- **MULTI_AGENT_P11_SEAL_RESULT_2026-03.md**：P11 验收结论（3 条逐条结论、58 用例全通过、与 P10 兼容性说明、冻结非目标）；第八轮正式基线 = P11。
- **MULTI_AGENT_P12_SCOPE_2026-03.md**：P12 第九轮正式基线；范围与验收（定稿）；唯一目标为 retrieval_agent 直接替换为 ReAct 模式（无开关，无双模式，P12-1），2 条验收标准已写死；已封板。
- **MULTI_AGENT_P12_SEAL_RESULT_2026-03.md**：P12 验收结论（2 条逐条结论、85 用例全通过、与 P11 兼容性说明、冻结非目标）；第九轮正式基线 = P12。
- **MULTI_AGENT_P13_SCOPE_2026-03.md**：P13 第十轮正式基线；范围与验收（定稿）；唯一目标为 tool_registry 工具实现升级（search_knowledge 真实接入 + retrieval_agent 专属工具扩展，P13-1），3 条验收标准已写死；已封板。
- **MULTI_AGENT_P13_SEAL_RESULT_2026-03.md**：P13 验收结论（3 条逐条结论、85 用例全通过、与 P12 兼容性说明、冻结非目标）；第十轮正式基线 = P13。
- **MULTI_AGENT_P14_SCOPE_2026-03.md**：P14 第十一轮正式基线；judge_agent ReAct 化；范围与验收；已封板。
- **MULTI_AGENT_P14_SEAL_RESULT_2026-03.md**：P14 验收结论；第十一轮正式基线 = P14。
- **MULTI_AGENT_P15_SCOPE_2026-03.md**：P15 第十二轮正式基线；Supervisor LLM 提案 + graph_executor 动态路由；范围与验收；已封板。
- **MULTI_AGENT_P15_SEAL_RESULT_2026-03.md**：P15 验收结论；第十二轮正式基线 = P15。
- **dataflow_report_20260311.md**：系统数据流全景报告；P16 多智能体分派数据流、active_agent_id 同步与 allowlist 对齐见 §十三；**P16 = 多智能体重构升级终点封板**。
- **MULTI_AGENT_SEAL_RESULT.md**：在完成 P1～P2 后更新为「新架构下的封测结论」。

---

## 12. 实施前必须冻结的工程约束

- execution_profile 仅入口层可写，后续只读
- Entry Layer 为前置路由层，不承载业务编排
- graph_executor 不感知业务 agent 语义
- agent_dispatch_handler 仅能在当前 graph allowlist 内分派
- fallback / 降级路径按 profile 固化，不允许跨图逃逸

---

## 13. P1/P2 任务清单与文件级改造顺序

以下为可直接排期、可勾选执行的任务清单；**文件级顺序**满足依赖关系，按序实施可避免破坏现有行为。

### 13.1 P1：图拆分 — 任务清单

| # | 任务项 | 产出/验收 | 依赖 |
|---|--------|-----------|------|
| P1-1 | 定义 execution_profile 常量与解析函数 | 新模块：`PROFILE_CHAT_FAST` / `PROFILE_WORKFLOW_SUPERVISED` / `PROFILE_INTENT_EXEC`；`resolve_execution_profile(scene_type, narrative_only?, …)` 返回 profile | 无 |
| P1-2 | GraphState 增加 execution_profile 字段 | `graph_contract.GraphState` 增加 `execution_profile: str \| None`，仅入口层写入 | P1-1 |
| P1-3 | 实现 build_chat_graph() | 仅含 chat 快路径：START → chat_agent → light_guard(optional) → renderer → END；无 scene_router/supervisor/agent_dispatch | P1-1 |
| P1-4 | 实现 build_supervised_graph() | 固定链路：START → supervisor_dispatch → agent_dispatch → judge_agent → renderer → END；无 planner_constraints/tool_planner/intent 子图 | P1-1 |
| P1-5 | 实现 build_intent_graph() | 固定链路：START → intent_agent_entry → intent_agent_subgraph(或占位) → safety_verifier → judge_agent → renderer → END | P1-1 |
| P1-6 | 入口层按 profile 选图并写入 execution_profile | `chat_handler_complex`：在 resolve scene 后调用 `resolve_execution_profile()`，按 profile 调用 `build_chat_graph` / `build_supervised_graph` / `build_intent_graph`，将 execution_profile 写入 payload/state | P1-2, P1-3, P1-4, P1-5 |
| P1-7 | graph_runtime 初始化 state 写入 execution_profile | `run_graph` 或 `build_initial_state` 从 graph_config / payload 读取 execution_profile 并写入 GraphState | P1-2, P1-6 |
| P1-8 | 废弃单图双轨：build_chat_graph_config 收敛为按 profile 选 builder | 删除 `use_scene_router`/`HOST_GRAPH_SCENE_ROUTER_ENABLED` 在「选哪张图」上的作用；入口只根据 execution_profile 选 build_*，可保留 `build_chat_graph_config` 为薄包装（内部按 profile 调 build_*）以兼容调用方 | P1-6 |

**P1 最小可交付**：P1-1～P1-7 完成即「先选图再跑图」打通；P1-8 可与 P2 开关删除一并做。

### 13.2 P2：开关收敛 — 任务清单

| # | 任务项 | 产出/验收 | 依赖 |
|---|--------|-----------|------|
| P2-1 | 删除 MULTI_AGENT_ENABLED 的读取与分支 | `supervisor_dispatch_handler.py`、`graph_config_builder.py`、`observability/metrics.py` 中不再读取或上报 MULTI_AGENT_ENABLED；路由逻辑仅依赖 execution_profile | P1 最小可交付 |
| P2-2 | 删除 HOST_GRAPH_SCENE_ROUTER_ENABLED / use_scene_router | `graph_config_builder.py` 中移除 scene_router_enabled / use_scene_router；选图完全由入口层 execution_profile 决定 | P1-8 或 P2-1 |
| P2-3 | 引入 SUPERVISOR_MODE（rule \| llm） | 环境变量或配置项；Supervisor 行为按 mode 选择 rule/llm；原 SUPERVISOR_AUTO_ENABLED 语义收敛到此 | P2-1 |
| P2-4 | 引入 INTENT_EXEC_MODE（chain \| subgraph） | 环境变量或配置项；执行意图链式/子图模式收敛到此；原 INTENT_SUBGRAPH_ENABLED 语义收敛到此 | P2-1 |
| P2-5 | 文档与配置示例更新 | `.env.example`、REFACTOR §4、运维文档中删除旧开关说明，补充 execution_profile / SUPERVISOR_MODE / INTENT_EXEC_MODE | P2-1～P2-4 |

### 13.3 文件级改造顺序（推荐执行顺序）

按**依赖从底向上**、**先契约再实现再入口**的顺序动文件，避免循环依赖与行为回退。

| 阶段 | 顺序 | 文件 | 改造内容 |
|------|------|------|----------|
| **契约与 profile** | 1 | 新增 `host_brain/app/execution_profile.py`（或 `host_brain/integrations/execution_profile.py`） | 定义 PROFILE_* 常量；实现 `resolve_execution_profile(scene_type, narrative_only=None, …)`，映射到 §9 路由定版表 |
| | 2 | `host_brain/integrations/graph_contract.py` | GraphState 增加 `execution_profile: str \| None`；`build_initial_state`/校验处支持从 payload 注入 |
| **图配置拆分** | 3 | `host_brain/app/graph_config_builder.py` | 新增 `build_chat_graph()`、`build_supervised_graph()`、`build_intent_graph()`；从现有 `build_chat_graph_config` 中抽出三条独立实现，去掉「if use_scene_router」式双轨 |
| | 4 | `host_brain/integrations/graph_runtime.py` | 初始化 state 时写入 execution_profile（从 graph_config.graph_meta 或 payload 取）；不再塞「多 agent 开关」字段 |
| **入口层** | 5 | `host_brain/app/chat_handler_complex.py` | Governor 后、建图前：调用 `resolve_execution_profile(scene_type, narrative_only, …)`；按 profile 调用 `build_chat_graph` / `build_supervised_graph` / `build_intent_graph`；将 execution_profile 写入传给 run_graph 的 payload；删除对「scene_router 开关决定建哪张图」的依赖 |
| **P2 收口** | 6 | `host_brain/app/handlers/supervisor_dispatch_handler.py` | 删除对 MULTI_AGENT_ENABLED 的读取；行为仅依赖 state 内 execution_profile / scene_type |
| | 7 | `host_brain/app/graph_config_builder.py` | 删除 HOST_GRAPH_SCENE_ROUTER_ENABLED、use_scene_router；`build_chat_graph_config` 改为按调用方传入的 execution_profile 调对应 build_*（或删除该函数，调用方直接调 build_*） |
| | 8 | `host_brain/observability/metrics.py` | 删除 MULTI_AGENT_ENABLED 上报；可增加 execution_profile 维度（可选） |
| | 9 | 配置/文档 | `.env.example`、REFACTOR §4、运维说明：旧开关移除，新开关 SUPERVISOR_MODE / INTENT_EXEC_MODE 说明 |

### 13.4 依赖关系简图

```
P1:  execution_profile 模块 → graph_contract(GraphState) → graph_config_builder(build_* 三图)
                                                              ↓
     chat_handler_complex(resolve_profile + 选图 + payload) ← 三图
                                                              ↓
     graph_runtime(initial_state 写 execution_profile)

P2:  P1 最小可交付 → 删 MULTI_AGENT_ENABLED / HOST_GRAPH_SCENE_ROUTER → 新开关 SUPERVISOR_MODE / INTENT_EXEC_MODE → 文档
```

### 13.5 与 §7 落地改法的对应关系

| §7 文件 | P1/P2 对应任务 |
|---------|----------------|
| graph_config_builder.py | P1-3, P1-4, P1-5, P1-8；P2-2, P2-5 |
| graph_runtime.py | P1-7 |
| graph_executor.py | P1 不强制改；P3 迁入 supervised 后确认「仅 Supervised 图内 agent_dispatch」 |
| supervisor_dispatch_handler.py | P2-1（删 MULTI_AGENT_ENABLED） |
| agent_dispatch_handler.py（新增） | P3 或 P1 后：将 executor 内 _run_agent_dispatch 收口为独立 handler（可选在 P1 后做） |
| intent_agent_handler.py | P1-5 中 intent_graph 需调用 intent 子图时涉及 |
| chat_handler.py | P1-6 入口层选图；双层判定（fast-pass + classifier）可在 P1 或 P3 细化 |

*本节为「P1/P2 任务清单与文件级改造顺序」的权威拆解；实施时以本节顺序为准，可与 §10 阶段对照排期。*

---

## 14. 需补充冻结的实现细则

本节为实施前必须补充并冻结的实现细则，用于消除 P1/P2 落地过程中的剩余歧义；若本节与其他章节表述存在冲突，以本节为准。

### 14.1 Entry Layer 不是业务图，不实现 `build_entry_graph()`

Entry Layer 的定位是**前置路由层 / graph selector**，不是第四张业务图。其职责仅限于：

1. Airlock / Governor 前置校验
2. 解析 `scene_type`、`narrative_only`、风险级别等输入信号
3. 调用 `resolve_execution_profile()` 得到 `execution_profile`
4. 选择并调用对应的 graph builder（`build_chat_graph()` / `build_supervised_graph()` / `build_intent_graph()`）
5. 将 `execution_profile` 写入 payload / GraphState 后启动对应图执行

**明确约束：**
- 不新增 `build_entry_graph()`
- Entry Layer 不承载业务编排，不引入 Supervisor / Agent / Judge / Subgraph 节点
- Entry Layer 不维护业务 edges，不形成新的“隐形万能图”
- `graph_config_builder.py` 只负责三张业务图：
  - `build_chat_graph()`
  - `build_supervised_graph()`
  - `build_intent_graph()`

### 14.2 `SCENE_CHAT` 未命中 fast-pass 时的固定去向

为避免实现阶段对聊天类请求发生分流歧义，`SCENE_CHAT` 的 execution_profile 规则补充如下：

#### 14.2.1 固定规则

- `SCENE_CHAT` 且 **fast-pass 命中** → `CHAT_FAST`
- `SCENE_CHAT` 且 **fast-pass 未命中**，但未被明确识别为执行型/工具型请求 → **仍然进入 `CHAT_FAST`**
- 只有当分类器或规则层**明确判定为执行意图**时，才允许从 `SCENE_CHAT` 转入 `INTENT_EXEC`
- **不得因为 fast-pass 未命中而默认转入 `WORKFLOW_SUPERVISED`**

#### 14.2.2 设计理由

`fast-pass` 的作用是优先锁定纯聊天快路径，而不是作为“未命中即升级治理链”的触发器。
否则将导致：
- 普通聊天误入监督图
- 聊天延迟升高
- 用户感知不稳定
- 原本已收口的执行模式再次互相污染

#### 14.2.3 实现要求

`resolve_execution_profile()` 至少满足以下判定顺序：

1. 若 `scene_type == SCENE_EXEC_INTENT` → `INTENT_EXEC`
2. 若 `scene_type == SCENE_CHAT` 且 fast-pass 命中 → `CHAT_FAST`
3. 若 `scene_type == SCENE_CHAT` 且 fast-pass 未命中：
   - 若 classifier / 规则明确识别为执行型请求 → `INTENT_EXEC`
   - 否则 → `CHAT_FAST`
4. 若 `scene_type in {SCENE_WORK_CODE, SCENE_WORK_TRANSLATE, SCENE_AUTO}` → `WORKFLOW_SUPERVISED`

### 14.3 `execution_profile` 只以 payload/state 为真相源

`execution_profile` 必须单源化，避免 payload、graph_meta、运行时隐式字段形成多源竞争。

#### 14.3.1 真相源规则

- **唯一真相源**：入口层写入的 `payload["execution_profile"]`
- `graph_runtime` 只负责将其注入 `GraphState.execution_profile`
- `GraphState.execution_profile` 是运行时读取口径
- `graph_meta.execution_profile` 如存在，仅用于观测、断言、调试，不作为控制真相源

#### 14.3.2 明确约束

- 不允许在运行过程中由 handler / agent / subgraph 改写 `execution_profile`
- 不允许 `graph_runtime` 自行推断或覆盖 `execution_profile`
- 不允许出现“payload 与 graph_meta 各自提供 execution_profile，谁优先由实现者自行决定”的情况
- 若发现 `payload.execution_profile` 与 `graph_meta.execution_profile` 不一致：
  - 以 `payload.execution_profile` 为准
  - 同时记录审计事件或告警日志

#### 14.3.3 建议实现口径

推荐顺序如下：

1. 入口层调用 `resolve_execution_profile()`
2. 将结果写入 `payload["execution_profile"]`
3. 选择对应 graph builder
4. `graph_runtime.build_initial_state()` 从 payload 注入 `GraphState.execution_profile`
5. 后续所有节点只读该字段，不可覆盖

### 14.4 fallback / 降级矩阵按 profile 固化，不允许跨图逃逸

为防止不同图在异常处理时重新耦合，所有降级路径必须按 `execution_profile` 固化，且**不得跨图逃逸**。

#### 14.4.1 固定降级矩阵

| Execution Profile | 失败位置 | 固定降级策略 |
|-------------------|----------|--------------|
| `CHAT_FAST` | `chat_agent` / `light_guard` / `renderer` | 在本图内返回安全最小回复或最小可渲染输出；不得切到 `WORKFLOW_SUPERVISED` 或 `INTENT_EXEC` |
| `WORKFLOW_SUPERVISED` | `supervisor_dispatch` / `agent_dispatch` / `judge_agent` / `renderer` | 在本图内走 judge 降级、空工件收口或 renderer 降级；不得切到 `INTENT_EXEC` 或 `CHAT_FAST` |
| `INTENT_EXEC` | `intent_agent_subgraph` / `safety_verifier` / `judge_agent` / `renderer` | 在本图内进行安全收口、拒答、降级说明或最小安全输出；不得回跳 `CHAT_FAST` 或 `WORKFLOW_SUPERVISED` |

#### 14.4.2 明确约束

- **不允许跨图 fallback**
- **不允许“本图失败后改 execution_profile 再重跑另一张图”**
- **不允许以异常处理名义重新进入旧主图长链**
- 所有 fallback 必须可审计，至少记录：
  - 当前 `execution_profile`
  - 失败节点 / 失败阶段
  - 降级目标
  - 是否产生最终用户可见输出

#### 14.4.3 工程要求

- `CHAT_FAST` 的失败处理优先保证低延迟和边界安全，不追求复杂补救
- `WORKFLOW_SUPERVISED` 的失败处理优先保证工件收口和可解释性
- `INTENT_EXEC` 的失败处理优先保证安全闭锁，不因补救而越权

### 14.5 `agent_dispatch_handler` 采用 graph-local allowlist

`agent_dispatch_handler` 是显式分派边界，必须只在**当前 graph 的 allowlist** 内调度 agent，不得使用全局 registry 任意跨图挑选 agent。

#### 14.5.1 规则

- `WORKFLOW_SUPERVISED` 图仅允许调度该图显式注册的 agent
- `agent_dispatch_handler` 不得直接调 `intent_agent_subgraph`
- 不允许通过 registry 绕过 graph 语义边界
- allowlist 必须由当前 graph config 或 graph-local registry 明确提供

#### 14.5.2 目标

防止出现“图虽然拆了，但 agent 仍可跨图乱跳”的隐性耦合，确保：
- 图的边界真实存在
- 分派范围可审计
- agent 能力扩展不会破坏 execution_profile 语义

### 14.6 `graph_executor` 不感知业务 agent 语义

`graph_executor` 保持通用执行器定位，不维护业务级 `agent_id -> handler_id` 映射，不承担业务 Agent 分派职责。

#### 14.6.1 保留职责

- 节点执行调度
- trusted handler 控制字段白名单
- 通用状态推进
- 通用异常捕获与观测

#### 14.6.2 禁止职责

- 不维护 `chat_agent` / `code_agent` / `translate_agent` / `intent_agent` 等业务语义映射
- 不在 executor 内实现 `_dispatch_to_agent()` 作为全局业务分派中心
- 不根据 `active_agent_id` 做跨图兜底路由
- 不感知某个 execution_profile 下应调用哪些业务 Agent

#### 14.6.3 业务分派归属

业务 Agent 的分派逻辑只允许存在于：

- `agent_dispatch_handler`（Supervised Graph 内）
- graph-local config / allowlist
- 业务层 handler 显式逻辑

不得重新下沉回 `graph_executor.py`。

---

**本节冻结后，P1/P2 的实现必须满足以下最终口径：**
- Entry Layer 负责“选图”，不是“跑业务图”
- `SCENE_CHAT` 默认稳定落在 `CHAT_FAST`，而不是因 fast-pass 未命中而升级到监督图
- `execution_profile` 单源、只读、不可运行中改写
- fallback 只在本图内收口，不允许跨图逃逸
- `agent_dispatch_handler` 是唯一显式 Agent 分派边界
- `graph_executor` 维持通用执行器定位，不重新成为业务总控中心

*本文档为「先选图再跑图」的权威可执行方案；具体排期与任务拆解由项目跟进。*
