受众：MASTER
标题：CURRENT_CODE_ARCHITECTURE 对齐审计收口

# CURRENT_CODE_ARCHITECTURE 对齐审计收口

版本：v1.1
日期：2026-06-30
对象：`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`

## 1. 状态

旧审计报告针对的是 `CURRENT_CODE_ARCHITECTURE.md` v3.3。该版本已被 v4.1 全量替换，本文件不再记录旧章节级差异，只保留收口状态。

当前主文档：

- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` v4.1
- 代码对齐日期：2026-06-30
- 事实源：`host_brain/app/`、`host_brain/persona/`、`host_brain/pentest/`、`host_brain/tribe/`、`edge_client/`、`desktop_pet/`、`scripts/runtime/start_monitor_proxy.ps1`

## 2. 本轮已覆盖

- 运行面：Host Brain、Observability、Monitor Proxy、Edge RPC、Unified Gateway、Desktop Pet、MateEngine/VRM。
- Host 应用入口：`app_factory.py`、`router_registry.py`、`asgi.py`、`obs_fastapi_app.py`。
- Chat 主链：`/v1/chat` 到 executive runtime 与 Surface 主链适配。
- Persona/Surface：`host_brain/persona/`、`surface/`、`surface/scoring/` 当前规模与职责。
- Pentest：ToolSpec/Executor、路由、安全边界、agent/adversarial/swarm/online 子系统。
- Tribe：23 个 family spec、25 个默认 agent descriptor。
- Monitor UI：源码规模、route manifest、proxy/static 分发方式。

## 3. 后续不在本文件处理

- `dataflow_report.md` 的图和链路仍需单独审计。
- `DevSpec.md` 的接口契约仍需单独审计。
- 历史阶段文档不应直接当作当前架构事实源。
