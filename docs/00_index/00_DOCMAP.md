受众：MASTER
标题：文档地图（唯一入口）

版本：v3.6
日期：2026-04-22

本仓库所有"应该读什么、从哪里开工、哪些文档禁止当任务来源"，以本文件为准。

0）总览关系图（唯一口径）

白皮书（不可变法律）
→ PRD（FR-…：做什么+验收）
→ DevSpec（工程契约+T-…：怎么做+如何验证） + ModelPlane（模型平面规格）
→ DevTasks（TASK-…：最小交付单元，引用 FR/T/章节）
→ 测试与审计回放（证据）

使用规则：
- 实现层的任务来源只能是 DevTasks（以及 AGENTS 工作规范）；不得把白皮书/PRD/Theory/Trace 当任务来源。
- Project（文档代理）负责把讨论沉淀到 PRD/DevSpec/DevTasks，并维护 Trace 覆盖关系。

当前代码优先阅读顺序（2026-04-19 起）：
- `README.md`：仓库入口与运行面总览
- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`：当前代码事实总览
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`：主链核心模块与覆盖优先级
- `docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md`：前端主体化信息架构
- `docs/50_project/arch/dataflow_report.md`：请求流与回流补充
- `docs/60_trace/requirements_trace.md`：需求到设计/测试映射

---

1）白皮书（宪法 + 人格/风格设定）
- docs/10_whitepaper/whitepaper_constitution_persona.md
  用途：定义不可变边界、不变量、真相/风格层级、人格表达约束；作为"法律与人设"。含 sys_mode/Focus 子状态、Scheduler 宪法约束、NARRATIVE_ONLY 宪法定位、PERF/SCHED 系列错误码。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

2）需求文档（PRD）
- docs/20_prd/PRD.md
  用途：定义要做什么（范围、用户故事、验收、非功能指标），不描述实现步骤。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

3）开发文档（DevSpec + 任务 + 模型平面）
- docs/30_devspec/DevSpec.md
  用途：把 PRD 落到模块、接口、协议、Schema、日志、错误码、状态机；属于工程契约与蓝图层。若与当前代码事实文档冲突，以 `README.md`、`CURRENT_CODE_ARCHITECTURE.md` 和实际代码为准。
  读者：实现层（主要）、MASTER。

- docs/30_devspec/DevTasks.md
  用途：实现层可逐条执行的任务清单（每条都有输入/输出/验收）。
  读者：实现层（唯一任务入口）。

- docs/30_devspec/ModelPlane.md
  用途：模型平面规格（多模型节点分工、路由、降级、出网最小化、审计字段、回归口径）；用于"可回放、可追责"的模型配置治理。
  读者：MASTER、实现层（按契约实现与验证）。

4）代理行为规范（实现层）
- docs/40_agents/AGENTS.md
  用途：实现层的工作准则：任务来源门禁、交付包格式、不变量优先、测试编号（T-xx）口径。
  读者：实现层（主要）、MASTER。

- docs/40_agents/CURSOR.md
  用途：Cursor 实现层任务看板（TODO 列表、完成标准、进度追踪）；记录各 TODO 的背景、修复方案、改动文件与验证结果。
  读者：实现层（主要）、MASTER。

5）Project 指令（文档生产，不含代码）
- docs/50_project/PROJECT_BRIEF.md
- docs/50_project/PROJECT_TASKS.md
- docs/50_project/arch/ENV_EXTERNAL_SERVICES_CHECKLIST.md
- docs/50_project/arch/ExternalServices_Config.md
- docs/50_project/guides/vts_hotkey_config_guide.md
  用途：给 Project（文档代理）的产出约束、任务拆解、以及 Project×实现层×版本库 的协作工作表；配置指南与外部服务清单；不包含终端指令片段。
  读者：Project（主要）、MASTER。实现层不应把它当任务来源。

- docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md
  用途：双节点开发协作工作流规范（2026-03-26）。定义"开发节点 ⇄ 运行节点"闭环机制：开发实测报告→真机验证→运行反馈报告→修复闭环。含报告类型定义、命名规范、流程步骤、闭环判定条件。填补已删除的 WORKSHEET_Project_Codex_Git_Flow.md 空缺。
  读者：MASTER、实现层（查阅遵守）。

- docs/50_project/guides/WXAUTOX4_THREE_PHASE_COMPLETION_REPORT.md
  用途：wxautox4 三期更新完整总结报告（2026-03-27）。记录三期计划全部完成情况：ALLOWED_ACTIONS 从 6 扩展到 37，新增消息对象缓存、HTTP 查询端点、朋友圈/群管理/高级消息操作等全量功能。含架构示意图、环境变量清单、40条测试用例汇总和真机验证建议。
  读者：MASTER、运行节点（按指引验证）。

- docs/50_project/guides/CUSTOM_SKILL_INSTALL.md
  用途：Natsume 自定义技能安装指南（2026-03-26）。说明如何创建 custom_providers/ 下的自定义技能 Provider，含目录约定、模板文件、注册流程与验证方式。
  读者：CODEX、实现层。

- docs/50_project/arch/GATEWAY_ZH_CHAIN_NODE_ANALYSIS.md
  用途：Gateway 中文文字链路完整节点分析（当前代码实况）。覆盖 `server.py -> /v1/host/input -> cortex_orchestrator -> post_gateway_reply -> OutboundDispatcher` 的当前回流与回送链，并明确 `render_target_lang="zh"` 的注入点。
  读者：MASTER、架构参考。

- docs/50_project/guides/DEV_TEST_REPORT_TEMPLATE.md
  用途：开发实测报告模板（DEV→RUN）。开发节点完成功能后按此模板产出实测报告，列出本机已验证项与真机实测清单，供运行节点执行。
  读者：实现层（模板使用）。

- docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md
  用途：运行反馈报告模板（RUN→DEV）。运行节点完成真机测试后按此模板产出反馈报告，逐条记录测试结果与新发现问题，供开发节点修复闭环。
  读者：实现层（模板使用）。

- docs/50_project/index.md
  用途：Flow 资产追溯索引（按环境），记录 flows/active.pointer 指向的当前生效 Flow 导出物；支撑 T-Flow-02 回归口径。
  读者：MASTER、Project（文档代理）。

- docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md
  用途：当前代码事实总览。覆盖实际运行面（Host Main / Obs Backend / Edge RPC / Gateway / Monitor UI）、主入口、路由面、聊天主链、外部输入回流、Tribe family 现状。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md
  用途：核心模块定位文档。用于快速识别哪些模块真的处在主链上、模块之间如何衔接、当前测试覆盖更应该补在哪一层。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md
  用途：数字生命体前端主体化方案。对齐当前 Monitor UI 的页面分组、生命时间轴/病灶中心/数字生命体导航等信息架构变化。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/MODE_TRANSITION_SYSTEM.md
  用途：智能模式切换系统技术文档（2026-03-27）。定义 ModeTransitionService（手动+自动统一切换）、ActivityMonitor（Host 端前台进程检测/空闲检测/早间唤醒）、REST API（GET/POST /obs/sys-mode 等 5 个端点）、WebUI RuntimeMode 面板。含模式定义矩阵（6 种模式）、合法切换路径、5 条自动触发规则、30+ 游戏进程内置列表、审计追踪。修复 ALLOWED_SYS_MODES 缺少 sleep 的 Bug。
  读者：MASTER、实现层（查阅对齐）。

- docs/50_project/arch/AGENTIC_RAG_ARCHITECTURE.md
  用途：Agentic RAG 智能检索架构历史留痕。旧 `hippocampus_agent -> retrieval.orchestrator -> retrieval.pipeline` 主链已移除；当前表层主链只接收压缩后的 `ReplyControlPacket` 与 thin recall 输出，复杂 RAG/多智能体只能作为上游结构化事实来源重新接入。
  读者：MASTER、实现层（历史对照）。

- docs/50_project/arch/PHASE_X_INTELLIGENT_ORCHESTRATION.md
  用途：Phase X 智能编排层升级六大方案留痕（2026-03-29，2026-04-19 已加校准说明）。记录 `MemoryWorthEvaluator / AdaptivePromptBudget / SemanticDedup / BondAwareStyleEngine / ConsolidationTrigger / TtsContextRouter` 六项能力的原始设计意图；这些模块现已落库，但当前接线与接口应以现行契约文档和实际代码为准。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/PHASE_XI_AGENT_ARCHITECTURE_UPGRADE.md
  用途：Phase XI 智能体架构升级七大方案留痕（2026-03-29，2026-04-19 已加校准说明）。记录 `ReflectionChain / EpisodicMemoryGraph / PredictiveState / PlanningAgent / MetaLearningLoop / SkillComposer / MultimodalProcessor` 的原始设计意图；这些模块现已落库，当前多通过 `dependencies.py`、`intelligent_runtime.py`、`observability_persist.py` 等接线，但不应把本文当成当前主链默认行为说明。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/PHASE_XII_HOST_LOCAL_CONTROL.md
  用途：Phase XII Host 本地操控能力方案留痕（2026-03-29，2026-04-19 已加校准说明）。记录 `HostCommandExecutor / SecretVault / FileForensics / BrowserAutomation / FileOrganizer / SystemMonitor` 六类 Host 工具能力的原始设计意图；这些模块现已落库，但当前应通过 `ToolCatalog / ToolBroker / MCPAdapter / SkillRegistry / tool_routes` 的真实接线来理解其暴露与执行方式。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/DIGITAL_LIFE_MULTI_AGENT_BLUEPRINT_V1_20260408.md
  用途：数字生命多智能体族蓝图留痕（2026-04-08，2026-04-19 已加校准说明）。记录多智能体族、`ContextPack` / `StateArbiter`、主 DAG / 子图 DAG 与 P0-P3 迁移路线的阶段性构想；正文含 `top_agent.py` 等未落地入口，应按蓝图/历史参考理解，不作为当前运行时事实或实现层任务来源。
  读者：MASTER、Project（文档代理）。

- docs/50_project/arch/PARENT_CHILD_AGENT_PERSONA_RENDERING.md
  用途：Phase XIV 顶级 Agent / 父子级渲染历史方案（2026-03-31，2026-04-19 已加校准说明）。记录通过 `top_agent.py` 统一拟人化渲染的阶段方案；正文含 `SceneRouter`、`main_chain.py`、`chat_handler_complex.py` 等旧链路假设，应按历史方案理解，不作为当前运行时事实或实现层任务来源。
  读者：MASTER、Project（文档代理）。

- docs/50_project/arch/PHASE_XIII_STYLE_SYSTEM_OVERHAUL.md
  用途：Phase XIII 风格系统重构方案留痕（2026-03-30，2026-04-19 已加校准说明）。记录旧 `StyleDistiller / EmotionTaggedUtteranceIndex / persona_examples 独立注入 / style_canon 脱离 reranker` 的设计缘起；`StyleDistiller`、`EmotionTaggedUtteranceIndex` 和 `style_reference_bank` 已从当前最小主链清理，评分只作观察。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/DIGITAL_LIFE_FUSION_V2_20260408.md
  用途：数字生命架构融合方案 V2 留痕（2026-04-08，2026-04-19 已加校准说明）。记录当时“P0 地基收口 + TRIBE 渐进迁移 + Edge 躯干族”合并方案；正文含 `top_agent`、`dispatch_agent`、`Supervisor` 等阶段口径，应按历史融合方案理解，不作为当前主链事实或实现层任务来源。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/PHASE_XV_DISPATCH_CHAT_ARCHITECTURE.md
  用途：Phase XV Dispatch / Chat 分离历史方案与问题复盘（2026-04-01，2026-04-19 已加校准说明）。记录 TopAgent 分拆、上下文断裂、TTS / Gateway 双通道等阶段性问题分析；当前 `/v1/chat` 与 `/v1/host/input` 已改为 cortex / executive runtime 主链，应按历史对照理解。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md
  用途：TRIBE 多智能体族重构蓝图与实施记录留痕（2026-04-08，2026-04-19 已加校准说明）。混合记录目标态分族图与 T-xx 级落库记录；正文含 `top_agent` 等阶段入口，应按迁移轨迹/历史参考理解，不作为当前运行时事实或实现层任务来源。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/NATSUME_AI_FULL_LLM_CAPABILITY_RESTORATION_PLAN_20260422.md
  用途：Natsume_Ai 全认知能力 LLM 恢复蓝图与实施记录（2026-04-22，已按多批次落地）。当前应结合文首“代码校准说明”阅读：共享池、MiniMax、`llm_pool_call`、内心世界双 tick、记忆蒸馏与后台 LLM 调度门控均已落库；正文后半保留原始计划项与运行节点验收清单。
  读者：MASTER、实现层（查阅现状与后续验收）、Project（文档代理）。

- docs/50_project/arch/dataflow_report.md
  用途：系统数据流全景报告（请求流、回流、模块间数据交换）；与架构文档互补，负责“流向”视角。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/arch/UNIFIED_MESSAGE_GATEWAY_DESIGN.md
  用途：外部消息统一网关设计与现状混合文档（2026-03-24，2026-04-19 已加校准说明）。文首给出当前 Gateway 真实主链：`server.py -> GatewayRouter -> /v1/host/input -> Host 主链 -> post_gateway_reply -> /v1/gateway/reply -> OutboundDispatcher`；正文保留 `SCENE_GATEWAY / SceneRouter / MainChain` 等历史方案留痕，阅读时应区分现行入口与设计背景。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/phases/MULTI_AGENT_UPGRADE_PLAN.md
  用途：多 Agent 编排升级历史阶段方案（2026-03-16 起持续追加，2026-04-19 已加校准说明）。记录 `Supervisor / Judge / active_agent_id / graph_executor` 动态分发等 graph-runtime 方案演化；仓库仍保留部分 handler 与 contract，但现行 `/v1/chat` 主链不以本文的 `scene_router / main_chain / Supervisor-Judge` 口径为准。
  读者：MASTER、Project（文档代理）、实现层（历史对照）。

- docs/50_project/arch/AGENT_ORCHESTRATION_DETAIL.md
  用途：Agent 编排详情（当前运行时实况）。覆盖 `/v1/chat`、`/v1/host/input`、`/v1/agent/run` 与 Executive route 类型，说明当前 skill-first runtime 口径。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/50_project/phases/MULTI_AGENT_P4_SCOPE.md
  用途：P4 范围与验收标准（意图子图透明封装）。定义 P4 阶段的交付范围、验收条件与完成标准。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P5_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P5_SEAL_RESULT.md
  用途：P5 范围与验收标准（Supervisor 决策升级）+ P5 验收结论。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P6_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P6_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P6_1_LOCAL_LOOP_CONTRACT.md
  docs/50_project/phases/MULTI_AGENT_P6_2_IMPLEMENTATION.md
  docs/50_project/phases/MULTI_AGENT_P6_3_INTEGRATION_ACCEPTANCE.md
  用途：P6 范围与验收标准（Intent 子图局部循环）+ P6 验收结论 + P6 子文档（局部循环契约、最小闭环实现、集成验收补齐）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P7_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P7_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P7_2_FAILURE_MATRIX.md
  docs/50_project/phases/MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P7_4_FAULT_INJECTION_BASELINE.md
  docs/50_project/phases/MULTI_AGENT_P7_ACCEPTANCE_PREVIEW.md
  用途：P7 范围与验收标准（治理与稳定性基线）+ P7 验收结论 + P7 子文档（三图失败收口矩阵、统一观测面范围、压测与故障注入基线、验收汇总预审）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P8_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P8_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P9_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P9_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P10_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P10_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P11_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P11_SEAL_RESULT.md
  docs/50_project/phases/MULTI_AGENT_P12_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P12_SEAL_RESULT.md
  用途：P8–P12 各阶段范围与验收标准 + 对应封板结论（P8–P12 为候选基线迭代阶段）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P13_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P13_SEAL_RESULT.md
  用途：P13 范围与封板结论（tool_registry 工具实现升级，search_knowledge 真实接入）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P14_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P14_SEAL_RESULT.md
  用途：P14 范围与封板结论（judge_agent ReAct 化）。
  读者：Project（文档代理）。

- docs/50_project/phases/MULTI_AGENT_P15_SCOPE.md
  docs/50_project/phases/MULTI_AGENT_P15_SEAL_RESULT.md
  用途：P15 范围与封板结论（终点封板：Supervisor LLM 提案 + graph_executor 动态路由）。
  读者：Project（文档代理）。

6）可追溯性（Trace）
- docs/60_trace/requirements_trace.md
  用途：审阅"需求→设计→任务→测试"覆盖情况。
  口径：若 csv 未维护或滞后，以 md 为准。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

- docs/60_trace/flows/index.md
  用途：Flow 指针与索引的一致性。说明 `flows/active.pointer` 当前仍是占位型环境指针，现行 `/v1/chat` 主链不直接由导出 Flow 驱动。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/risk_registry.md
  用途：风险登记册，记录已识别风险、缓解措施与状态。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/test_report_2026-03-05.md
  用途：全量测试报告（2026-03-05）。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/EDGE_OPTIMIZATION_AND_TEST_REPORT_2026-03-04.md
  用途：Edge 优化与测试报告（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/EMOTION_CLOSED_LOOP_REPORT_20260309_235917.md
  用途：情感闭环测试报告（2026-03-09）。验证情感状态写入、读取与回流的完整闭环链路。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_telegram_adapter_20260326.md
  用途：Telegram Userbot 适配器（Phase 6）开发实测报告（2026-03-26）。Telethon MTProto 协议实现"人感"消息收发，含 GatewayConfig 集成、EdgeConfig 环境变量、bootstrap 注册、架构文档更新。10 项真机测试（P0×5 / P1×3 / P2×2）。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_qq_adapter_enhancement_20260326.md
  用途：QQ 适配器增强版开发实测报告（2026-03-26）。含默认平台配置调整、QQ 适配器 6 项功能增强、新建单元测试 test_qq_adapter.py（27 个测试用例）。9 项真机测试。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_voice_send_20260326.md
  用途：微信语音消息发送功能实测报告（2026-03-26）。实现 PCM→silk 编码→bridge 队列发送链路，含 pysilk-mod 依赖、VoiceHandler 模块、WeChatAdapter 语音分支。
  读者：MASTER、实现层。

- docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_qq_adapter_enhancement_20260326.md
  用途：QQ 适配器增强版运行反馈报告（2026-03-26）。真机验证 QQ 适配器增强功能的实际执行结果。
  读者：MASTER、实现层。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_line_adapter_20260326.md
  用途：LINE Messaging API 适配器开发实测报告（2026-03-26）。基于飞书 Webhook+REST 架构模式实现 LINE 官方 Messaging API 接入，含 HMAC-SHA256 签名验证、7 种消息类型解析、Reply/Push 双通道发送、EdgeConfig/GatewayConfig/bootstrap 全链路集成。11 项真机测试清单（Webhook 接收、消息解析、回复发送、签名验证等）。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_emotion_system_fixes_2026-03-26.md
  用途：情感系统 6 项 Bug 修复开发实测报告（2026-03-26）。覆盖线程安全（threading.Lock）、JSON 文件持久化、标签集统一（16 种）、VAD 映射唯一权威来源（emotion_constants.py）、EmotionTimeline/compute_emotion_echo 代码去重（~250 行）、衰减参数环境变量可配置化（9 个环境变量）。7 个文件修改，含 6 项待 RUN 节点验证测试。
  读者：MASTER、实现层、Project（文档代理）。

- docs/35_quality/audits/SOUL_SYSTEM_COMPLETENESS_AUDIT_20260326.md
  用途：灵魂系统完整性审计报告（2026-03-26）。对 Phase I-VIII 全部 8 阶段的代码实体、测试覆盖、设计文档对齐、里程碑追溯、模块导出 6 个维度进行交叉验证。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_bridge_20260327.md
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase1_20260327.md
  用途：wxautox4 Bridge Phase 1 开发实测报告（2026-03-27）。记录 Phase 1 新增 9 个 actions（IsOnline/GetMyInfo/好友群列表/历史消息/媒体下载/监听管理/ChatWith）的实现，含 10 条待真机验证测试用例（T-P1-01~T-P1-10）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase2_20260327.md
  用途：wxautox4 Bridge Phase 2 开发实测报告（2026-03-27）。记录 Phase 2 新增 8 个 actions（GetSubWindow/ChatInfo/GetDialog/SendUrlCard/GetAllMessage/GetNewFriends/GetFriendDetails）+ LinkMessage.get_url() 解析 + Gateway HTTP 查询端点（POST /v1/gateway/wechat/query），含 11 条待真机验证测试用例（T-P2-01~T-P2-11）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase3_20260327.md
  用途：wxautox4 Bridge Phase 3 开发实测报告（2026-03-27）。记录 Phase 3 新增 7 个 actions（朋友圈4个/群管理1个/高级消息操作3个）+ 消息对象缓存机制（TTL 5min），含 11 条待真机验证测试用例（T-P3-01~T-P3-11）。至此三期计划全部完成，ALLOWED_ACTIONS 从 6 扩展到 37。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_mode_transition_20260327.md
  用途：智能模式切换系统开发实测报告（2026-03-27）。ModeTransitionService（手动+自动统一切换）、ActivityMonitor（Host 端游戏进程检测/空闲检测）、REST API 5 个端点、WebUI RuntimeMode 面板。修复 ALLOWED_SYS_MODES 缺少 sleep Bug。4 个新增文件 + 6 个修改文件，含 13 条真机测试用例（P0×5 / P1×5 / P2×3）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p0_p1_fixes_20260327.md
  用途：P0/P1 修复实测报告（2026-03-27）。P0-1: dependencies.py 新增 get_hard_state()/set_sys_mode()（ModeTransitionService 不再降级）；P0-2: ActivityMonitor 集成到 http_hooks.py 启动/关闭流程；P1-4: .env.example 补齐 90+ 缺失环境变量（LLM/Edge RPC/情绪系统/Bond/语义召回等）。7 条真机验证用例。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_recall_fusion_bugfix_20260327.md
  用途：灵魂层召回逻辑 4 项 Bug 修复报告（2026-03-27）。修复：①_render_target_lang_selector_match 空值返回 False 导致多语言路由失效（高优）；②fusion key 跨路由碰撞；③emotion_filter 分组内排序丢失；④memory_ops_log 搜索文本质量差。含 5 条待真机验证测试用例（T-RF-FIX-01~05）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_emotion_pipeline_fix_20260327.md
  用途：情感层管线修复报告（2026-03-27）。修复 8 个情感子系统的管线断裂/未接入问题：P0（emotion_context 传递不完整 + runtime_payload 顶层字段缺失）、P1（PersonaExpression/SelfReflection/BondNarrative 三个子系统首次接入）、P2（UserEmotionProfile 显式传递）。含 11 条待真机验证测试用例（T-EMO-01~11）。
  读者：MASTER、运行节点（按用例验证）。
- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_asr_complex_tts_fix_20260327.md
  用途：ASR 复杂场景 TTS 语音播报缺失修复报告（2026-03-27）。修复 ASR 语音输入被分类为非 CHAT 场景时 TTS 完全缺失的 Bug。方案A：_complex_scene_sse_generator 补 TTS；方案B：AsrInputSourceStrategy.post_chain 兜底 TTS（双保险）。含 7 条真机测试用例（T-ASR-TTS-01~07）。
  读者：MASTER、运行节点（按用例验证）。
  用途：wxautox4 Bridge 集成开发实测报告（2026-03-27）。新增 wxautox4 作为微信自动化 bridge 后端（替代自研 UIA Bridge），外部协议完全兼容（JSONL 命令队列 + webhook POST）。改动 5 个文件，含 8 项真机测试清单（P0×4 / P1×2 / P2×2），涵盖环境安装、独立启动、inbound 消息转发、outbound 命令执行、热切换回退、图片发送、消息去重、群聊识别。
  读者：MASTER、实现层、Project（文档代理）。

- docs/45_testing/dev_test_reports/DEV_TEST_REPORT_continuity_memory_sync_write_20260401.md
  用途：连续性记忆同步写入修复报告（2026-04-01）。将 INPUT_COMPLETED 事件写入从异步后处理（_async_post_process）提前到 COMMIT SSE 发送之前同步执行，修复"每次问夏目蓝刚才说了什么，她回复不知道"的记忆断层问题。覆盖快速路径（recent_turn_direct）和正常 LLM 路径两个出口。14 项单元测试全部通过。
  读者：MASTER、运行节点（按用例验证）。

- docs/50_release/changelog/UI_UPGRADE_CHANGELOG.md
  用途：UI 升级变更日志。
  读者：MASTER、Project（文档代理）。

- docs/50_release/changelog/MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md
  用途：模型本地/云端切换变更日志（2026-03-05）。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_test_plan_20260313.md
  用途：封板测试计划（2026-03-13）。定义封板前必须通过的测试项目、验收标准与执行顺序。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_test_plan_supplement_20260315.md
  用途：封板测试计划补充（2026-03-15）。对 20260313 测试计划的补充与修订。
  读者：MASTER、Project（文档代理）。

- docs/50_release/release_seal_execution_report_20260316.md
  用途：封板执行报告（2026-03-16）。记录封板测试的实际执行结果、通过/失败项与最终封板决策。
  读者：MASTER、Project（文档代理）。

- docs/50_release/reports/release_seal_20260316/00_index/BATCH1_20260316_p0_entry.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH2_20260316_p1_p2_formal.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH3_20260316_automated.md
  docs/50_release/reports/release_seal_20260316/00_index/BATCH4_20260316_full_auto.md
  docs/50_release/reports/release_seal_20260316/00_index/MULTI_AGENT_SEAL_RESULT.md
  docs/50_release/reports/release_seal_20260316/00_index/README.md
  用途：2026-03-16 封板批次报告（P0 入口、P1/P2 正式、自动化、全自动、多 Agent 封板结果与索引）。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/ROLLBACK_RUNBOOK_2026-03-10.md
  用途：回滚操作手册（2026-03-10）。定义各里程碑的回滚步骤、前置条件与验证口径。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/RELEASE_ROLLBACK_GATE_20260310_010648.md
  用途：发布回滚门控记录（2026-03-10）。记录回滚决策触发条件与实际执行情况。
  读者：MASTER、Project（文档代理）。

- docs/50_release/rollback/POST_RELEASE_REVIEW_20260310_010918.md
  用途：发布后复盘报告（2026-03-10）。记录发布后问题、根因分析与改进措施。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/ENV_SINGLE_SOURCE_MIGRATION_2026-03-04.md
  用途：环境变量单一来源迁移记录（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/CANON_VECTOR_PROJECTION_2026-03-04.md
  用途：Canon 向量投影分析（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/ARCH_REVIEW_AND_ROADMAP.md
  用途：架构评审与路线图（2026-03-09）。对当前架构的评审结论与后续演进路线规划。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/RETRIEVAL_ORCHESTRATOR_DESIGN.md
  用途：检索编排器设计文档（2026-03-09）。记录检索层编排逻辑、优先级策略与接口设计。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/README_trial_run_reports.md
  用途：试运行报告目录说明（README）。解释 artifacts/trial_run_reports/ 下各报告的命名规范与阅读方式。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/VTS_VOICE_TROUBLESHOOTING.md
  用途：VTS 语音故障排查手册。记录语音链路常见问题、排查步骤与已知解决方案。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/BUG_FIX_LOG.md
  用途：Bug 修复日志。记录项目代码全量排查过程中发现并修复的所有 Bug，含修复日期、影响范围与修改文件。
  读者：MASTER、实现层。


- artifacts/trace/HOST_CHAT_*_req_*.json
  artifacts/trace/CANON_STYLE_SYNC_*.json
  artifacts/trace/canon_sync_summary_*.json
  用途：运行时原始 JSON 追踪数据（模型控制、检索事件、Canon 同步等）。
  读者：MASTER（调试/回放用）。

- docs/35_quality/audits/audit_report_20260311.md
  用途：Natsume AI 深度审计报告（2026-03-11）。覆盖意图识别、模型编排控制、Agent 路由、SSE 双端对齐、情感层接入、Bond 优化六大维度。
  读者：MASTER、Project（文档代理）。

- docs/35_quality/GATEWAY_ARCH_REDESIGN.md
  用途：Gateway 架构债与整改方案盘点（2026-04-15，2026-04-19 已加校准说明）。从质量视角记录 Gateway / ASR 双路径、意图识别割裂、TTS / VTS 抑制等问题背景；当前真实 Gateway 主链应以 `UNIFIED_MESSAGE_GATEWAY_DESIGN.md`、`GATEWAY_ZH_CHAIN_NODE_ANALYSIS.md` 与实际代码为准。
  读者：MASTER、Project（文档代理）、实现层（质量对照）。

- docs/35_quality/ORGAN_TRIBE_UPGRADE_PLAN.md
  用途：器官族能力分级升级盘点（2026-04-16，2026-04-19 已加校准说明）。记录按 A/B/C 评级推进器官族能力增强的阶段性计划；该评级不是当前 tribe family 的权威运行时总表，应按质量盘点/升级待办理解。
  读者：MASTER、Project（文档代理）、实现层（质量对照）。

- artifacts/trial_run_reports/
  用途：自动化试运行报告（T1-x 系列，按时间戳归档）。
  读者：MASTER、Project（文档代理）。

- docs/35_quality/code_reviews/
  用途：代码审查报告归档目录（含全量审查报告与各里程碑专项审查报告）。
  命名规范：
    - 全量审查：full_code_review_report_YYYY-MM-DD[-vN|-mXX].md
    - 里程碑专项：code_review_mXX_YYYY-MM-DD.md
    - 仓库分析：repo_code_analysis_report_YYYY-MM-DD.md
  读者：MASTER、Project（文档代理）。

7）提示词入口
- prompts/CODEX_PROMPT.md
- prompts/PROJECT_PROMPT.md
- prompts/llm_contract/*
  用途：分别作为实现层与 Project 的入口提示词；以及 LLM 输出契约/拒绝范式等（作为契约资产，不作为需求来源）。
  读者：按文件受众标注执行。

8）理论文档（Theory：论证/原则推导/边界解释）
- docs/15_theory/Theory.md
  用途：解释"为什么这样设计"、关键机制的理论依据与判定口径；2026-04-19 已补当前代码校准说明，明确其属于理论层，不作为实现任务清单。若与当前代码事实冲突，以 `README.md`、`CURRENT_CODE_ARCHITECTURE.md`、现行契约文档和实际代码为准。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

- docs/15_theory/soul_emotion_optimization.md
  用途：灵魂情感架构优化设计留痕文档（2026-03-05）。记录五大优化方向：①情绪连续体 EmotionVector、②Bond 情感质感层、③情感记忆标注、④emotion_pulse 动态槽位、⑤主动情感触发器。含完整伪代码设计方案、优先级排序与安全约束说明。待实施，不作为当前任务来源。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md
  用途：LangGraph 高级能力与 Host 治理约束冲突分析（2026-03-06）。分析五项冲突点（条件边、并行分支、子图、断点续跑、流式输出）与当前系统约束的关系。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LangGraph_Governed_Runtime_Solution.md
  用途：LangGraph Governed Runtime 理论方案留痕（2026-03-06，2026-04-19 已加校准说明）。提出三层架构（Policy Plane / Execution Plane / Evidence Plane）、Host Governance Overlay 方案、五阶段能力恢复路径与三句话架构结论；用于解释治理化设计思路，不作为当前 `/v1/chat` / `/v1/host/input` 主链事实说明。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/architecture_overview.md
  用途：Natsume AI 整体架构全景文档（2026-03-10）。含双端总体架构、Host Brain 各模块、Edge 各模块、数据流、记忆层、治理层的完整 Mermaid 图与说明。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/CORE_LAYER_MODULE_REFERENCE.md
  用途：host_brain/core/ 层模块参考（当前代码实况）。覆盖 `prompt_context`、`metabolism`、`endocrine`、`services`、`mode_transition`、`activity_monitor`、`predictive_state`、`meta_learning_loop` 等当前基础状态与运行时辅助模块，并明确 core 现状已不是“零上游依赖纯数据层”。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/INTEGRATIONS_LAYER_CONTRACT.md
  用途：host_brain/integrations/ 层接口契约（当前代码实况）。覆盖 `GraphRuntimeContract`、`GraphExecutor`、LangGraph checkpoint/subgraph 适配、`GovernedToolNode`、Evidence Plane、LLM Gateway，以及当前依赖边界与恢复/降级口径。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/RETRIEVAL_LAYER_CONTRACT.md
  用途：host_brain/retrieval/ 层接口契约历史留痕与 thin recall 迁移参考。旧 Agentic RAG pipeline/orchestrator/query planner/evidence evaluator 已从当前主链移除；现行入口以 RecallFusionServiceContract 和表层 memory recall adapter 为准。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/GOVERNOR_SCHEDULER_CONTRACT.md
  用途：host_brain/governor/ + scheduler/ 层接口契约（当前代码实况）。覆盖 R1–R8 门控、TokenService 当前校验/消费顺序，以及两套调度面：`/scheduler/*` 的声明式 Intent 调度器与 `/host-scheduler/*` 的 Host 常驻任务调度器。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md
  用途：host_brain/tools/ + memory/ 层接口契约（当前代码实况）。覆盖 ToolCatalog/ToolBroker/Selector/MCPAdapter、具体 Host 工具模块，以及 Canon SSOT、EpisodicStore、ContextCompressor、Distillation、MemoryWorthEvaluator、ConsolidationTrigger、EpisodicMemoryGraph 等现行记忆能力。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/PERIPHERAL_LAYERS_CONTRACT.md
  用途：host_brain 外围层接口契约。覆盖 airlock、bond、channels、vision、lsp、mcp；其中 channels 口径已补到当前的 VTS / Edge RPC / Gateway 出站关系。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md
  用途：host_brain 核心应用层契约（当前代码实况）。覆盖当前 app 运行面、`/v1/chat` 主链、`SQLiteLedger` 写接口分组、动态 SkillRegistry/SkillLoader 执行路径，以及独立 Obs Backend + Monitor UI 的观测结构。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

- docs/15_theory/SOUL_SYSTEM_FULL_UPGRADE_PATH.md
  用途：灵魂系统升级蓝图与历史留痕（2026-03-23，2026-04-19 已加校准说明）。覆盖 8 大升级阶段与能力拆解，但正文仍含 `main_chain.py` 等旧入口口径，应按蓝图/历史参考理解，不作为当前运行时事实或实现层任务来源。
  读者：MASTER、Project（文档代理）。

9）性能与稳定性监控（Metrics & Stability）
- docs/55_testing/metrics/baseline_stream_metrics_2026-03-09.md
  用途：流式响应基线性能指标（2026-03-09）。记录 SSE 流式输出的延迟、吞吐量与稳定性基线数据。
  读者：MASTER、Project（文档代理）。

- docs/55_testing/metrics/LONGTERM_METRIC_DASHBOARD_20260310_014526.md
  用途：长期指标看板（2026-03-10）。汇总多维度性能指标趋势，用于长期健康度追踪。
  读者：MASTER、Project（文档代理）。

- docs/55_testing/stability_weekly/WEEKLY_STABILITY_REPORT_20260310_005912.md
  用途：周度稳定性报告（2026-03-10）。记录一周内系统稳定性数据、异常事件与改进建议。
  读者：MASTER、Project（文档代理）。

10）协作与运行手册（Worksheet：分工/流程/落地节奏）

（注：协作工作表 WORKSHEET_Project_Codex_Git_Flow.md 已删除，由 docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md（双节点开发协作工作流）替代，已在第 5 节登记。docs/50_project/ 下的 PROJECT_BRIEF.md、PROJECT_TASKS.md、arch/ENV_EXTERNAL_SERVICES_CHECKLIST.md、arch/ExternalServices_Config.md、guides/vts_hotkey_config_guide.md、index.md、arch/CURRENT_CODE_ARCHITECTURE.md、arch/dataflow_report.md、phases/MULTI_AGENT_UPGRADE_PLAN.md 已在第 5 节登记。）

11）配置资产入口（非文档，但属于“可部署/可回放”真相源的一部分）
- flows/
  用途：Flow 导出物与环境指针（exports/ + active.pointer）；由 Trace（docs/60_trace/flows/index.md）指向当前生效版本。
  读者：MASTER、实现层（按 DevSpec/DevTasks 约束维护与回归）。

- 根目录 .env / .env.example
  用途：运行时单一配置源（`.env`）与示例/降级模板（`.env.example`）；用于 Host/Edge/Neo4j/Chroma/Retrieval 统一配置。
  读者：MASTER、实现层（按 DevSpec 与运行脚本约束实现与回归）。

12）发布目录（Release）
- configs/release/mXX_release.json
  用途：各里程碑发布清单（JSON 格式），记录发布版本、变更摘要、测试通过情况。
  读者：MASTER、Project（文档代理）。

- configs/release/release_catalog.json
  用途：发布资产目录（M0~M3 里程碑发布清单，含资产路径、依赖服务、配置快照引用）。
  读者：MASTER、Project（文档代理）。

- configs/release/release_rollback_map.json
  用途：发布回滚映射表，记录各里程碑的回滚目标版本与回滚步骤引用。
  读者：MASTER、Project（文档代理）。

- artifacts/release/reports/
  用途：发布报告（T1-x 系列，按时间戳归档）。
  读者：MASTER、Project（文档代理）。

---

- docs/35_quality/audits/DIGITAL_LIFE_GAP_CHECKLIST_V2_20260403.md
  用途：数字生命差距清单 V2（2026-04-03）。基于 DIGITAL_LIFE_RUNTIME_AUDIT / ROADMAP / VERIFICATION_PLAN 与当前代码状态，重排 P0/P1/P2/P3 的可执行任务、交付物与验收标准。
  读者：MASTER、Project（文档代理）、实现层（查阅对齐）。

附：实现层任务来源白名单（强约束）
- 仅允许将以下目录作为"任务来源"读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中明确点名路径与引用范围。

附：受众门禁（文档头部强制）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`

附：文档归类规则（v1.2 新增，v1.5 补充）
- 代码审查报告（code_review_*.md、full_code_review_report_*.md）→ docs/35_quality/code_reviews/
- 测试报告、运行追踪报告、变更日志、审计报告、数据流报告 → docs/60_trace/
- 架构全景、架构分析、理论推导、设计方案 → docs/15_theory/
- 配置指南 → docs/50_project/guides/
- 运行手册、项目协作文档、Flow 资产索引 → docs/50_project/
- 架构文档（代码架构、数据流、Agent 编排、外部服务配置）→ docs/50_project/arch/
- Multi-Agent 各阶段范围文档（MULTI_AGENT_Pxx_SCOPE.md）、封板结论（MULTI_AGENT_Pxx_SEAL_RESULT.md）、子阶段文档（MULTI_AGENT_Pxx_y_*.md）→ docs/50_project/phases/
- 发布清单（JSON）、发布目录、回滚映射 → configs/release/
- 封板测试计划、封板执行报告、回滚手册、发布后复盘 → docs/50_release/
- 性能基线指标、长期指标看板、周度稳定性报告 → docs/55_testing/
- 开发规范、任务清单、模型平面 → docs/30_devspec/
- 任务看板、TODO 追踪 → docs/40_agents/
