受众：MASTER
标题：图 07 — Observability 拓扑（独立 ASGI / warm cache / Monitor UI）

版本：v1.0
日期：2026-05-30
归属：docs/50_project/arch/diagrams/

---

## 怎么读这张图

Observability 的核心特点：

1. **是独立 ASGI app**（不是 Host 的子路由），有独立进程和端口
2. **有 5 个 warm cache 服务**，提前拉好热点数据，提升 Monitor UI 响应
3. **Host Main 也保留 `/obs/*` 兼容挂载**，但完整观测以 Obs Backend 为准
4. **Monitor UI 是一个 Vite/React SPA**，由 Host 或 Obs 静态托管

## 总览

```mermaid
graph TB
    subgraph Host["🟦 Host Main (port 8000)"]
        HOST_OBS["/obs/* (兼容挂载)"]
        HOST_PANEL["/panel"]
    end

    subgraph Obs["🟦 Observability Backend (port 18082)"]
        OBS_APP["host_brain.app.obs_fastapi_app:app"]
        OBS_OBS["/obs/* (主路由)"]
        OBS_UI["/api/ui/* (Monitor UI 接口)"]

        subgraph Warm["5 个 warm cache 服务"]
            W1["runtime_snapshot"]
            W2["trace_feed"]
            W3["insights_summary"]
            W4["digital_life_overview"]
            W5["life_body"]
        end

        OBS_APP --> OBS_OBS
        OBS_APP --> OBS_UI
        OBS_APP -.lifespan startup.- Warm
    end

    subgraph MonUI["🟧 Monitor UI (Vite/React SPA)"]
        UI_FILES["host_brain/app/monitor_ui/<br/>index.html / src / public"]
    end

    USER1["👁️ Operator 浏览器"] -->|"主访问"| OBS_UI
    USER1 -->|"兼容访问"| HOST_OBS
    OBS_UI --> UI_FILES
    HOST_PANEL --> UI_FILES

    Warm -.提前拉数据.-> OBS_OBS
```

## obs_routes 模块清单

```mermaid
flowchart LR
    subgraph ObsRoutes["host_brain/app/obs_routes_*.py"]
        OR1["obs_routes_core.py<br/>核心路由聚合"]
        OR2["obs_routes_health.py<br/>健康检查"]
        OR3["obs_routes_metrics.py<br/>指标"]
        OR4["obs_routes_platform.py<br/>平台快照"]
        OR5["obs_routes_sessions.py<br/>会话快照"]
        OR6["obs_routes_stats.py<br/>统计"]
        OR7["obs_routes_life_body.py<br/>生命体观测"]
        OR8["obs_routes_digital_life_soak.py<br/>数字生命浸泡测"]
        OR9["obs_routes_codex_bio_ops.py<br/>codex bio ops"]
        OR10["obs_routes_security_immune.py<br/>安全免疫"]
    end

    OR1 -.聚合.-> OR2 & OR3 & OR4 & OR5 & OR6
    OR1 -.聚合.-> OR7 & OR8 & OR9 & OR10
```

## Monitor UI 信息架构（4 大板块）

```mermaid
graph TB
    UI["Monitor UI"]
    UI --> SEC1["1. 数字生命体<br/>(life timeline / 病灶中心 /<br/>器官状态 / 生命体导航)"]
    UI --> SEC2["2. 运营<br/>(会话 / 用户 / 平台 /<br/>消息流量)"]
    UI --> SEC3["3. 器官级观测<br/>(每个 family 的指标 /<br/>handoff / 失败率)"]
    UI --> SEC4["4. 外周执行<br/>(skill / tool 调用 /<br/>Edge 动作执行)"]
```

## 启动顺序（lifespan）

```mermaid
sequenceDiagram
    participant U as uvicorn
    participant App as obs_fastapi_app:app
    participant W1 as runtime_snapshot
    participant W2 as trace_feed
    participant W3 as insights_summary
    participant W4 as digital_life_overview
    participant W5 as life_body

    U->>App: startup
    App->>W1: start_runtime_snapshot_warm_cache()
    App->>W2: start_trace_feed_warm_cache()
    App->>W3: start_insights_summary_warm_cache()
    App->>W4: start_digital_life_overview_warm_cache()
    App->>W5: start_life_body_warm_cache()
    App-->>U: ready (port 18082)

    Note over App: 服务运行中

    U->>App: shutdown
    App->>W5: stop_life_body_warm_cache()
    App->>W4: stop_digital_life_overview_warm_cache()
    App->>W3: stop_insights_summary_warm_cache()
    App->>W2: stop_trace_feed_warm_cache()
    App->>W1: stop_runtime_snapshot_warm_cache()
    App-->>U: closed
```

## 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOST_OBS_APP_HOST` | `127.0.0.1` | Obs Backend 监听 host |
| `HOST_OBS_APP_PORT` | `18082` | Obs Backend 端口 |
| `HOST_RUNTIME_ENV_FILE` | — | runtime env 配置文件路径 |
| `HOST_SYS_MODE` | `normal` | sys_mode（locked / maintenance / normal） |
| `HOST_GOVERNOR_EXECUTOR_WORKERS` | `4` | governor executor 线程数（2-12） |

## 代码锚点

| 内容 | 文件 |
|---|---|
| Obs 主入口 | `host_brain/obs_main.py` |
| Obs ASGI app | `host_brain/app/obs_fastapi_app.py`（**单文件保留**） |
| obs_routes 模块组 | `host_brain/app/obs_routes_*.py` |
| Monitor UI 工程 | `host_brain/app/monitor_ui/` |
| warm cache 实现 | `host_brain/observability/*_warm_cache.py` |

## 不变量

1. Host Main 与 Observability 是**两个独立 ASGI app**，独立端口、独立进程。
2. **`obs_fastapi_app.py` 是单文件保留**（与 Host 的三件套不同），因为 Obs 装配相对简单。
3. Host Main 的 `/obs/*` 是**兼容路径**；正式观测应走 Obs Backend (`:18082`)。
4. 5 个 warm cache 在 lifespan startup 时启动，shutdown 时反序停止。
5. Monitor UI 是 Vite/React SPA，构建产物由 Host 或 Obs 静态托管，**不是后端服务**。

## 已废弃路径

| 旧路径 | 当前事实 |
|---|---|
| Obs 是 Host 主进程的附属接口 | ❌ 已升级为独立后端 |
| 单一 `/obs` 路由模块 | ❌ 已拆为 10 个 obs_routes_* |
| Monitor UI 只是"调试控制台" | ❌ 已升级为"数字生命驾驶舱" |
