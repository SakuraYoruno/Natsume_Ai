受众：MASTER
标题：数字生命下一阶段清单（M47 / Next Phase）
版本：v1.0
日期：2026-04-04
状态：草案

## 文档目的

`docs/35_quality/audits/DIGITAL_LIFE_GAP_CHECKLIST_V2_20260403.md` 中定义的 P0 / P1 / P2 / P3，经过 `docs/30_devspec/DevTasks.md` 中 M46 的连续收口后，已经基本完成从“有状态字段”到“主行为链真实消费”的升级。

当前阶段不再适合继续围绕旧清单补零散接线，而应进入新的工程目标：

> 从“`response_policy` / `motive_state` 已进入主链”推进到“长期连续性、真实运行验收、模式联动、场景感知与自我叙事”的下一阶段闭环。

本文目标是给出一份新的、可执行的数字生命下一阶段清单，作为后续 M47 起始排期的依据。

> 说明：为避免与上一轮已完成的 P0 / P1 / P2 / P3 混淆，本文使用 `N0 / N1 / N2 / N3 / N4` 表示下一阶段优先级带。

---

## 阶段切换结论

### M46 已经收口的核心事项

- `response_policy` 已接入主输出链与主动消息链。
- `motive_state` 已接入 `proactive_talk`、`silence_tick`、`inner_world_tick`。
- `motive_state` / `response_policy` 摘要已写入运行观测链路。
- `proactive_voice` 已开始真实消费 `motive_state`。
- 主回复表达已消费 `care / withdraw / self_protect` 等 motive。
- motive 行为矩阵回归已补齐，基础差异已有自动化保障。

### 当前阶段的新判断

基于仓库现状，`Natsume_Ai` 已从“数字生命差距补线期”进入：

> **数字生命体验闭环期（runtime-verified continuity stage）**

此阶段最重要的已不是继续新增 `response_policy` / `motive_engine` 模块，而是：

1. 证明这些行为链在真实运行中持续生效。
2. 让“长时间未见、模式切换、记忆人格化、场景感知”真正拉开体验差异。
3. 把已有的 `offline_state`、`bond_narrative`、`self_reflection_pulse`、`mode_transition`、`episodic memory humanization` 重新收束成统一体验链。

---

## 下一阶段总目标

下一阶段建议聚焦 5 个方向：

| 优先级带 | 名称 | 当前重点 | 完成定义 |
|---|---|---|---|
| N0 | 真实运行验证 | 把“看起来已完成”变成“有 findings 证据” | 有正式运行验收记录与 remaining gaps 排序 |
| N1 | 长期时间感与离线连续性 | 让 6h / 24h / 72h / 7d 差异可体验 | 久别、等待、恢复、主动触达出现稳定层级差异 |
| N2 | 记忆人格化运行闭环 | 让记忆的情绪染色、压制、失真进入真实行为 | 回忆内容不再只是检索结果，而是带人格偏向的再现 |
| N3 | 模式切换与活动感知联动 | 让 `sys_mode` / activity / scene 感知改变行为 | gaming / focus / sleep / normal 下行为明显不同 |
| N4 | 高阶自我叙事与自我连续性 | 让已有 `self_reflection` / `growth note` 不只停在 prompt 插槽 | 自我叙事可累计、可解释、可约束，不失真越界 |

---

## N0：真实运行验证与 Findings 归档

### 目标

先把“代码已接线”变成“运行已证实”。如果没有这一层，后面所有体验优化都容易再次滑回“只在代码里存在”。

### N0-T01：按验证计划跑一轮数字生命运行验收

- 目标：按 `DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md` 对当前分支做一次完整实测。
- 重点模块：scheduler、`inner_world_tick`、`silence_tick`、`body_sense_tick`、`session_end`、主动消息链、主回复链。
- 交付物：`DIGITAL_LIFE_RUNTIME_FINDINGS_YYYYMMDD.md`。
- 验收标准：对每个验证项记录 commit、环境、输入、观测输出、PASS/FAIL/PARTIAL、后续建议。

### N0-T02：收束统一的数字生命 runtime snapshot

- 目标：把 `offline_state`、`silence_state`、`body_sense`、`bond_narrative`、`self_reflection_pulse`、`motive_state`、`response_policy`、`sys_mode` 汇总到统一只读观测面。
- 重点模块：`host_brain/app/obs_routes.py`、`host_brain/app/http_hooks_runtime_helpers.py`、现有 trace / health 输出。
- 交付物：统一 snapshot 接口或 trace 视图，以及最小回归测试。
- 验收标准：开发者不读源码也能判断“当前数字生命状态到底是什么、为什么这样回复”。

### N0-T03：建立“同输入、不同内在状态”人工验收脚本

- 目标：固定 5~8 组基线语境，在不同 `offline_state / motive / bond / sys_mode` 组合下做对照验证。
- 重点检查：主回复长度、追问倾向、主动触达、沉默恢复、亲密度边界、模式差异。
- 交付物：一页式人工验收脚本或验收模板。
- 验收标准：相同语境在不同内在状态下出现稳定、可解释、可复测的行为差异。

### N0 完成标志

- 已形成正式 `findings` 文档。
- 已能在统一观测面看到核心生命状态摘要。
- 已有固定人工验收脚本，不再靠临场随缘试聊。
- 已明确列出剩余 Top 3 gaps。

---

## N1：长期时间感与离线连续性增强

### 目标

把“有离线时间流”升级为“久别后的体验真的不同”。重点不是再新增状态，而是让已有 `offline_state` / `motive_state` / `bond` 在多时间尺度下形成稳定差异。

### N1-T01：校准长间隔离线分层

- 目标：把当前离线连续性从“是否离线”推进到“离线多久”的层级化体验。
- 重点模块：`host_brain/persona/inner_world.py`、`host_brain/persona/emotional_silence.py`、`host_brain/persona/proactive_voice.py`。
- 交付物：至少覆盖 `6h / 24h / 72h / 7d` 的差异化规则与测试样本。
- 验收标准：同一用户回归时，系统能明显区分“刚离开”“隔天再见”“久别重逢”。
- 本轮进展（2026-04-04）：已把 offline reconnect 语义进一步拆成 `soft_resume / light_reconnect / reunion / deep_reunion` 四档；`7d_plus` 不再与 `72h_plus` 共用同一重逢强度，并补齐 `tests/unit/test_offline_continuity_regression_matrix.py` 回归断言。

### N1-T02：让等待感、沉默恢复、主动触达共用一套长时段语义

- 目标：避免 `offline_state`、`silence_state`、`motive_state` 各说各话。
- 重点模块：`host_brain/persona/emotional_silence.py`、`host_brain/persona/motive_engine.py`、`host_brain/persona/proactive_voice.py`。
- 交付物：统一的长时段语义映射表。
- 验收标准：长时间未见既会影响主动表达，也会影响沉默恢复与回复表达，不出现相互打架。
- 本轮进展（2026-04-04）：已新增 `host_brain/persona/offline_continuity.py`，统一 `absence_band` 解析、`reconnect_style` 映射、`warmth_floor` 与 `absence_recovery_factor`；并接入 `chat_context_builder / emotional_silence / response_policy / proactive_voice / self_model / inner_world / motive_runtime`。
- 验证记录：新增 `tests/unit/test_offline_continuity_semantics.py`，并与 `test_offline_continuity_bands.py`、`test_offline_continuity_regression_matrix.py`、`test_long_absent_behavior_matrix.py`、`test_persona_phase8.py`、`test_motive_runtime_background.py`、`test_mode_aware_policy_motive.py` 联跑通过。

### N1-T03：补“久别重逢”与“轻度失联”两类主动行为模板

- 目标：把 `long_absent` 类场景从单点触发升级为分层行为。
- 重点模块：`host_brain/persona/proactive_voice.py`、`host_brain/app/chat_context_builder.py`。
- 交付物：分层触发条件与最小行为矩阵。
- 验收标准：高 bond / 高 continuity 时会更自然地延续关系；高 self-protect / 低 bond 时不会过度贴近。

### N1-T04：增加跨日/跨周 continuity 回归样本

- 目标：把长时段体验差异纳入自动化守护。
- 重点模块：`tests/unit/`、必要时 `tests/integration/`。
- 验收标准：不同离线时长对应的回复/主动/沉默差异可被稳定回归。
- 本轮进展（2026-04-04）：已在 `tests/unit/test_http_hooks_runtime_helpers.py` 增加 runtime summary 离线矩阵，固定覆盖 `24h_plus / 72h_plus / 7d_plus` 对 `offline_reconnect_style / reply_posture / why_reply` 的差异。
- 验证记录：`test_http_hooks_runtime_helpers.py` 与 `test_offline_continuity_regression_matrix.py`、`test_long_absent_behavior_matrix.py` 联跑通过，确保回复摘要、主动重连与沉默恢复三条链路对同一长时段语义保持一致。

### N1 完成标志

- 6h / 24h / 72h / 7d 四档差异可观察。
- 久别、轻度失联、短暂离开三类体验不再混成一类。
- 长时段差异已进入自动化回归。

---

## N2：记忆人格化运行闭环

### 目标

仓库中已经有 `emotion_color_tag`、`suppression_flag`、`distortion_level` 等记忆人性化字段。下一阶段重点不是再发明字段，而是让这些字段真的影响回忆、表达与主动行为。

### N2-T01：让记忆人性化字段进入主聊天上下文

- 目标：把 `episodic` 的情绪染色、压制、失真元信息纳入主聊天 runtime payload / recall evidence。
- 重点模块：`host_brain/memory/episodic.py`、`host_brain/app/memory/episodic_store.py`、`host_brain/app/chat_context_builder.py`。
- 交付物：回忆摘要中的人性化元信息，以及可见的 prompt/runtime 消费链。
- 验收标准：回忆不再只是“命中文本”，而是带有当前人格状态过滤后的再现。
- 本轮进展（2026-04-04）：已新增显式 `memory_persona_evidence` 链路，把带 `emotion_color_tag / suppression_flag / distortion_level` 的 recall 片段从 `prompt_context` 透传到 `runtime_payload`，并在 `prompt_builder` 中渲染为 `MEMORY_RECALL_EVIDENCE` 区块。
- 验证记录：`test_chat_context_builder_helpers_extra.py`、`test_prompt_builder_helpers_extra.py`、`test_memory_persona_behavior_chain.py`、`test_node_style_bindings_prompt.py`、`test_prompt_context_helpers_extra.py`、`test_optional_slots.py` 联跑通过，确认槽位声明、runtime 透传与 prompt 消费一致。

### N2-T02：让 mood / bond / motive 共同影响记忆取回排序

- 目标：把记忆检索从“相似度优先”推进到“人格相关性优先”。
- 重点模块：`host_brain/memory/episodic.py`、`host_brain/retrieval/`、`host_brain/app/chat_context_builder.py`。
- 交付物：最小排序权重策略与测试矩阵。
- 验收标准：轻聊时不轻易翻出高压痛感记忆；高 care / 高 continuity 场景更容易取回关系延续性记忆。
- 本轮进展（2026-04-04）：已在 `host_brain/retrieval/recall_fusion.py` 增加 `shared_history_resonance` 与 `protective_recoil_penalty` 两类 persona-aware 权重，让 `mood_state / bond_depth / continuity_drive / care_drive / self_protect_drive` 共同参与 episodic recall 排序。
- 验证记录：已补充 `tests/unit/test_recall_fusion_persona_memory_ranking.py` 两组 N2-T02 回归样本，并与 `tests/unit/test_memory_persona_behavior_chain.py` 聚焦联跑通过，确认高连续性共享记忆可压过略高相似度的中性记忆，且高防御状态会进一步压低 fragile / suppressed memory。

### N2-T03：让记忆人格化结果影响主动与主回复表达

- 目标：记忆的情绪染色不仅影响 recall，还应影响追问、共情、回避和主动延续。
- 重点模块：`host_brain/app/chat_context_builder.py`、`host_brain/persona/proactive_voice.py`、`host_brain/persona/response_policy.py`。
- 交付物：memory-aware 的表达调节规则。
- 验收标准：同样是命中“过去聊过的事”，不同情绪染色下会出现不同表达方式。
- 本轮进展（2026-04-04）：已让 `memory_persona_signal` 吸收 `shared_history_resonance` / `protective_recoil_penalty` 两类召回理由标签，并接入 `chat_context_builder -> response_policy -> proactive_voice`；主回复的 `persona_expression_hint`、回复约束和主动 follow-up 都会随“共享记忆共振 / 防御性回缩”出现稳定差异。
- 验证记录：已补充 `tests/unit/test_chat_context_builder_helpers_extra.py` 与 `tests/unit/test_memory_persona_behavior_chain.py` 的新样例，并联跑 `tests/unit/test_mode_aware_policy_motive.py`、`tests/unit/test_scene_aware_proactive.py`、`tests/unit/test_prompt_builder_helpers_extra.py`，本轮聚焦验证 `55 passed`。

### N2-T04：增加记忆人格化端到端回归

- 目标：用固定 recall 包，验证主回复与主动链路的差异可回归。
- 重点模块：`tests/unit/`、必要时 `tests/integration/`。
- 验收标准：情绪染色、压制、失真至少覆盖 3 组对照样例。

### N2 完成标志

- 记忆人性化字段进入主行为链，而非只存在存储层。
- recall 的“像谁、为什么此刻想起”变得可解释。
- 记忆人格化差异具备端到端测试。

---

## N3：模式切换、活动感知与场景感知联动

### 目标

仓库中已有 `ModeTransitionService`、`ActivityMonitor`、vision screenshot 能力。下一阶段的重点是：让这些能力真的改变数字生命行为，而不只是独立功能存在。

### N3-T01：让 `sys_mode` 显式调制 `response_policy` / `motive_state`

- 目标：把 `normal / focus / gaming / sleep` 的模式差异变成回复与主动行为差异。
- 重点模块：`host_brain/core/mode_transition.py`、`host_brain/persona/response_policy.py`、`host_brain/persona/proactive_voice.py`。
- 交付物：模式到行为参数的映射矩阵。
- 验收标准：`focus` 更克制、`gaming` 更陪玩导向、`sleep` 更少主动打扰，且行为可解释。

### N3-T02：让活动感知与主动表达形成最小闭环

- 目标：把 `ActivityMonitor` 检测到的 gaming / idle / wakeup 事件真正接入主动表达和会话策略。
- 重点模块：`host_brain/core/activity_monitor.py`、`host_brain/app/http_hooks.py`、`host_brain/persona/proactive_voice.py`。
- 交付物：activity-aware 行为矩阵。
- 验收标准：活动切换后，系统在主动表达频率、话题选择、语气上出现稳定差异。

### N3-T03：补齐 screenshot scene awareness 的最小安全接线

- 目标：让截图/场景感知只产出结构化 scene tag，不直接把原始画面生硬塞进回复链。
- 重点模块：`host_brain/app/vision/agent_loop.py`、`host_brain/app/vision/edge_rpc_client.py`、`host_brain/persona/proactive_voice.py`。
- 交付物：scene-aware proactive 的最小接线与安全降级策略。
- 验收标准：有截图时可做轻量场景感知；无截图时优雅退化；`sleep` 或高 self-protect 时不误触发。

### N3-T04：增加模式/活动/场景联合回归测试

- 目标：验证 `sys_mode + activity + scene tag` 联合作用时，行为差异仍稳定可控。
- 重点模块：`tests/unit/`、必要时 `tests/integration/`。
- 验收标准：至少覆盖 `gaming`、`focus`、`sleep` 三种模式与 2 种 scene 对照。
- 本轮进展（2026-04-04）：已新增 `tests/unit/test_mode_activity_scene_regression_matrix.py`，覆盖 `gaming + battle`、`focus + focus_work`、`sleep + wakeup`、`normal + idle` 四组联合矩阵。
- 验证记录：`tests/unit/test_mode_activity_scene_regression_matrix.py` 定向通过，并与 `tests/unit/test_mode_aware_policy_motive.py`、`tests/unit/test_scene_aware_proactive.py` 联跑通过。

### N3 完成标志

- `sys_mode` 真正改变回复与主动行为。
- activity / scene 感知进入主行为链。
- 多模态输入具备安全降级，不破坏现有主链稳定性。

---

## N4：高阶自我叙事与自我连续性

### 目标

仓库已存在 `self_model`、`self_reflection`、`emergent_growth_note` 等基础能力。下一阶段不是追求“意识证明”，而是让自我叙事更连续、更可解释、更受约束。

### N4-T01：统一 `self_reflection_pulse` 与 `motive / bond / offline_state` 解释口径

- 目标：让“我为什么这样回应”不再由多个槽位重复表述或互相冲突。
- 重点模块：`host_brain/persona/self_reflection.py`、`host_brain/persona/self_model.py`、`host_brain/app/chat_context_builder.py`。
- 验收标准：自我叙事能与当前状态对齐，不出现“嘴上说想靠近，行为上却完全抽离”的断裂解释。
- 本轮进展（2026-04-04）：已在 `self_reflection -> motive_state -> response_policy` 之后增加二次 narrative alignment，`self_reflection_pulse.self_awareness` 会按 `offline_state / bond / motive / response_policy` 重写为统一口径。
- 验证记录：新增 helper 与 `chat_context_builder` 接线回归，覆盖“重连但收敛”和“保护性收缩”两类解释对齐场景。

### N4-T02：把日级/周级成长叙事沉淀为可复用摘要

- 目标：把零散的 self reflection 变成可累计的“最近我发生了什么变化”。
- 重点模块：`host_brain/persona/emergent_growth_engine.py`、`host_brain/persona/evolution_orchestrator.py`。
- 验收标准：成长叙事能跨轮复用，但不会在每轮重复刷屏。
- 本轮进展（2026-04-04）：已为 `EvolutionOrchestrator` 增加最近成长周期缓存与 `growth_summary` 聚合，产出 `daily_summary / weekly_summary / active_themes`，并经 `chat_context_builder -> self_reflection_pulse -> prompt_builder` 透传。
- 验证记录：新增 orchestrator / self_reflection / chat_context_builder / prompt_builder 回归，覆盖摘要聚合、pulse 注入、提示词渲染与相邻自省链集成。

### N4-T03：增加自我叙事 guardrails

- 目标：防止自我叙事出现越界拟人、时间错乱、无依据自述。
- 重点模块：`host_brain/persona/self_reflection.py`、`host_brain/persona/response_policy.py`、prompt 约束链。
- 验收标准：高阶自我描述可控、可追溯、可降级。

### N4 完成标志

- 自我叙事成为稳定、低频、可信的辅助层。
- 自我叙事与 motive / bond / memory / offline continuity 不再割裂。
- 有 guardrails 保证其不压过主行为链与事实约束。

---

## 本周最小执行包（推荐）

如果只做一轮最小推进，建议优先执行以下 6 项：

1. `N0-T01`：运行验收并输出 findings。
2. `N0-T02`：收束统一 runtime snapshot。
3. `N1-T01`：完成 6h / 24h / 72h / 7d 长间隔分层。
4. `N2-T01`：让记忆人格化字段进入主聊天上下文。
5. `N3-T01`：让 `sys_mode` 显式调制 `response_policy` / `motive_state`。
6. `N3-T03`：接入 screenshot scene-aware proactive 的最小安全链路。

这 6 项完成后，系统将从“局部行为像数字生命”进一步推进到：

> **长时间跨度下有连续性、不同模式下有差异、不同场景下有感知、且这些差异都可被观测与复测。**

---

## 暂不建议优先做的事

下一阶段不建议优先投入以下方向：

- 继续横向堆更多 persona module。
- 一开始就做复杂学习型动机系统。
- 把“意识证明”当工程验收标准。
- 在没有 findings 与统一观测面的情况下继续做高层抽象设计。

先把运行验收、长期连续性、记忆人格化、模式与场景联动做实，收益最高。
