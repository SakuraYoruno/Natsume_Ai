# Research PlanningSearch 固定收益队列

本目录冻结 A15 Research 的 24 个配对 workload，分为运行时恢复、存储持久化和模型证据三个主题。每个主题同时包含项目本地 primary、官方 Web 独立来源的短释义快照和信息不完整的 secondary 摘要。

baseline 与 search arm 都由同一个 `GoalPlanningService` 产生：baseline 关闭 L3，只选择 secondary；search 使用 AUTO 条件搜索，必须选择 primary 与 independent。期望覆盖点只交给独立隐藏质量评审，不进入 `ResearchTaskSnapshot`、搜索候选或执行提示。

来源正文、引用范围和 SHA-256 都由 `cohort.json` 冻结。运行时用 UTF-8 byte offset 重新打开文件验证引用，不访问网络。官方来源为：

- Python asyncio：https://docs.python.org/3.14/library/asyncio-task.html
- Redis persistence：https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
- DeepSeek pricing：https://api-docs.deepseek.com/quick_start/pricing

无外部调用的完整准备检查：

```powershell
python scripts/runtime/run_research_benefit_cohort.py --dry-run
```

真实运行必须额外传入隔离 SQLite、运行环境、独立 secret 文件、忽略目录下的证据路径和从未使用过的 `run-namespace`。先以 `--limit 1` 预检；只有预检通过后才允许执行 24 题。终态重放不会恢复回答正文，因此中断后不得沿用同一 namespace 补签质量分数。
