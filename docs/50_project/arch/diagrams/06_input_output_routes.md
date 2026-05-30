受众：MASTER
标题：图 06 — 输入回流与出站（Gateway / `/v1/host/input` / OutboundDispatcher）

版本：v1.0
日期：2026-05-30
归属：docs/50_project/arch/diagrams/

---

## 怎么读这张图

外部消息进入和发出的核心是 **Unified Message Gateway**：

| 方向 | 链路 |
|---|---|
| **入站** | 平台 webhook → Gateway → `/v1/host/input` → Host 主链 |
| **出站** | Host 主链 → `post_gateway_reply` → `/v1/gateway/reply` → OutboundDispatcher → 平台适配器 |
| **Edge 事件** | Edge Runtime → `/v1/edge/events`（**不是** `/v1/host/input`） |

## 入站链路（消息进入主链）

```mermaid
flowchart TD
    P1["WeChat / QQ / Feishu / WeCom / Telegram / Line"] -->|webhook| GW

    subgraph Gateway["Unified Message Gateway (edge_client/gateway/)"]
        SVR["server.py<br/>HTTP 入口"]
        NORM["normalizer.py<br/>归一化平台 payload"]
        ROUTER["router.py<br/>GatewayRouter"]
        HUM["humanize_sender.py"]
        SVR --> NORM --> ROUTER
    end

    GW --> ROUTER
    ROUTER -->|"POST /v1/host/input"| INR

    subgraph HostInput["Host /v1/host/input (host_brain/app/input_routes.py)"]
        INR["InputPayload<br/>(line 100)"]
        ACK["即时 ACK / 质量分析 / 偏航检测"]
        DISP["dispatch worker<br/>(线程池, _run_dispatch_worker_in_thread)"]
        DEDUP["gateway_dedupe<br/>(TTL 防重)"]
        INR --> DEDUP --> ACK --> DISP
    end

    DISP --> THA["thalamus_agent"]
    THA --> COR["cortex_orchestrator"]
    COR --> ERT["executive_runtime_core"]
    ERT --> CA["chat_agent"]
    CA --> SC["surface_main_chain"]
    SC --> POST["async_post_process"]
```

## 出站链路（回复回到平台）

```mermaid
flowchart LR
    POST["async_post_process"] --> PGR["post_gateway_reply<br/>(host 端)"]
    PGR -->|"POST /v1/gateway/reply"| OD

    subgraph OutboundLayer["edge_client/gateway/outbound.py"]
        OD["OutboundDispatcher"]
        OD --> AD1["WeChat Adapter"]
        OD --> AD2["QQ Adapter"]
        OD --> AD3["Feishu Adapter"]
        OD --> AD4["WeCom Adapter"]
        OD --> AD5["Telegram Adapter"]
        OD --> AD6["Line Adapter"]
    end

    AD1 -->|平台 API| WX["WeChat 用户"]
    AD2 -->|平台 API| QQUSER["QQ 用户"]
    AD3 -->|平台 API| FSUSER["Feishu 用户"]
    AD4 -->|平台 API| WCUSER["WeCom 用户"]
    AD5 -->|平台 API| TGUSER["Telegram 用户"]
    AD6 -->|平台 API| LNUSER["Line 用户"]
```

## Edge 事件链路（与 input 不混）

```mermaid
flowchart LR
    ER["Edge Runtime<br/>(edge_client)"] --> EE["POST /v1/edge/events"]

    subgraph HostEdgeRoutes["Host /v1/edge/events"]
        EVT_PLAY["播放状态<br/>(speak progress)"]
        EVT_GATE["资源门控<br/>(barge_in / lock)"]
        EVT_WAIT["inline waiters<br/>(awaitable callbacks)"]
    end

    EE --> EVT_PLAY
    EE --> EVT_GATE
    EE --> EVT_WAIT
```

## 三种入站口径对比

| 入站方式 | HTTP 路由 | 用途 |
|---|---|---|
| **直接 chat** | `POST /v1/chat` | 前端直连聊天主链 |
| **Gateway 平台消息** | `POST /v1/host/input` | 微信 / QQ / 飞书等平台消息归一化进入 |
| **Edge 运行时事件** | `POST /v1/edge/events` | 播放状态、资源门控、inline waiter 等 |

## 代码锚点

| 链路 | 文件 | 行 |
|---|---|---:|
| Gateway 入口 | `edge_client/gateway/server.py` | — |
| Gateway 归一化 | `edge_client/gateway/normalizer.py` | — |
| Gateway 路由 | `edge_client/gateway/router.py` | — |
| Gateway 出站 | `edge_client/gateway/outbound.py` | — |
| 平台适配器 | `edge_client/gateway/adapters/` | — |
| Host 入站 | `host_brain/app/input_routes.py` | 100（InputPayload） |
| Host 入站 worker | 同上 | 192（`_run_dispatch_worker_in_thread`） |
| Host 入站 dedupe | 同上 | 239（`_gateway_dedupe_ttl_s`） |

## 不变量

1. **Gateway 不再是旧 chat 主链里的一个场景分支**；它是独立运行面，平台输入/输出总线。
2. 入站消息**不能**再走旧的 `SCENE_GATEWAY → SceneRouter` 结构；当前是 `Gateway.router → /v1/host/input → 主链`。
3. `/v1/host/input` 已承担 ACK / 进度推送 / 重对齐通知等控制逻辑，不再只是"转发壳"。
4. **Edge 运行时事件**（播放状态 / 资源门控 / inline waiter）走 `/v1/edge/events`，与 `/v1/host/input` 严格分离。
5. Outbound 由 `OutboundDispatcher` 统一，平台 adapter 隔离。

## 已废弃路径

| 旧路径 | 当前事实 |
|---|---|
| `SCENE_GATEWAY` 场景类型 | ❌ 已删除 |
| Gateway 消息走 `SceneRouter` | ❌ 已不再使用 |
| `/v1/host/input` 是"转发壳" | ❌ 已升级为含 ACK / 质量分析 / 偏航检测 |
| Edge 事件走 `/v1/host/input` | ❌ 已迁移到 `/v1/edge/events` |
