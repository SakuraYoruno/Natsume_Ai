受众：PROJECT
标题：Governed Runtime 验收证据索引

# Governed Runtime 验收证据索引

版本：v2.4
维护日期：2026-07-28
状态：当前机器证据

- `ne01_universal_route_acceptance_0142_20260728.json`：当前权威七路线
  v3 收据；绑定干净实现提交
  `08a172da662df11d34fb42ea6e25a651f84da141` 与 migration 0142。既有
  accepted SQLite snapshot 通过 backup 复制并从 0141 只追加 0142 后，以
  query-only reader 重开；七路均为 `PASS`，receipt digest 为
  `sha256:161b860fd67d47945c44ff403304742c5619bc6a590cd76dd12b119e1e29f15f`，
  没有重跑 accepted worker。
- `ne01_universal_route_acceptance_0141_20260728.json`：此前绑定
  `6f78e25c24a2bc148aa8a0a12016558a6e492a7a` 的历史七路线 v3 收据，已由
  0142 收据取代当前权威角色。
- `ne01_universal_route_acceptance_0140_20260728.json`：此前绑定
  `7e9b090b3fcaf6e8a6d8cf9b2e0ab4ae702b08b8` 的历史七路线 v3 收据，已由
  0142 收据取代当前权威角色。
- `ne01_universal_route_acceptance_0137_20260728.json`：此前绑定
  `708aa845ef68b9f7df931b2aba8c204cf6e9946a` 的七路线 v3 收据。该提交在
  PR 合并后不再是 `dev` 祖先，因此只保留为历史校准，不能满足当前
  coverage Gate。
- `ne01_plan_delegate_route_acceptance_20260727.json`：0135 阶段的历史
  七路线 v3 收据；已由 0142 收据取代当前权威角色。
- `ne01_skill_policy_route_acceptance_20260727.json`：上一版七路线 v3 收据，
  仍是 Skill 当前启用范围的 Budget/Effect 历史绑定；已由 0142 收据取代
  “当前权威”角色。
- `ne01_universal_route_acceptance_20260727.json`：前一干净提交的七路线 v3 收据，只作历史校准；当前机器矩阵不得用它替代上面的最新收据。
- `gr02a_planning_search_shadow_benchmark_v1.json`：规划搜索影子基准与收益比较。
- `gr06_universal_governed_runtime_live_acceptance_20260715.json`：旧提交、旧 migration head 的 chat、skill、delegate、plan 四路线验收。

这些证据只证明各自声明的程序 Gate，不代表 Full Architecture 的全部目标已封板。
