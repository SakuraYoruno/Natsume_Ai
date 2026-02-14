受众：MASTER
标题：文档/代理分工与防混写工作流（落地版）

本仓库把内容强制拆成三类，并用“物理隔离 + 受众头 + 预提交门禁”保证不再混写。

一、三类内容与唯一入口
1）给你（MASTER）：只描述“你要怎么做、怎么验收、怎么发布、怎么回滚”。禁止出现对代理的命令式指令。
   入口：docs/00_index/00_DOCMAP.md

2）给 Codex（CODEX/CLI）：只描述“必须执行的任务清单 + 约束 + 输出契约 + 验收方式”。禁止长篇背景与讨论。
   入口：docs/30_devspec/DevTasks.md 与 docs/40_agents/AGENTS.md

3）给 ChatGPT Project（PROJECT）：只描述“要产出/更新哪些文档、更新规则、版本策略”。禁止出现改代码/提交/跑测试。
   入口：docs/50_project/PROJECT_TASKS.md

二、强制约束（不靠自觉）
- 每份文档首行必须写：受众：MASTER | CODEX | PROJECT
- 文档路径与受众必须匹配（例如 docs/30_devspec 下必须是 CODEX）
- pre-commit 会在提交前运行 tools/doc_audience_lint.py，发现混写直接拒绝提交

三、你需要做的事（一次性）
1）把本脚手架目录合并到你的 Git 仓库根目录（保持路径不变）
2）在仓库根目录启用 pre-commit：
   - 安装：python -m pip install pre-commit
   - 启用：pre-commit install
   - 手动全量检查：pre-commit run -a

四、日常使用节奏（推荐）
- 讨论/定规范：只改 docs/10_whitepaper 与 docs/20_prd
- 给 Codex 干活：只引用 docs/30_devspec 与 docs/40_agents，外加你点名的代码文件
- 给 Project 写文档：只引用 docs/10_whitepaper、docs/20_prd、docs/60_trace

五、版本策略（建议）
- 白皮书（宪法/人格）变更较大：升 whitepaper 版本（例如 v1.6.2 → v1.7.0）
- PRD 变更：升 prd 版本（例如 v0.3 → v0.4）
- DevSpec/任务分解变更：升 devspec 版本（例如 v0.3 → v0.4）
