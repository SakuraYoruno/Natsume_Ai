受众：MASTER
标题：文档地图（唯一入口）

本仓库所有“应该读什么、从哪里开工、哪些文档禁止当任务来源”，以本文件为准。

---

1）白皮书（宪法 + 人格/风格设定）
- docs/10_whitepaper/whitepaper_constitution_persona.md  
  用途：定义不可变边界、不变量、真相/风格层级、人格表达约束；作为“法律与人设”。  
  读者：MASTER、Project（文档代理）。Codex 不应把它当任务来源。

2）需求文档（PRD）
- docs/20_prd/PRD.md  
  用途：定义要做什么（范围、用户故事、验收、非功能指标），不描述实现步骤。  
  读者：MASTER、Project（文档代理）。Codex 不应把它当任务来源（除非 MASTER 明确点名引用）。

3）开发文档（DevSpec + 任务）
- docs/30_devspec/DevSpec.md  
  用途：把 PRD 落到模块、接口、协议、Schema、日志、错误码、状态机；可直接指导实现。  
  读者：Codex（主要）、MASTER。

- docs/30_devspec/DevTasks.md  
  用途：Codex 可逐条执行的任务清单（每条都有输入/输出/验收）。  
  读者：Codex（唯一）。

4）代理行为规范（Codex）
- docs/40_agents/AGENTS.md  
  用途：Codex 的工作准则：一次改哪些文件、提交规范、禁止事项、测试要求。  
  读者：Codex（主要）、MASTER。

5）Project 指令（文档生产，不含代码）
- docs/50_project/PROJECT_BRIEF.md  
- docs/50_project/PROJECT_TASKS.md  
  用途：给 Project（文档代理）的产出约束与任务拆解；不含代码执行命令。  
  读者：Project（主要）、MASTER。Codex 不应把它当任务来源。

6）可追溯性（Trace）
- docs/60_trace/requirements_trace.md  
- docs/60_trace/requirements_trace.csv  
  用途：审阅“需求→设计→实现→测试”覆盖情况；不含执行命令。  
  读者：MASTER、Project（文档代理）。Codex 不应把它当任务来源。

7）提示词入口
- prompts/CODEX_PROMPT.md  
- prompts/PROJECT_PROMPT.md  
  用途：分别作为 Codex 与 Project 的入口提示词（受众与职责必须清晰分离）。

8）理论文档（Theory：论证/原则推导/边界解释）
- docs/15_theory/Theory.md  
  用途：解释“为什么这样设计”、关键机制的理论依据与判定口径；不作为实现任务清单。  
  读者：MASTER、Project（文档代理）。Codex 不应把它当任务来源（除非 MASTER 明确点名引用）。

9）协作与运行手册（Worksheet：分工/流程/落地节奏）
- docs/50_project/WORKSHEET_Project_Codex_Git_Dify.md  
  用途：明确 Project×Codex×Master 的分工边界、工作流、交接物与防污染规则；不含代码执行命令。  
  读者：MASTER、Project（文档代理）。Codex 不应把它当任务来源。

---

附：Codex 任务来源白名单（强约束）
- 仅允许将以下目录作为“任务来源”读取并据此行动：
  - docs/30_devspec/**
  - docs/40_agents/**
- 若需引用其它文档，必须由 MASTER 在任务卡中“明确点名路径 + 引用范围”。

附：受众门禁（pre-commit）
- 所有 .md 文件必须以两行头部开场：
  - `受众：MASTER|CODEX|PROJECT`
  - `标题：...`
