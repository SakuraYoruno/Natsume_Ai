受众：MASTER
标题：Flow 生效索引（active.pointer 对照）

版本：v0.1
日期：2026-02-28

定位：维护各环境当前生效的 Flow 导出资产，并与 `flows/active.pointer` 保持一一对应。

| environment | active.pointer value | export status | notes |
|---|---|---|---|
| dev | flows/exports/2026-02-28_dev_skeleton/flow.json | active | bootstrap placeholder |
| staging | flows/exports/2026-02-28_staging_skeleton/flow.json | active | bootstrap placeholder |
| prod | flows/exports/2026-02-28_prod_skeleton/flow.json | active | bootstrap placeholder |

一致性规则：
1. `flows/active.pointer` 必须是单个合法 JSON，且仅包含 `dev/staging/prod` 三个键。
2. 本索引表中每个环境的路径必须与 `active.pointer` 的值完全一致。
3. 任何环境切换都需要同时更新 `active.pointer` 与本索引。
