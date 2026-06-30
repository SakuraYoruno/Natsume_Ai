受众：PROJECT
标题：Natsume AI 仓库总览

# Natsume AI

版本：v6.1 Living
代码对齐日期：2026-06-30

Natsume AI 是一个围绕“数字生命体”构建的本地运行时仓库。当前代码的核心不是单一聊天服务，而是 Host Brain、Observability、Edge RPC、Unified Message Gateway、Monitor UI、Desktop Pet、MateEngine/VRM 桥接等多个运行面协同。

本 README 只保留可从当前代码直接核验的入口、目录和导航。实现细节以 `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`、`docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` 和实际代码为准。

## 文档规则

- 所有 Markdown 文件必须以两行开头：`受众：MASTER|CODEX|PROJECT` 和 `标题：...`。
- `[LIVING]` 文档跟随代码变化；发生冲突时，以代码为事实源。
- 架构事实入口在 `docs/50_project/arch/`，历史计划和阶段复盘只作为追溯资料。

## 当前运行面

| 运行面 | 入口 | 端口/配置 | 当前事实 |
|---|---|---|---|
| Host Brain | `host_brain/__main__.py` -> `host_brain.app.asgi:app` | `HOST_APP_PORT`，CLI fallback `8000`；示例环境常设 `18080` | 主 API、聊天、工具、记忆、数字生命、Pentest 路由 |
| Observability | `host_brain/obs_main.py` -> `host_brain.app.obs_fastapi_app:app` | `HOST_OBS_APP_PORT`，fallback `18082` | 独立 FastAPI app，承接 `/obs/*`、`/api/ui/*` 等监控流量 |
| Monitor Proxy | `scripts/runtime/start_monitor_proxy.ps1` | `HOST_MONITOR_PROXY_PORT`，fallback `18180` | Nginx 代理 `/monitor/` 静态资产、Host upstream 和 Obs upstream |
| Edge RPC | `edge_client/__main__.py` | `EDGE_RPC_PORT`，fallback `17800` | Edge 侧 ASR/TTS/avatar/resource gate 等 RPC 能力 |
| Unified Gateway | `edge_client/gateway/bootstrap.py` + `edge_client/gateway/server.py` | `EDGE_GATEWAY_PORT`，fallback `19000` | 平台 webhook、统一输入回流、平台回复分发 |
| Desktop Pet | `desktop_pet/main.cjs` | `NATSUME_DESKTOP_PET_PORT`，fallback `18788` | Electron + Live2D 本地桌宠，提供 `/health` 与 `/action` |
| MateEngine/VRM | `host_brain/channels/mateengine.py`、`edge_client/config.py` | MateEngine UDP `32145`，VRM bridge `18770` | Host 侧动作/说话桥接，Edge 侧 VRM bridge 配置 |

## 代码主干

| 路径 | 当前规模 | 说明 |
|---|---:|---|
| `host_brain/app/` | 228 Python 文件 | Host FastAPI、主链、路由、Obs app、Monitor UI 后端接口 |
| `host_brain/persona/` | 75 Python 文件 | 人格、内心世界、回复规划、Surface 主链与评分 |
| `host_brain/persona/surface/` | 17 Python 文件 | Surface 数据模型、输入校验、prompt、候选评分、scoring 子包 |
| `host_brain/pentest/` | 124 Python 文件 | 授权渗透测试代理、工具执行、安全边界、报告和自主 agent |
| `host_brain/runtime/` | 6 Python 文件 | 数字生命后台 tick、连续性、器官状态、自主发起 |
| `host_brain/memory/` | 24 Python 文件 | LifeMemoryStore、召回融合、Canon、上下文压缩 |
| `host_brain/tribe/` | 34 Python 文件 | 23 个 family spec、25 个默认 agent descriptor、事件总线 |
| `host_brain/governor/` | 10 Python 文件 | 决策、规则、tier policy、rate limit、token budget |
| `host_brain/skills/impl/` | 25 executor 文件 | 普通 skill executor；Pentest 不走这里注册 |
| `edge_client/` | 80 Python 文件 | Edge RPC、Gateway、平台适配器、Edge tribe bridge |
| `desktop_pet/` | Electron 项目 | Live2D 桌宠本地窗口与 HTTP 控制面 |

## 关键入口

### Chat 主链

```text
POST /v1/chat
  -> host_brain/app/app_factory.py 注册 endpoint
  -> host_brain/app/chat_routes.py::handle_v1_chat_route()
  -> host_brain/app/chat_handler.py::handle_chat()
  -> host_brain/app/agent_runtime_shared.py
  -> host_brain/app/executive_runtime_core.py
  -> host_brain/app/surface_main_chain_adapter.py
  -> host_brain/persona/surface_main_chain.py
```

### Host 路由事实

`host_brain/app/router_registry.py` 当前注册：`/canon`、`/scheduler`、`/host-scheduler/*`、`/tools`、`/prompt-config`、`/bond`、`/v1/agent`、`/v1/host/input`、`/internal/*`、`/v1/digital-life`（含 `/proactive/*`）、`/v1/memory`、`/v1/mateengine`、`/v1/pentest`、`/health`、`/ready` 等。Observability 路由（`/obs/*`、`/api/ui/*`）由独立 `obs_fastapi_app.py` 装配，含 `/obs/proactive-autonomy` 快照端点。

`/v1/chat` 和 `/v1/edge/events` 由 `host_brain/app/app_factory.py` 单独注册，不是 `router_registry.py` 中的普通 router include。

### Monitor UI

React 源码位于 `host_brain/app/monitor_ui/src/`。当前 `routeManifest.jsx` 注册 37 条路由，其中 35 条进入导航，分为 Cockpit、Life Systems、Observability、Operations、Execution、Security Lab、Integrations 七组。构建产物位于 `host_brain/app/static/monitor/`，由 monitor proxy 脚本通过 `/monitor/` 对外提供。

### Pentest

Pentest 使用独立调度链路，不在 `host_brain/skills/impl/` 普通 skill executor 中实现。当前事实：

- ToolSpec 真源：`host_brain/pentest/tools/tool_specs.py`（88 个 ToolSpec）
- Executor 真源：`host_brain/pentest/tools/tool_executors.py`（92 个 executor，6,029 行）
- 88 个 ToolSpec，92 个 executor，37 个 side-effect ToolSpec，0 个缺 executor
- Kali bridge 有 17 个 wrapper class
- 规划器：MCTS（`mcts_planner.py`）+ ReAct（`llm_react_planner.py`）+ Cognitive（`agent/cognitive_engine.py`）三条路径
- Agent 子包：`pentest/agent/` 31 文件（8,906 行），含认知引擎、信念模型、目标模型、演化核心
- Adversarial：`pentest/adversarial/` 9 文件 — 红蓝对抗 game loop + 策略协同进化
- Swarm：`pentest/swarm/` 8 文件 — 多 agent 协调器 + 消息总线 + 图合并
- Online：`pentest/online/` 5 文件 — 安全门控、授权管理、速率治理
- 入口文档：`host_brain/pentest/CAPABILITY.md`（v2.3）
- 专项架构：`docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md`（v1.1）

## 快速启动

```powershell
# Host Brain
python -m host_brain --port 18080

# Observability
python -m host_brain.obs_main --port 18082

# Edge RPC
python -m edge_client --port 17800

# Monitor proxy
powershell -ExecutionPolicy Bypass -File scripts/runtime/start_monitor_proxy.ps1

# Desktop Pet
cd desktop_pet
npm install
npm start
```

Gateway 通常由 Edge bootstrap 组装运行；配置入口在 `edge_client/config.py`，平台范围由 `EDGE_GATEWAY_PLATFORMS` 控制，当前支持 `wechat`、`qq`、`feishu`、`wecom`、`telegram`、`line`。

## 主要文档

- 文档地图：[`docs/00_index/00_DOCMAP.md`](docs/00_index/00_DOCMAP.md)
- 当前代码架构：[`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- 核心模块参考：[`docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`](docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md)
- Pentest 专项架构：[`docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md`](docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md)
- Pentest 能力边界：[`host_brain/pentest/CAPABILITY.md`](host_brain/pentest/CAPABILITY.md)
- 数据流报告：[`docs/50_project/arch/dataflow_report.md`](docs/50_project/arch/dataflow_report.md)
- 工程契约：[`docs/30_devspec/DevSpec.md`](docs/30_devspec/DevSpec.md)
- 人格定义：[`Soul.md`](Soul.md)
