# A15 Code PlanningSearch 固定队列

本目录冻结 20 个小型 Python 修复任务。它们只用于 A15 收益实验，不是生产 Skill 或训练语料。

- runner 从同一配置生成一个确定性的干净 Git 源提交；
- baseline 与 selected 使用同一 Code PlanningSearch 内核、provider、model 和预算；
- baseline 关闭 L3，selected 使用 AUTO 条件 MCTS；
- 每只 arm 都在独立 detached worktree 中只允许修改一个目标文件；
- 外部模型看不到 `cases`，隐藏行为测试和静态安全测试由本地独立子进程执行；
- 配置中的错误次级证据和权威证据用于验证“搜索选择证据”是否带来真实通过率收益，不进入生产策略；
- 模型正文、目标源码和隐藏用例不写入 Code/cohort durable ledger，账本仅保存摘要、usage、成本、测试和清理收据。
- v1 完整运行在第 6 题遇到 provider timeout 后永久保留为失败证据；v2 冻结 `CODE_BENCHMARK` 的低温、600-token、non-thinking JSON generation profile，但预检暴露 objective 泄漏正确语义；v3 改用统一中性 objective，不覆盖或挑选旧成员。
- durable workload registration 使用 `<固定题号>@<cohort_version>`，同题的新版本不会复用旧 ledger identity，也不会因 config drift 覆盖 v1。

dry-run 只能证明队列、搜索路径和冻结源码可构造，不能作为收益证据。`3eaa5486b` 的 v3 已完成 20 组真实双臂、40 个外部模型请求、40 个 fresh worktree 的隐藏／静态验证并得到 `PASS`；完整文件 SHA-256 为 `9c9938f15d303f5699debc60af5d7ebf09b417f70b4fd2824e8115cf12062170`，生产影响仍为 false。
