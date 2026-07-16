受众：PROJECT
标题：图 01——当前运行拓扑

# 图 01：当前运行拓扑

版本：v2.6
核对日期：2026-07-16

## 拓扑

```mermaid
flowchart LR
    USER["用户 / API Client"]
    OP["Operator"]
    PLATFORM["消息平台"]
    MODEL["模型 Provider"]

    subgraph HostProc["Host Main process"]
        HOST["host_brain.app.asgi:app\n默认 127.0.0.1:18080"]
        DOMAIN["Governed Runtime + Domain State"]
        HOST --> DOMAIN
    end

    subgraph ObsProc["Observability process"]
        OBS["host_brain.app.obs_fastapi_app:app\n默认 127.0.0.1:18082"]
    end

    subgraph EdgeProc["Edge process"]
        EDGE["Edge RPC\n默认 127.0.0.1:17800"]
        GW["Unified Gateway\n默认 19000，按配置启用"]
    end

    subgraph Presentation["Presentation"]
        MON["Monitor UI"]
        PET["Desktop Pet / Avatar"]
    end

    subgraph OptionalInfra["Optional external services"]
        NEO["Neo4j"]
        CHR["Chroma"]
        REDIS["Redis"]
    end

    USER -->|"POST /v1/chat"| HOST
    PLATFORM -->|"webhook"| GW
    GW -->|"Host input contract"| HOST
    HOST -->|"durable reply/effect"| GW
    HOST -->|"signed RPC"| EDGE
    EDGE -->|"effect receipt / event"| HOST
    DOMAIN -->|"ModelAttempt"| MODEL
    DOMAIN --> NEO
    DOMAIN --> CHR
    DOMAIN --> REDIS
    OBS -->|"read-only durable projection"| DOMAIN
    OP --> MON --> OBS
    MON -.->|"Operator BFF: task_graph.cancel\nexact revision；默认 OFF"| HOST
    EDGE --> PET
    HOST -."static/compat hosting".-> MON
```

## 运行面边界

| 运行面 | Authority | 依赖失效时的语义 |
|---|---|---|
| Host | Command、Task、Life、Memory 等业务协调 authority | 核心合同失败应拒绝或显式降级 |
| Observability | Query/Projection，不拥有业务写入 | 观测不可用不能偷偷改变业务状态 |
| Gateway | Transport inbox/outbox 与平台 delivery | 平台失败保留可恢复投递状态 |
| Edge | 设备执行与本机 receipt | 设备不可用返回 `DEGRADED/UNAVAILABLE` |
| UI/Pet | 人机界面和表现 | 不得成为任务、Persona 或 Life 真相 |
| Neo4j/Chroma/Redis | 可选图、向量或运行辅助 | 不得因可选服务可用性改变 canonical writer |

Gateway 通常在 Edge lifecycle 中启动，不应简单按“固定独立进程数量”理解部署。生产可以拆分进程，但 authority 边界不随部署方式改变。

## 代码锚点

- Host CLI：`host_brain/__main__.py`
- Host ASGI composition：`host_brain/app/asgi.py`、`host_brain/app/app_factory.py`、`host_brain/app/router_registry.py`
- Observability：`host_brain/obs_main.py`、`host_brain/app/obs_fastapi_app.py`
- Edge RPC：`edge_client/__main__.py`、`edge_client/app/fastapi_app.py`
- Gateway：`edge_client/runtime/bootstrap.py`、`edge_client/gateway/bootstrap.py`、`edge_client/gateway/server.py`
- 跨进程合同：`natsume_contracts/`
- 可选服务：`scripts/runtime/docker-compose.external.yml`

## 不能从本图推出的结论

- 图上有连线不等于全部 NE-01 已完成。Durable DAG 是 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：0091 success、0092 cancellation、0093 delivery、0094 cancellation continuation、0095 revisioned identity、0096 Review continuation 与 0097 grant uniqueness 已有证据。Cancellation Operator authority/入口为 `WIRED_DEFAULT_OFF`：Host/Internal/Scheduler 共享 Secure Ingress verifier，Gateway/BFF/UI 精确提交 revision；开启时要求 Secure Ingress `ENFORCE` 且 Gateway 可用，否则 Host 启动 fail closed。0096 已接通 task-bound Review 自动续跑，但它未解决 0092 operation/effect cancellation reconciliation。业务对账、历史修复、Budget 高竞争、部署凭据与真实 canary仍缺，atomic/bounded/cancellation 均 OFF。
- 可选服务启动成功不等于 Recall、Bond 或 rate limit 已生产启用。
- Edge/Gateway 存在不等于真实平台账号、硬件或密钥已验收。
