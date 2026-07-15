受众：PROJECT
标题：Natsume AI 仓库总览

# Natsume AI

版本：v8.0 Living
代码对齐日期：2026-07-15

Natsume AI 是本地优先、用户主权优先的个人数字生命系统。仓库同时包含 Host、Observability、Gateway、Edge、Monitor UI、Desktop Pet 与领域能力；这些运行面通过版本化合同协作，不应形成平行的身份、任务或长期状态真相。

本 README 只提供入口和导航。不要从这里推导架构完成度：

- 当前代码事实：[CURRENT_CODE_ARCHITECTURE.md](docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- 稳定目标：[TARGET_ARCHITECTURE.md](docs/50_project/arch/TARGET_ARCHITECTURE.md)
- 当前差距：[ARCHITECTURE_GAP_MATRIX.md](docs/50_project/arch/ARCHITECTURE_GAP_MATRIX.md)
- 正式执行：[NEXT_GENERATION_EVOLUTION_EXECUTION.md](docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)

## 运行面

| 运行面 | 入口 | 职责 |
|---|---|---|
| Host | `host_brain/__main__.py` → `host_brain.app.asgi:app` | 对话、治理、Task/Agent Runtime、Life、Memory 与业务 API |
| Observability | `host_brain/obs_main.py` → `host_brain.app.obs_fastapi_app:app` | 只读投影、健康、回放与 Operator BFF |
| Monitor UI | `host_brain/app/monitor_ui/` | Operator Web 界面；导航以源码 manifest 为准 |
| Edge RPC | `edge_client/__main__.py` | ASR、TTS、Avatar、Desktop 与设备 actuator |
| Unified Gateway | `edge_client/gateway/bootstrap.py` | 平台 webhook、Host 输入回流与回复投递 |
| Desktop Pet | `desktop_pet/main.cjs` | Electron + Live2D 本地呈现 |

普通 Host router 由 `host_brain/app/router_registry.py` 装配；Chat 和 Edge event 等特殊入口由 `host_brain/app/app_factory.py` 单独注册。具体 route 列表以运行时 OpenAPI 和代码为准。

## 代码地图

| 路径 | 说明 |
|---|---|
| `host_brain/app/` | Host/Obs HTTP adapter、ASGI lifecycle 与 Monitor UI |
| `host_brain/orchestration/` | HostCommand admission、计划编译、revision 与 node grant |
| `host_brain/task_runtime/` | Durable DAG、Attempt、Lease、Budget、Review、Verification、Acceptance |
| `host_brain/planning_search/` | 领域无关搜索合同和算法；不拥有真实副作用 |
| `host_brain/digital_life/`、`host_brain/persona/` | Life 状态与最终表达 |
| `host_brain/memory/`、`host_brain/persistence/` | Memory、Canon、repositories 与 migrations |
| `host_brain/capability_plane/`、`host_brain/skills/` | Capability Broker、内置能力与供应链资产 |
| `host_brain/pentest/` | 授权安全实验室及其 PlanningSearch adapter |
| `edge_client/` | Edge RPC、Gateway、平台和设备 adapter |
| `natsume_contracts/` | Host/Edge 共用的版本化跨进程合同 |
| `desktop_pet/` | Electron + Live2D 桌面呈现 |

## 主链边界

Host `chat/skill/delegate/plan` 的当前主链以 `CURRENT_CODE_ARCHITECTURE.md` 为准，核心 lineage 为：

```text
Secure Ingress
  → Governor
  → HostCommand + WorkOrderV3
  → immutable TaskGraph
  → Attempt / Budget / Review / node-bound grant
  → Verification / Acceptance
  → durable effect intents / receipts
```

Pentest 的授权、阶段、工具和规划边界见：

- [Pentest 能力边界](host_brain/pentest/CAPABILITY.md)
- [Pentest 专项架构](docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md)

任何 synthetic benchmark、shadow 路径或历史 receipt 都不能单独证明生产收益或完整架构完成。

## 快速启动

```powershell
# Host
python -m host_brain --port 18080

# Observability
python -m host_brain.obs_main --port 18082

# Edge RPC
python -m edge_client --port 17800

# Monitor proxy
powershell -ExecutionPolicy Bypass -File scripts/runtime/start_monitor_proxy.ps1

# Desktop Pet
Set-Location desktop_pet
npm install
npm start
```

Gateway 通常由 Edge bootstrap 装配；启用平台和连接参数以 `edge_client/config.py`、adapter registry 与环境配置为准。

## 仓库规则

- Markdown 首两行必须是 `受众：MASTER|CODEX|PROJECT` 和 `标题：...`。
- 运行数据写入被忽略的 `runtime/`，临时验收产物写入被忽略的 `artifacts/`。
- 顶层不恢复 `data/`、`flows/`、`skills/`、`integrations/` 杂物目录。
- 内置 Skill 位于 `host_brain/skills/`；外部 bundle 必须经过受治理供应链。
- 跨进程合同位于 `natsume_contracts/`，不是可删除的兼容垃圾目录。
- 提交信息、PR 标题和正文统一使用中文。

## 文档入口

- [文档地图](docs/00_index/00_DOCMAP.md)
- [产品需求](docs/20_prd/PRD.md)
- [工程合同](docs/30_devspec/DevSpec.md)
- [当前追溯矩阵](docs/60_trace/requirements_trace.md)
- [2026-07 历史架构程序](docs/_history/programs/2026-07/README.md)

历史文档中的 `DONE/PASS` 只对其固定提交、环境和声明范围成立。
