受众：MASTER
标题：文档地图（唯一入口）

版本：v1.1
日期：2026-03-03

本仓库所有“应该读什么、从哪里开工、哪些文档禁止当任务来源”，以本文件为准。

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
  用途：定义不可变边界、不变量、真相/风格层级、人格表达约束；作为“法律与人设”。含 sys_mode/Focus 子状态、Scheduler 宪法约束、NARRATIVE_ONLY 宪法定位、PERF/SCHED 系列错误码。
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
  用途：模型平面规格（多模型节点分工、路由、降级、出网最小化、审计字段、回归口径）；用于“可回放、可追责”的模型配置治理。
  读者：MASTER、实现层（按契约实现与验证）。

4）代理行为规范（实现层）
- docs/40_agents/AGENTS.md
  用途：实现层的工作准则：任务来源门禁、交付包格式、不变量优先、测试编号（T-xx）口径。
  读者：实现层（主要）、MASTER。

5）Project 指令（文档生产，不含代码）
- docs/50_project/PROJECT_BRIEF.md
- docs/50_project/PROJECT_TASKS.md
- docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md
- docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md
- docs/50_project/ExternalServices_Config.md
  用途：给 Project（文档代理）的产出约束、任务拆解、以及 Project×实现层×版本库 的协作工作表；不包含终端指令片段。
  读者：Project（主要）、MASTER。实现层不应把它当任务来源。

6）可追溯性（Trace）
- docs/60_trace/requirements_trace.md
- 可选：docs/60_trace/requirements_trace.csv
- docs/60_trace/flows/index.md
  用途：审阅“需求→设计→任务→测试”覆盖情况；以及 Flow 指针与索引的一致性。
  口径：若 csv 未维护或滞后，以 md 为准。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

7）提示词入口
- prompts/CODEX_PROMPT.md
- prompts/PROJECT_PROMPT.md
- prompts/llm_contract/*
  用途：分别作为实现层与 Project 的入口提示词；以及 LLM 输出契约/拒绝范式等（作为契约资产，不作为需求来源）。
  读者：按文件受众标注执行。

8）理论文档（Theory：论证/原则推导/边界解释）
- docs/15_theory/Theory.md
  用途：解释“为什么这样设计”、关键机制的理论依据与判定口径；不作为实现任务清单。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源（除非 MASTER 任务卡点名引用）。

9）协作与运行手册（Worksheet：分工/流程/落地节奏）
- docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md
  用途：明确 Project×实现层×Master 的分工边界、交接物与防污染规则；不包含终端指令片段。
  读者：MASTER、Project（文档代理）。实现层不应把它当任务来源。

10）配置资产入口（非文档，但属于“可部署/可回放”真相源的一部分）
- flows/
  用途：Flow 导出物与环境指针（exports/ + active.pointer）；由 Trace（docs/60_trace/flows/index.md）指向当前生效版本。
  读者：MASTER、实现层（按 DevSpec/DevTasks 约束维护与回归）。

- configs/model_plane/
  用途：模型平面机器可读配置（node_registry / model_profiles 等），用于版本化、审计回放与成本/延迟治理。
  读者：MASTER、实现层（按 ModelPlane/DevSpec 契约实现与回归）。

---

附：实现层任务来源白名单（强约束）
- 仅允许将以下目录作为“任务来源”读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中明确点名路径与引用范围。

附：受众门禁（文档头部强制）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`
