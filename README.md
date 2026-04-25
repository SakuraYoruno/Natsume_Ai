# Natsume AI

Natsume AI 是一个围绕“数字生命体”构建的运行时仓库。当前代码已经形成 5 个清晰的运行面：`Host Brain` 主应用、独立 `Observability` 后端、`Edge RPC`、`Unified Message Gateway`，以及基于 React 的 `Monitor UI`。

这份 README 只保留代码事实和入口导航。更细的设计说明请直接进入文档区，不再把大量阶段性方案和历史结构塞在首页。

## 当前代码现状

- Host 主应用入口：`host_brain/__main__.py`，默认端口 `8000`
- Observability 后端入口：`host_brain/obs_main.py`，默认端口 `18082`
- Edge RPC 入口：`edge_client/__main__.py`，默认端口 `17800`
- Unified Message Gateway 入口：`edge_client/gateway/bootstrap.py`，默认端口 `19000`
- Gateway 当前适配器：`wechat`、`qq`、`feishu`、`wecom`、`telegram`、`line`
- Tribe 注册表当前定义 `23` 个 family spec，见 `host_brain/tribe/registry.py`
- Monitor UI 源码位于 `host_brain/app/monitor_ui/`，构建产物位于 `host_brain/app/static/monitor/`

## 仓库结构

```text
Natsume_Ai/
├─ host_brain/                 # Host 主脑：聊天、治理、观测、调度、Tribe、记忆、工具
│  ├─ app/                     # FastAPI 应用、运行时、监控前端源码
│  ├─ tribe/                   # 23 个 tribe family 及事件/注册表
│  ├─ memory/ retrieval/ bond/ # 记忆、检索、关系系统
│  ├─ observability/           # 观测聚合与指标
│  └─ scheduler/               # 调度器
├─ edge_client/                # Edge RPC、语音执行、网关、平台适配器
├─ configs/                    # 模型、部署、发布、密钥模板
├─ docs/                       # 白皮书 / PRD / DevSpec / Project / Trace
├─ scripts/runtime/            # Host 侧启动、停止、运维脚本
├─ tests/                      # unit / integration / fixtures
└─ tools/                      # 诊断、回放、静态扫描、文档门禁
```

## 主要代码入口

- 聊天入口：`POST /v1/chat`
  - `host_brain/app/asgi.py`
  - `host_brain/app/app_factory.py`
  - `host_brain/app/chat_routes.py`
  - `host_brain/app/chat_handler.py`
  - `host_brain/app/cortex_orchestrator.py`
  - `host_brain/app/executive_router.py`
  - `host_brain/app/executive_runtime.py`
  - `host_brain/app/executive_runtime_core.py`
  - `host_brain/app/chat_agent.py`
- Edge 事件回流：`POST /v1/edge/events`
- 外部输入回流：`POST /v1/host/input`（当前接收 ASR / Gateway 归一化输入；其他 Edge 运行时事件走 `POST /v1/edge/events`）
- Edge 能力入口：`/v1/edge/health`、`/v1/edge/speak`、`/v1/edge/barge_in`、`/v1/edge/vts`、`/v1/edge/fallback/*`、`/v1/edge/screenshot`、`/v1/edge/automate`、`/v1/edge/visual_locate`、`/v1/edge/proactive_push`、`/v1/edge/proactive_messages`、`/v1/edge/wx/*`
- 监控与观测：`/obs/*`、`/api/ui/*`、`/panel`、`/monitor/*`

## Monitor UI 当前页面

Monitor UI 现在已经不是单一的“监控页”，而是围绕数字生命体拆成 4 组导航：

- 数字生命体：`总览`、`生命时间轴`、`病灶中心`、`情绪`、`关系`、`记忆`、`存在态`、`内在世界`、`回复策略`、`自我模型`
- 运营：`提示词`、`人格文案`、`系统底座`、`设定管理`
- 器官级观测：`生命体征`、`神经会话`、`神经链路`、`代谢指标`、`皮层洞察`、`神经调试`、`免疫告警`、`生理日志`
- 外周执行：`感官网关`、`行为技能`、`执行工具`、`节律调度`、`反射回路`

对应源码入口见：

- `host_brain/app/monitor_ui/src/App.jsx`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`
- `host_brain/app/monitor_ui/src/routes/routeLoaders.js`

## 推荐阅读顺序

1. [docs/00_index/00_DOCMAP.md](docs/00_index/00_DOCMAP.md)
2. [docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
3. [docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md](docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md)
4. [docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md](docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md)
5. [docs/50_project/arch/dataflow_report.md](docs/50_project/arch/dataflow_report.md)
6. [docs/60_trace/requirements_trace.md](docs/60_trace/requirements_trace.md)

如果旧文档与当前代码不一致，请优先以 `CURRENT_CODE_ARCHITECTURE.md`、`CORE_MODULES_DETAILED_REFERENCE.md` 和实际代码为准。

## 启动方式

Windows 环境优先使用仓库自带脚本：

- `scripts/runtime/start_host_side.ps1`
- `scripts/runtime/stop_host_side.ps1`

手动入口：

```powershell
python -m host_brain --startup-check
python -m host_brain.obs_main
python -m edge_client
```

## 技术栈

- Backend：Python、FastAPI
- Frontend：React 18、Vite、Tailwind CSS、ECharts、XYFlow、Zustand
- Storage：SQLite、Chroma、Neo4j
- Runtime pattern：SSE、Tribe event bus、Edge RPC、gateway adapters

## 说明

- `README.md` 现在只做仓库入口，不再承担完整架构白皮书角色
- 阶段性方案、历史计划和封板报告仍保留在 `docs/50_project/`、`docs/50_release/`、`docs/60_trace/`
- 监控前端最近有较大重构，涉及“生命时间轴 / 病灶中心 / 数字生命体导航”的页面变化时，请优先检查 `host_brain/app/monitor_ui/src/`
