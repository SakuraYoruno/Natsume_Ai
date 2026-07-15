受众：MASTER
标题：Flow 生效索引（active.pointer 对照，当前代码校准）

版本：v3.0
日期：2026-05-30

定位：维护 `flows/active.pointer` 的当前指针值，并说明它与现行代码运行面的关系。

---

## 当前状态

`flows/active.pointer` 当前仍是占位型环境指针：

| environment | active.pointer value | export status | notes |
|---|---|---|---|
| dev | `flows/exports/2026-02-28_dev_skeleton/flow.json` | placeholder | 当前代码不直接读取该导出物作为 `/v1/chat` 主运行时 |
| staging | `flows/exports/2026-02-28_staging_skeleton/flow.json` | placeholder | 同上 |
| prod | `flows/exports/2026-02-28_prod_skeleton/flow.json` | placeholder | 同上 |

对应的 `flows/active.pointer` 当前内容为：

```json
{
  "dev": "flows/exports/2026-02-28_dev_skeleton/flow.json",
  "staging": "flows/exports/2026-02-28_staging_skeleton/flow.json",
  "prod": "flows/exports/2026-02-28_prod_skeleton/flow.json"
}
```

---

## 当前代码运行面说明

当前代码里，`flows/` 目录仍是“可部署配置资产/占位索引”，但 `/v1/chat` 主链已经不是旧的导出 Flow 驱动口径。当前主聊天运行时实际入口是：

| 层 | 当前模块 | 说明 |
|---|---|---|
| HTTP 入口 | `host_brain/app/asgi.py` (`app = create_app()`) + `host_brain/app/app_factory.py` + `host_brain/app/router_registry.py` | 定义 `POST /v1/chat`，旧 `host_brain/app/fastapi_app.py` 已删除 |
| 聊天入口 | `host_brain/app/chat_routes.py` + `host_brain/app/chat_handler.py` | immune gate + 统一 SSE 入口 |
| Cortex 编排 | `host_brain/app/cortex_orchestrator.py` | 转交共享运行时 |
| 共享运行时 | `host_brain/app/agent_runtime_shared.py` | 召回、trace、后处理 |
| Executive 路由 | `host_brain/app/executive_runtime.py` | canonical runtime 的应用层 composition 入口 |
| Executive 执行 | `host_brain/app/executive_runtime.py` + `host_brain/executive_runtime/runtime.py` | chat / skill / delegate / plan 主执行 |
| 主链作者 | `host_brain/app/surface_reply_runtime.py` -> `host_brain/persona/surface_main_chain.py` | normal chat 文本最终作者为 SurfaceMainChain |

因此，当前正确口径是：

- `flows/active.pointer` 仍应维护一致性
- 但它当前不是 `/v1/chat` 运行主链的事实来源
- 旧的 `main_chain.py / SceneRouter` 说法不应再写进本索引的“现行运行面”描述

---

## 一致性规则

1. `flows/active.pointer` 必须是单个合法 JSON，且只包含 `dev / staging / prod` 三个键。
2. 本索引表中的三条路径必须与 `flows/active.pointer` 完全一致。
3. 若未来恢复真正的 Flow 导出物驱动，需要同时更新：
   - `flows/active.pointer`
   - 本索引
   - 对应当前代码事实文档

---

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-02-28 | 初版，占位 skeleton |
| v1.0 | 2026-03-19 | 补充 bootstrap 说明 |
| v2.0 | 2026-04-19 | 去除对 `main_chain.py / SceneRouter` 的现行描述，改为当前代码校准版 |
