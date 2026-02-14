受众：CODEX
标题：DevTasks（可逐条执行的任务清单）

规则：每条任务必须写清 输入/输出/验收/影响文件范围。不要写背景故事。

TASK-001：建立“受众头 + 文档门禁”基础设施
- 输入：本仓库 docs/ 与 prompts/ 目录
- 输出：
  1）tools/doc_audience_lint.py 可运行
  2）.pre-commit-config.yaml 可用
  3）提交时能拦截受众不匹配/禁词混入
- 验收：
  - pre-commit run -a 全部通过
  - 故意在 PRD 写入“codex/提交/patch”会被阻止

TASK-002：把现有 PRD/DevSpec/AGENTS/Trace 迁移到新目录并修复受众头
- 输入：旧文件（例如 prd_v0.3_codex_ready.md 等）
- 输出：迁移后的路径与文件名符合 docs/00_index/00_DOCMAP.md 的约定
- 验收：doc_audience_lint.py 对迁移后的全仓库通过

TASK-003：为 Codex 工作流补齐“只读输入列表”与“允许改动列表”
- 输入：docs/40_agents/AGENTS.md
- 输出：明确列出 Codex 允许读取的文档路径与允许修改的代码路径
- 验收：AGENTS.md 中存在“允许读取/禁止读取/允许修改/禁止修改”四个小节

（后续工程任务请在此文件继续追加，每条同样格式。）
