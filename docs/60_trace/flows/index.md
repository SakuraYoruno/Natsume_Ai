受众：MASTER
标题：Flow 生效索引（active.pointer 对照）

版本：v1.0
日期：2026-03-19

定位：维护各环境当前生效的 Flow 导出资产，并与 `flows/active.pointer` 保持一一对应。

> **2026-03-19 状态说明**：当前 Flow 导出体系处于 bootstrap 阶段，尚未建立正式的多环境 Flow 切换流程。
> 实际运行时的图编排由 `host_brain/integrations/graph_runtime.py` 直接管理，不依赖 `flows/` 目录下的导出资产。
> 本目录保留作为未来多环境 Flow 版本管理的预留结构。

| environment | active.pointer value | export status | notes |
|---|---|---|---|
| dev | flows/exports/2026-02-28_dev_skeleton/flow.json | placeholder | 尚未建立正式导出，当前以代码直接运行 |
| staging | flows/exports/2026-02-28_staging_skeleton/flow.json | placeholder | 同上 |
| prod | flows/exports/2026-02-28_prod_skeleton/flow.json | placeholder | 同上 |

## 当前图编排实际入口

P15 封板后，图编排的实际代码入口为：

| 模块 | 路径 | 说明 |
|------|------|------|
| 图运行时门面 | `host_brain/integrations/graph_runtime.py` | 统一图执行入口 |
| 图执行器 | `host_brain/integrations/graph_executor.py` | 节点分派与 Agent 路由 |
| 主链路编排 | `host_brain/app/graph/main_chain.py` | SceneRouter + 场景分支 |
| 图配置构建 | `host_brain/app/graph_config_builder.py` | 按执行画像构建图 |

## 一致性规则

1. `flows/active.pointer` 必须是单个合法 JSON，且仅包含 `dev/staging/prod` 三个键。
2. 本索引表中每个环境的路径必须与 `active.pointer` 的值完全一致。
3. 任何环境切换都需要同时更新 `active.pointer` 与本索引。

## 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-02-28 | 初版，bootstrap placeholder |
| v1.0 | 2026-03-19 | 补充当前状态说明，标注 flows/ 目录为预留结构，新增实际图编排入口对照表 |
