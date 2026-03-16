受众：MASTER
标题：文档地图（唯一入口）

版本：v1.3
日期：2026-03-12

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
  用途：把 PRD 落到模块、接口、协议、Schema、日志、错误码、状态机；可直接指导实现。
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
- docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md
- docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md
- docs/50_project/ExternalServices_Config.md
- docs/50_project/vts_hotkey_config_guide.md
  用途：给 Project（文档代理）的产出约束、任务拆解、以及 Project×实现层×版本库 的协作工作表；配置指南与外部服务清单；不包含终端指令片段。
  读者：Project（主要）、MASTER。实现层不应把它当任务来源。

- docs/50_project/index.md
  用途：Flow 资产追溯索引（按环境），记录 flows/active.pointer 指向的当前生效 Flow 导出物；支撑 T-Flow-02 回归口径。
  读者：MASTER、Project（文档代理）。

- docs/50_project/MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md
  用途：多 Agent 编排升级详细实施方案（2026-03-16）。基于当前单 agent 多节点路由架构，制定分四阶段升级为"Host 主权 + Supervisor 主 agent + 多专家子 agent + Judge 收口"体系的可落地方案。含角色定义、数据契约、各场景链路设计、文件改动清单、验收标准与风险评估。
  读者：MASTER、Project（文档代理）。

6）可追溯性（Trace）
- docs/60_trace/requirements_trace.md
  用途：审阅"需求→设计→任务→测试"覆盖情况。
  口径：若 csv 未维护或滞后，以 md 为准。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

- docs/60_trace/flows/index.md
  用途：Flow 指针与索引的一致性。
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

- docs/50_release/changelog/UI_UPGRADE_CHANGELOG.md
  用途：UI 升级变更日志。
  读者：MASTER、Project（文档代理）。

- docs/50_release/changelog/MODEL_LOCAL_CLOUD_CHANGELOG_2026-03-05.md
  用途：模型本地/云端切换变更日志（2026-03-05）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/ENV_SINGLE_SOURCE_MIGRATION_2026-03-04.md
  用途：环境变量单一来源迁移记录（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/CANON_VECTOR_PROJECTION_2026-03-04.md
  用途：Canon 向量投影分析（2026-03-04）。
  读者：MASTER、Project（文档代理）。

- docs/45_testing/test_reports/host_chat/HOST_CHAT_CHAIN_REAL_REPORT_2026-03-06.md
  docs/45_testing/test_reports/host_chat/HOST_CHAT_REAL_CHAIN_JA_RENDER_VERIFY_2026-03-05.md
  docs/45_testing/test_reports/host_chat/HOST_CHAT_REAL_CHAIN_TRACE_req_*.md
  docs/45_testing/test_reports/host_chat/HOST_FULL_CHAIN_REAL_RUN_2026-03-05.md
  docs/45_testing/test_reports/host_chat/HOST_REAL_RUN_TEST_2026-03-04.md
  用途：真实链路运行追踪报告（按日期/请求ID归档）。
  读者：MASTER、Project（文档代理）。

- artifacts/trace/HOST_CHAT_*_req_*.json
  artifacts/trace/CANON_STYLE_SYNC_*.json
  artifacts/trace/canon_sync_summary_*.json
  用途：运行时原始 JSON 追踪数据（模型控制、检索事件、Canon 同步等）。
  读者：MASTER（调试/回放用）。

- docs/35_quality/audits/audit_report_20260311.md
  用途：Natsume AI 深度审计报告（2026-03-11）。覆盖意图识别、模型编排控制、Agent 路由、SSE 双端对齐、情感层接入、Bond 优化六大维度。
  读者：MASTER、Project（文档代理）。

- docs/60_trace/dataflow_report_20260311.md
  用途：Natsume AI 系统数据流全景报告（2026-03-11，持续更新）。覆盖双端通信、各功能/模块数据流、Agent 治理与路由、Graph 编排；含严格代码审计与修复记录（§13/§16）。
  读者：MASTER、Project（文档代理）。

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
  用途：解释"为什么这样设计"、关键机制的理论依据与判定口径；不作为实现任务清单。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

- docs/15_theory/soul_emotion_optimization_2026-03-05.md
  用途：灵魂情感架构优化设计留痕文档（2026-03-05）。记录五大优化方向：①情绪连续体 EmotionVector、②Bond 情感质感层、③情感记忆标注、④emotion_pulse 动态槽位、⑤主动情感触发器。含完整伪代码设计方案、优先级排序与安全约束说明。待实施，不作为当前任务来源。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS_2026-03-06.md
  用途：LangGraph 高级能力与 Host 治理约束冲突分析（2026-03-06）。分析五项冲突点（条件边、并行分支、子图、断点续跑、流式输出）与当前系统约束的关系。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/LangGraph_Governed_Runtime_Solution_2026-03-06.md
  用途：LangGraph Governed Runtime 更优解法方案（2026-03-06）。提出三层架构（Policy Plane / Execution Plane / Evidence Plane）、Host Governance Overlay 方案、五阶段能力恢复路径与三句话架构结论。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/architecture_overview_20260310.md
  用途：Natsume AI 整体架构全景文档（2026-03-10）。含双端总体架构、Host Brain 各模块、Edge 各模块、数据流、记忆层、治理层的完整 Mermaid 图与说明。
  读者：MASTER、Project（文档代理）。

- docs/15_theory/arch_analysis_report_20260311.md
  用途：Natsume AI 架构分析报告（2026-03-11）。对整体架构全景文档的深度评审：成熟特征、薄弱环节、优先级建议与落地路径。
  读者：MASTER、Project（文档代理）。

9）协作与运行手册（Worksheet：分工/流程/落地节奏）
- docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md
  用途：明确 Project×实现层×Master 的分工边界、交接物与防污染规则；不包含终端指令片段。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

10）配置资产入口（非文档，但属于"可部署/可回放"真相源的一部分）
- flows/
  用途：Flow 导出物与环境指针（exports/ + active.pointer）；由 Trace（docs/60_trace/flows/index.md）指向当前生效版本。
  读者：MASTER、实现层（按 DevSpec/DevTasks 约束维护与回归）。

- 根目录 .env / .env.example
  用途：运行时单一配置源（`.env`）与示例/降级模板（`.env.example`）；用于 Host/Edge/Neo4j/Chroma/Retrieval 统一配置。
  读者：MASTER、实现层（按 DevSpec 与运行脚本约束实现与回归）。

11）发布目录（Release）
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

附：实现层任务来源白名单（强约束）
- 仅允许将以下目录作为"任务来源"读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中明确点名路径与引用范围。

附：受众门禁（文档头部强制）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`

附：文档归类规则（v1.2 新增）
- 代码审查报告（code_review_*.md、full_code_review_report_*.md）→ docs/35_quality/code_reviews/
- 测试报告、运行追踪报告、变更日志、审计报告、数据流报告 → docs/60_trace/
- 架构全景、架构分析、理论推导、设计方案 → docs/15_theory/
- 配置指南、运行手册、项目协作文档、Flow 资产索引 → docs/50_project/
- 发布清单（JSON）、发布目录、回滚映射 → configs/release/
- 开发规范、任务清单、模型平面 → docs/30_devspec/
- 任务看板、TODO 追踪 → docs/40_agents/
