受众：PROJECT
标题：Governed Runtime 验收证据索引

# Governed Runtime 验收证据索引

版本：v2.2
维护日期：2026-07-27
状态：当前机器证据

- `ne01_plan_delegate_route_acceptance_20260727.json`：当前权威七路线 v3
  收据；绑定干净提交 `3db60ff170a085056d7fd9ba792cb7064de14d35`、
  migration 0135 和同一真实文件型 SQLite，使七路
  `commit_bound_acceptance=PASS`，并使当前启用 Plan/Delegate 范围与既有
  Chat/Skill 一同达到 `COMPLETE`。Scheduler/Internal/Operator 和 NE-01
  不因该收据升级。
- `ne01_skill_policy_route_acceptance_20260727.json`：上一版七路线 v3 收据，
  仍是 Skill 当前启用范围的 Budget/Effect 历史绑定；已由上面的新收据取代
  “当前权威”角色。
- `ne01_universal_route_acceptance_20260727.json`：前一干净提交的七路线 v3 收据，只作历史校准；当前机器矩阵不得用它替代上面的最新收据。
- `gr02a_planning_search_shadow_benchmark_v1.json`：规划搜索影子基准与收益比较。
- `gr06_universal_governed_runtime_live_acceptance_20260715.json`：旧提交、旧 migration head 的 chat、skill、delegate、plan 四路线验收。

这些证据只证明各自声明的程序 Gate，不代表 Full Architecture 的全部目标已封板。
