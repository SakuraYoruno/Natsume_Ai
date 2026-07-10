受众：MASTER
标题：文档地图（唯一入口）

版本：v5.5
日期：2026-07-10
维护：2026-07-10（登记完整架构目标、M48～M57 正式 CODEX 执行入口，以及 FR-ARCH/TASK/T-ARCH/Trace 闭环）

本仓库所有"应该读什么、从哪里开工、哪些文档禁止当任务来源"，以本文件为准。

## 标记体系

| 标记 | 含义 | 读写策略 |
|------|------|---------|
| `[LIVING]` | 实时文档，跟随代码变更 | 代码变更时必须同步更新；以代码为真源 |
| `[ARCHIVE]` | 历史留痕，只读不更新 | 冻结记录，不随代码变化；仅用于追溯历史上下文 |

---

## 目录级入口（优先顺序）
- `[LIVING]` `docs/00_index/index.md`：文档总入口
- `[LIVING]` `docs/10_whitepaper/index.md`：白皮书目录入口
- `[LIVING]` `docs/15_theory/index.md`：理论与原理目录入口
- `[LIVING]` `docs/20_prd/index.md`：PRD 需求入口
- `[LIVING]` `docs/30_devspec/index.md`：DevSpec 目录入口
- `[ARCHIVE]` `docs/35_quality/index.md`：质量与修订入口
- `[LIVING]` `docs/40_agents/index.md`：代理与协作入口
- `[ARCHIVE]` `docs/45_testing/index.md`：测试与反馈入口（历史报告归档）
- `[LIVING]` `docs/50_project/index.md`：项目资产追溯入口（含当前事实/历史归档）
- `[ARCHIVE]` `docs/50_release/index.md`：发布与回滚入口
- `[ARCHIVE]` `docs/55_testing/index.md`：长期指标入口
- `[LIVING]` `docs/60_trace/index.md`：Trace 与追溯入口
- `[ARCHIVE]` `docs/test_reports/index.md`：历史测试报告入口
- `[ARCHIVE]` `docs/35_quality/audits/index.md`：质量审计入口
- `[ARCHIVE]` `docs/35_quality/code_reviews/index.md`：评审日志入口
- `[ARCHIVE]` `docs/45_testing/dev_test_reports/index.md`：开发测试报告入口
- `[ARCHIVE]` `docs/45_testing/runtime_feedback_reports/index.md`：运行反馈入口
- `[ARCHIVE]` `docs/45_testing/test_reports/index.md`：专项测试报告入口
- `[LIVING]` `docs/50_project/arch/index.md`：架构档案入口
- `[LIVING]` `docs/50_project/guides/index.md`：项目指南入口
- `[ARCHIVE]` `docs/50_project/phases/index.md`：阶段任务入口
- `[ARCHIVE]` `docs/50_release/changelog/index.md`：发布变更日志入口
- `[ARCHIVE]` `docs/50_release/reports/index.md`：发布批次报告入口
- `[LIVING]` `docs/50_release/rollback/index.md`：回滚与降级入口
- `[ARCHIVE]` `docs/55_testing/metrics/index.md`：指标快照入口
- `[ARCHIVE]` `docs/_history/`：历史架构快照归档区
- `[ARCHIVE]` `docs/55_testing/stability_weekly/index.md`：稳定性周报入口

---

0）总览关系图（唯一口径）

白皮书（不可变法律）
→ PRD（FR-…：做什么+验收）
→ DevSpec（工程契约+T-…：怎么做+如何验证） + ModelPlane（模型平面规格）
→ DevTasks（TASK-…：最小交付单元，引用 FR/T/章节）
→ 测试与审计回放（证据）

使用规则：
- 实现层的任务来源只能是 DevTasks（以及 AGENTS 工作规范）；不得把白皮书/PRD/Theory/Trace 当任务来源。
- Project（文档代理）负责把讨论沉淀到 PRD/DevSpec/DevTasks，并维护 Trace 覆盖关系。

当前代码优先阅读顺序（2026-06-30）：
- `[LIVING]` `README.md`：仓库入口与运行面总览
- `[LIVING]` `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` (v4.2)：当前代码事实总览
- `[LIVING]` `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` (v4.2)：主链核心模块
- `[LIVING]` `docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md` (v1.1)：Pentest 专项模块架构
- `[LIVING]` `host_brain/pentest/CAPABILITY.md` (v2.3)：Pentest 能力边界与安全约束
- `[LIVING]` `docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md`：前端主体化信息架构
- `[LIVING]` `docs/50_project/arch/dataflow_report.md` (v4.0)：请求流与回流补充（含 Pentest/Adversarial/Swarm/Online/Proactive 数据流）
- `[LIVING]` `docs/50_project/arch/AGENTIC_RAG_ARCHITECTURE.md` (v3.0)：当前召回与记忆数据流（旧文件名兼容保留）
- `[LIVING]` `docs/50_project/arch/diagrams/00_INDEX.md` (v1.0)：架构图册（7 张 Mermaid 图）
- `[LIVING]` `docs/30_devspec/desktop_pet_runtime.md`：Desktop Pet 运行面契约
- `[LIVING]` `docs/30_devspec/mateengine_bridge.md`：MateEngine Bridge 契约
- `[LIVING]` `docs/15_theory/RETRIEVAL_LAYER_CONTRACT.md` (v3.1)：RecallFusionServiceContract
- `[LIVING]` `docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md` (v3.3)：LifeMemoryStore SSOT + tools
- `[LIVING]` `docs/60_trace/requirements_trace.md`：需求到设计/测试映射

---

## 1）白皮书（宪法 + 人格/风格设定）

- `[LIVING]` docs/10_whitepaper/whitepaper_constitution_persona.md
  用途：定义不可变边界、不变量、真相/风格层级、人格表达约束。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

## 2）需求文档（PRD）

- `[LIVING]` docs/20_prd/PRD.md (v2.5)
  用途：基于当前代码事实的系统能力描述。覆盖现有运行面、FR-NLE-001～005 的 M48-A 安全地基，以及 FR-ARCH-001～010 的完整架构目标需求。
  读者：MASTER、Project（文档代理）。

## 3）开发文档（DevSpec + 任务 + 模型平面）

- `[LIVING]` docs/30_devspec/DevSpec.md (v2.5)
  用途：当前代码的工程契约参考手册。含既有运行合同、M48-A 安全收敛契约，以及 §16 的 M48～M57 完整目标架构合同。
  读者：实现层（主要）、MASTER。

- `[LIVING]` docs/30_devspec/NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md
  用途：M48-A 安全地基详细执行入口；定义 TASK-0461～0486、依赖、切流、停止、回滚与 T-NLE-01～24 验收。
  读者：实现层（主要）、MASTER。

- `[LIVING]` docs/30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md
  用途：完整架构重构正式 CODEX 执行入口；统一编排 M48-A/M48-B、M49～M57、TASK-0461～0622、FR-ARCH-001～010 与 T-ARCH-01～30，并定义依赖 Gate、切流、停止、回滚和封板证据。
  读者：实现层（唯一完整程序入口）、MASTER、Project。

- `[LIVING]` docs/30_devspec/DevTasks.md
  用途：实现层任务总入口；当前指向 M48～M57 Master 程序和 M48-A 详细执行文档，TASK-0000～0460 保留为历史追踪。
  读者：实现层。

- `[LIVING]` docs/30_devspec/ModelPlane.md (v1.7)
  用途：模型平面规格（模型节点分工、路由、降级、出网最小化、审计字段）。
  读者：MASTER、实现层。

- `[LIVING]` docs/30_devspec/desktop_pet_runtime.md
  用途：Desktop Pet 本地运行面契约（Electron + Live2D、127.0.0.1:18788、Host/Edge 路径）。
  读者：MASTER、实现层。

- `[LIVING]` docs/30_devspec/mateengine_bridge.md
  用途：MateEngine Bridge 契约（UDP signal、HTTP bridge、生命周期、动作编排）。
  读者：MASTER、实现层。

## 4）代理行为规范（实现层）

- `[LIVING]` docs/40_agents/AGENTS.md
  用途：实现层工作准则——任务来源门禁、交付包格式、不变量优先、T-xx 口径。
  读者：实现层（主要）、MASTER。

- `[ARCHIVE]` docs/40_agents/CURSOR.md
  用途：Cursor 实现层任务看板（历史 TODO 追踪）。
  读者：实现层（历史参考）、MASTER。

## 5）Project 指令与架构文档

### 5.1 项目元文档

- `[LIVING]` docs/50_project/PROJECT_BRIEF.md
  用途：文档代理的产出约束与门禁。
  读者：Project（主要）、MASTER。

- `[ARCHIVE]` docs/50_project/PROJECT_TASKS.md
  用途：文档代理历史任务拆解（v2.1, 2026-03-27）。
  读者：Project（历史参考）。

- `[LIVING]` docs/50_project/arch/ENV_EXTERNAL_SERVICES_CHECKLIST.md
- `[LIVING]` docs/50_project/arch/ExternalServices_Config.md
  用途：外部服务清单与配置。
  读者：MASTER、实现层。

### 5.2 项目指南

- `[LIVING]` docs/50_project/guides/vts_hotkey_config_guide.md
  用途：VTS 热键配置指南。
  读者：MASTER、实现层。

- `[ARCHIVE]` docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md
  用途：双节点开发协作工作流规范（2026-03-26）。团队流程文档，不由代码驱动。
  读者：MASTER、实现层（查阅遵守）。

- `[ARCHIVE]` docs/50_project/guides/WXAUTOX4_THREE_PHASE_COMPLETION_REPORT.md
  用途：wxautox4 三期更新总结报告（2026-03-27）。
  读者：MASTER（历史参考）。

- `[LIVING]` docs/50_project/guides/CUSTOM_SKILL_INSTALL.md
  用途：自定义技能安装指南——目录约定与注册流程。目录结构与模板已验证匹配。
  读者：CODEX、实现层。

- `[LIVING]` docs/50_project/guides/DOCUMENT_SYNC_WORKFLOW.md
  用途：文档过期治理与同步工作流（PROJECT 专用）。
  读者：MASTER、Project（文档代理）。

- `[LIVING]` docs/50_project/guides/DEV_TEST_REPORT_TEMPLATE.md
- `[LIVING]` docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md
  用途：开发实测 / 运行反馈报告模板。
  读者：实现层（模板使用）。

### 5.3 架构文档（当前事实）

- `[LIVING]` docs/50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md (v1.0)
  用途：基于实际代码审计的完整目标架构蓝图；定义最终运行拓扑、包边界、Bounded Context、唯一真相源、M48～M57 路线图与最终架构 Gate。它不是直接任务来源，实施以完整架构重构正式执行程序为准。
  读者：MASTER、Project、实现层（架构依据）。

- `[LIVING]` docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md (v4.2)
  用途：当前代码事实总览——运行面、主入口、路由面、聊天主链、Persona/Surface 管线、Pentest 双规划器、Tribe 规模、Monitor UI 路由分组、Digital Life 18 端点。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/NATSUME_AI_V1_LONG_TERM_EVOLUTION_REFACTOR_PLAN_20260710.md
  用途：M48-A 的代码事实审计、局部架构裁决与迁移依据；不是完整目标架构或直接任务来源。
  读者：MASTER、Project、实现层（背景与证据）。

- `[LIVING]` docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md (v4.2)
  用途：核心模块定位与职责边界（含 persona surface/scoring 管线、life_memory 子包）。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md (v1.1)
  用途：Pentest 子系统专项架构索引。覆盖 tools、agent（31 文件清单）、adversarial、swarm、online、report（标注存根）、data 与安全边界、双规划器说明。
  读者：MASTER、Project、实现层。

- `[LIVING]` host_brain/pentest/CAPABILITY.md (v2.3)
  用途：Pentest 能力边界、ToolSpec/Executor 计数、side-effect guard、agent 模块能力矩阵（31 模块）。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md
  用途：数字生命体前端主体化方案——Monitor UI 页面分组与信息架构。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/MODE_TRANSITION_SYSTEM.md
  用途：智能模式切换系统——6 种模式、ModeTransitionService、ActivityMonitor。已验证与代码一致。
  读者：MASTER、实现层。

- `[LIVING]` docs/50_project/arch/dataflow_report.md (v3.3)
  用途：系统数据流全景报告——请求流、回流、模块间数据交换。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/diagrams/00_INDEX.md (v1.0)
  用途：架构图册总目录——7 张 Mermaid 图。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/GATEWAY_ZH_CHAIN_NODE_ANALYSIS.md (v2.1)
  用途：Gateway 中文文字链路完整节点分析（当前代码实况）。已验证与代码一致。
  读者：MASTER、架构参考。

- `[LIVING]` docs/50_project/arch/UNIFIED_MESSAGE_GATEWAY_DESIGN.md
  用途：统一消息网关设计（混合文档，文首已加当前代码校准说明；正文含历史方案留痕）。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/AGENT_ORCHESTRATION_DETAIL.md (v2.4)
  用途：Agent 编排详情（当前运行时实况）。已验证与代码一致。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/AGENTIC_RAG_ARCHITECTURE.md (v3.0)
  用途：当前召回与记忆数据流。文件名保留旧 `AGENTIC_RAG` 兼容名，但正文已按 RecallFusion + LifeMemoryStore + SurfaceMainChain 当前链路重写。
  读者：MASTER、Project、实现层。

- `[LIVING]` docs/50_project/arch/NATSUME_AI_FULL_LLM_CAPABILITY_RESTORATION_PLAN_20260422.md
  用途：LLM 能力恢复蓝图（混合文档：前半已落库，后半为原始计划留痕）。阅读时需区分现状与原始计划。
  读者：MASTER、实现层、Project。

- `[LIVING]` docs/50_project/index.md
  用途：Flow 资产追溯索引（按环境），支撑 T-Flow-02 回归口径。
  读者：MASTER、Project。

### 5.4 架构文档（历史留痕）

以下文档为历史方案/蓝图/重构计划，已被当前代码主链取代，仅作历史对照：

- `[ARCHIVE]` docs/50_project/arch/PHASE_X_INTELLIGENT_ORCHESTRATION.md
  用途：Phase X 智能编排层六大方案留痕（2026-03-29）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/PHASE_XI_AGENT_ARCHITECTURE_UPGRADE.md
  用途：Phase XI 智能体架构升级七大方案留痕（2026-03-29）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/PHASE_XII_HOST_LOCAL_CONTROL.md
  用途：Phase XII Host 本地操控能力方案留痕（2026-03-29）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/DIGITAL_LIFE_MULTI_AGENT_BLUEPRINT_V1_20260408.md
  用途：数字生命多智能体族蓝图留痕（2026-04-08）。正文含 top_agent.py 等未落地入口。
  读者：MASTER、Project（历史参考）。

- `[ARCHIVE]` docs/50_project/arch/PARENT_CHILD_AGENT_PERSONA_RENDERING.md
  用途：Phase XIV 父子级渲染历史方案（2026-03-31）。正文含 SceneRouter / main_chain.py 等旧链路。
  读者：MASTER、Project（历史参考）。

- `[ARCHIVE]` docs/50_project/arch/PHASE_XIII_STYLE_SYSTEM_OVERHAUL.md
  用途：Phase XIII 风格系统重构方案留痕（2026-03-30）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/DIGITAL_LIFE_FUSION_V2_20260408.md
  用途：数字生命架构融合方案 V2 留痕（2026-04-08）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/PHASE_XV_DISPATCH_CHAT_ARCHITECTURE.md
  用途：Phase XV Dispatch/Chat 分离历史方案与问题复盘（2026-04-01）。
  读者：MASTER、Project、实现层（历史对照）。

- `[ARCHIVE]` docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md
  用途：TRIBE 多智能体族重构蓝图留痕（2026-04-08）。
  读者：MASTER、Project、实现层（历史对照）。

### 5.5 Multi-Agent 阶段文档（全部归档）

所有 P4–P15 的 SCOPE / SEAL_RESULT / 子阶段文档均为已完成里程碑的历史记录：

- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_UPGRADE_PLAN.md — 多 Agent 编排升级历史阶段方案
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P4_SCOPE.md — P4 范围与验收
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P5_SCOPE.md / MULTI_AGENT_P5_SEAL_RESULT.md — P5 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P6_SCOPE.md / P6_SEAL_RESULT.md / P6_1~P6_3 子文档 — P6 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P7_SCOPE.md / P7_SEAL_RESULT.md / P7_2~P7_4 + P7_ACCEPTANCE_PREVIEW — P7 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P8_SCOPE.md / P8_SEAL_RESULT.md — P8 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P9_SCOPE.md / P9_SEAL_RESULT.md — P9 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P10_SCOPE.md / P10_SEAL_RESULT.md — P10 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P11_SCOPE.md / P11_SEAL_RESULT.md — P11 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P12_SCOPE.md / P12_SEAL_RESULT.md — P12 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P13_SCOPE.md / P13_SEAL_RESULT.md — P13 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P14_SCOPE.md / P14_SEAL_RESULT.md — P14 范围与封板
- `[ARCHIVE]` docs/50_project/phases/MULTI_AGENT_P15_SCOPE.md / P15_SEAL_RESULT.md — P15 终点封板

### 5.6 历史架构快照归档

- `[ARCHIVE]` docs/_history/README.md — 历史架构快照归档总说明
- `[ARCHIVE]` docs/_history/2026-04-22_arch_snapshot/INDEX.md — 2026-04-22 架构快照目录说明
  读者：MASTER、Project（仅用于历史追溯）。

---

## 6）可追溯性（Trace）

- `[LIVING]` docs/60_trace/requirements_trace.md
  用途：审阅"需求→设计→任务→测试"覆盖情况。
  读者：MASTER、Project。

- `[ARCHIVE]` docs/60_trace/flows/index.md
  用途：Flow 指针说明（当前为占位型环境指针）。
  读者：MASTER、Project。

- `[LIVING]` docs/60_trace/risk_registry.md
  用途：风险登记册。
  读者：MASTER、Project。

- `[LIVING]` docs/60_trace/VTS_VOICE_TROUBLESHOOTING.md
  用途：VTS 语音故障排查手册。
  读者：MASTER、Project。

- `[ARCHIVE]` docs/60_trace/BUG_FIX_LOG.md
  用途：Bug 修复日志（全量排查记录）。
  读者：MASTER、实现层。

- `[ARCHIVE]` docs/60_trace/ENV_SINGLE_SOURCE_MIGRATION_2026-03-04.md — 环境变量迁移记录
- `[ARCHIVE]` docs/60_trace/CANON_VECTOR_PROJECTION_2026-03-04.md — Canon 向量投影分析
- `[ARCHIVE]` docs/60_trace/ARCH_REVIEW_AND_ROADMAP.md — 架构评审与路线图（2026-03-09）
- `[ARCHIVE]` docs/60_trace/RETRIEVAL_ORCHESTRATOR_DESIGN.md — 检索编排器设计（2026-03-09）
- `[ARCHIVE]` docs/60_trace/README_trial_run_reports.md — 试运行报告目录说明

---

## 7）提示词入口

- `[LIVING]` prompts/CODEX_PROMPT.md — Codex 入口提示词
- `[LIVING]` prompts/PROJECT_PROMPT.md — Project 入口提示词
- `[LIVING]` prompts/llm_contract/* — LLM 输出契约/拒绝范式（契约资产）
  读者：按文件受众标注执行。

---

## 8）理论文档（Theory）

### 8.1 理论层契约（当前代码实况）

以下 7 份契约文档以当前代码为锚点，标记 `[LIVING]`：

- `[LIVING]` docs/15_theory/CORE_LAYER_MODULE_REFERENCE.md (v3.1) — host_brain/core/ 模块参考
- `[LIVING]` docs/15_theory/INTEGRATIONS_LAYER_CONTRACT.md (v3.1) — integrations 层接口契约
- `[LIVING]` docs/15_theory/RETRIEVAL_LAYER_CONTRACT.md (v3.1) — retrieval 层接口契约
- `[LIVING]` docs/15_theory/GOVERNOR_SCHEDULER_CONTRACT.md (v3.1) — governor + scheduler 契约
- `[LIVING]` docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md (v3.3) — tools + memory 契约
- `[LIVING]` docs/15_theory/PERIPHERAL_LAYERS_CONTRACT.md (v2.4) — 外围层契约（airlock/bond/channels/vision/lsp/mcp）
- `[LIVING]` docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md (v3.3) — 应用/审计/技能层契约

### 8.2 架构全景

- `[LIVING]` docs/15_theory/architecture_overview.md (v3.3) — 整体架构全景文档（7 运行面 Mermaid 图 + Pentest/Proactive 说明）
  读者：MASTER、Project、实现层（查阅对齐）。

### 8.3 理论基础

- `[LIVING]` docs/15_theory/Theory.md
  用途：解释"为什么这样设计"、关键机制的理论依据。若与代码事实冲突，以代码和架构文档为准。
  读者：MASTER、Project。

### 8.4 理论/设计历史留痕

- `[ARCHIVE]` docs/15_theory/soul_emotion_optimization.md — 灵魂情感架构优化设计留痕（2026-03-05，待实施）
- `[ARCHIVE]` docs/15_theory/LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md — LangGraph 约束冲突分析（2026-03-06）
- `[ARCHIVE]` docs/15_theory/LangGraph_Governed_Runtime_Solution.md — Governed Runtime 理论方案留痕（2026-03-06）
- `[ARCHIVE]` docs/15_theory/SOUL_SYSTEM_FULL_UPGRADE_PATH.md — 灵魂系统升级蓝图留痕（2026-03-23）

---

## 9）质量与审计（35_quality）

### 9.1 质量方案 / 盘点（历史留痕）

- `[ARCHIVE]` docs/35_quality/GATEWAY_ARCH_REDESIGN.md — Gateway 架构债盘点（2026-04-15）
- `[ARCHIVE]` docs/35_quality/ORGAN_TRIBE_UPGRADE_PLAN.md — 器官族升级盘点（2026-04-16，已全部 A 级完成）
- `[ARCHIVE]` docs/35_quality/audits/DIGITAL_LIFE_GAP_CHECKLIST_V2_20260403.md — 数字生命差距清单 V2
- `[ARCHIVE]` docs/35_quality/AGENT_RUNTIME_SHARED_BREAKDOWN.md
- `[ARCHIVE]` docs/35_quality/ARCH_FIX_PLAN.md
- `[ARCHIVE]` docs/35_quality/ISOLATED_SURFACE_GENERATION_HARNESS.md
- `[ARCHIVE]` docs/35_quality/LIVE_ACCEPTANCE_VALIDATION.md
- `[ARCHIVE]` docs/35_quality/OPTIMIZATION_PLAN_20260420.md
- `[ARCHIVE]` docs/35_quality/SAMPLE_GUIDED_TEXT_QUALITY.md
- `[ARCHIVE]` docs/35_quality/SELF_BUILT_MECHANISM_FIX_PLAN.md
- `[ARCHIVE]` docs/35_quality/SURFACE_SCENE_QUALITY_SAMPLES.md
- `[ARCHIVE]` docs/35_quality/TEMPLATE_AUDIT_2026-04-21.md
- `[ARCHIVE]` docs/35_quality/TEMPLATE_ROLE_CATALOG_2026-04-21.md
- `[ARCHIVE]` docs/35_quality/TEXT_STYLE_STANDARD_GALGAME.md

### 9.2 审计报告

- `[LIVING]` docs/35_quality/audits/MEMORY_SYSTEM_INTEGRITY_TEST_20260531.md — 记忆系统完整性实测报告（2026-05-31，近期）
- `[LIVING]` docs/35_quality/audits/RUNTIME_CHAIN_ACCEPTANCE_20260531.md — 当前运行链路验收报告（2026-05-31，近期）
- `[ARCHIVE]` docs/35_quality/audits/SOUL_SYSTEM_COMPLETENESS_AUDIT_20260326.md — 灵魂系统完整性审计（2026-03-26）
- `[ARCHIVE]` docs/35_quality/audits/audit_report_20260311.md — Natsume AI 深度审计报告（2026-03-11）

### 9.3 代码审查报告

- `[ARCHIVE]` docs/35_quality/code_reviews/ — 全部代码审查报告归档（全量审查、里程碑专项、仓库分析）
  读者：MASTER、Project。

---

## 10）测试报告（全部归档）

以下测试报告均为一次性执行记录，标记 `[ARCHIVE]`：

### 10.1 专项测试报告
- `[ARCHIVE]` docs/45_testing/test_reports/test_report_2026-03-05.md — 全量测试报告
- `[ARCHIVE]` docs/45_testing/test_reports/EDGE_OPTIMIZATION_AND_TEST_REPORT_2026-03-04.md — Edge 优化与测试报告
- `[ARCHIVE]` docs/45_testing/test_reports/EMOTION_CLOSED_LOOP_REPORT_20260309_235917.md — 情感闭环测试报告

### 10.2 开发实测报告
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_telegram_adapter_20260326.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_qq_adapter_enhancement_20260326.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wechat_voice_send_20260326.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_line_adapter_20260326.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_emotion_system_fixes_2026-03-26.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_bridge_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase1_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase2_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_wxautox4_phase3_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_mode_transition_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_p0_p1_fixes_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_recall_fusion_bugfix_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_emotion_pipeline_fix_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_asr_complex_tts_fix_20260327.md
- `[ARCHIVE]` docs/45_testing/dev_test_reports/DEV_TEST_REPORT_continuity_memory_sync_write_20260401.md

### 10.3 运行反馈报告
- `[ARCHIVE]` docs/45_testing/runtime_feedback_reports/RUNTIME_FEEDBACK_qq_adapter_enhancement_20260326.md

---

## 11）发布与回滚（50_release）

### 11.1 变更日志
- `[ARCHIVE]` docs/50_release/changelog/UI_UPGRADE_CHANGELOG.md — UI 升级变更日志
- `[ARCHIVE]` docs/50_release/changelog/MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md — 模型切换变更日志

### 11.2 封板相关
- `[ARCHIVE]` docs/50_release/release_seal_test_plan_20260313.md — 封板测试计划
- `[ARCHIVE]` docs/50_release/release_seal_test_plan_supplement_20260315.md — 封板测试计划补充
- `[ARCHIVE]` docs/50_release/release_seal_execution_report_20260316.md — 封板执行报告
- `[ARCHIVE]` docs/50_release/reports/release_seal_20260316/00_index/* — 封板批次报告

### 11.3 回滚
- `[LIVING]` docs/50_release/rollback/ROLLBACK_RUNBOOK_2026-03-10.md — 回滚操作手册（运维参考）
- `[ARCHIVE]` docs/50_release/rollback/RELEASE_ROLLBACK_GATE_20260310_010648.md — 回滚门控记录
- `[ARCHIVE]` docs/50_release/rollback/POST_RELEASE_REVIEW_20260310_010918.md — 发布后复盘

---

## 12）性能与稳定性监控（全部归档）

- `[ARCHIVE]` docs/55_testing/metrics/baseline_stream_metrics_2026-03-09.md — 流式响应基线
- `[ARCHIVE]` docs/55_testing/metrics/LONGTERM_METRIC_DASHBOARD_20260310_014526.md — 长期指标看板
- `[ARCHIVE]` docs/55_testing/stability_weekly/WEEKLY_STABILITY_REPORT_20260310_005912.md — 周度稳定性报告

---

## 13）配置资产入口

- `[LIVING]` flows/ — Flow 导出物与环境指针（exports/ + active.pointer）
- `[LIVING]` .env / .env.example — 运行时配置源
- `[LIVING]` configs/model_plane/ — 模型平面配置（node_registry.yaml + model_profiles.yaml）
- `[ARCHIVE]` configs/release/mXX_release.json — 各里程碑发布清单
- `[ARCHIVE]` configs/release/release_catalog.json — 发布资产目录
- `[LIVING]` configs/release/release_rollback_map.json — 发布回滚映射表
- `[ARCHIVE]` artifacts/release/reports/ — 发布报告归档
- `[ARCHIVE]` artifacts/trial_run_reports/ — 自动化试运行报告归档
- `[ARCHIVE]` artifacts/trace/*.json — 运行时原始 JSON 追踪数据

---

## 统计

| 标记 | 数量（约） | 说明 |
|------|-----------|------|
| `[LIVING]` | ~55 | 跟随代码变更，必须同步更新 |
| `[ARCHIVE]` | ~85 | 历史留痕，只读不更新 |

---

附：实现层任务来源白名单（强约束）
- 仅允许将以下目录作为"任务来源"读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中明确点名路径与引用范围。

附：受众门禁（文档头部强制）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`

附：文档归类规则
- 代码审查报告 → docs/35_quality/code_reviews/
- 测试报告、变更日志 → docs/60_trace/（历史）或 docs/45_testing/（专项）
- 架构分析、理论推导 → docs/15_theory/
- 配置指南 → docs/50_project/guides/
- 运行手册、项目协作 → docs/50_project/
- 架构文档（代码架构、数据流、Agent 编排）→ docs/50_project/arch/
- Multi-Agent 阶段文档 → docs/50_project/phases/
- 发布清单 → configs/release/
- 封板/回滚 → docs/50_release/
- 性能指标 → docs/55_testing/
- 开发规范 → docs/30_devspec/
