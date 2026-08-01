受众：PROJECT
标题：图 01——当前运行拓扑

# 图 01：当前运行拓扑

版本：v2.9
核对日期：2026-08-01

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
        TERMINAL["0099 terminal inventory/reconcile\noperator + system auto 默认 OFF"]
        HOSTTERMQUERY["terminal candidates / cases / challenge\nmode=ro + query_only"]
        HOST --> DOMAIN --> TERMINAL
        HOST --> HOSTTERMQUERY
    end

    subgraph ObsProc["Observability process"]
        OBS["host_brain.app.obs_fastapi_app:app\n默认 127.0.0.1:18082"]
        TERMQUERY["terminal candidates / cases / challenge\nquery-only；obs:task-runtime:read"]
        OBS --> TERMQUERY
    end

    subgraph EdgeProc["Edge process"]
        EDGE["Edge RPC\n默认 127.0.0.1:17800"]
        GW["Unified Gateway\n默认 19000，按配置启用"]
        TTSPARENT["TTS actuator parent\n应用 Python"]
        TTSWORKER["standalone GPT-SoVITS Worker\n模型 Python 3.9 / CUDA"]
        EDGE --> TTSPARENT
        TTSPARENT -->|"JSON-line synth request"| TTSWORKER
        TTSWORKER -->|"NPZ audio"| TTSPARENT
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
    HOSTTERMQUERY -->|"physical query-only"| TERMINAL
    TERMQUERY -->|"mode=ro + query_only"| TERMINAL
    OP --> MON --> OBS
    MON -.->|"Operator BFF: task_graph.cancel\nexact revision；默认 OFF"| HOST
    MON -.->|"task_terminal.reconcile\nSecure Ingress → 0119 ref → TaskGraph/Review/Acceptance；默认 OFF"| HOST
    EDGE --> PET
    TTSPARENT -->|"audio playback"| PET
    HOST -."static/compat hosting".-> MON
```

## 运行面边界

| 运行面 | Authority | 依赖失效时的语义 |
|---|---|---|
| Host | Command、Task、Life、Memory 等业务协调 authority | 核心合同失败应拒绝或显式降级 |
| Observability | Query/Projection，不拥有业务写入 | 观测不可用不能偷偷改变业务状态 |
| Gateway | Transport inbox/outbox 与平台 delivery | 平台失败保留可恢复投递状态 |
| Edge | 设备执行与本机 receipt | 设备不可用返回 `DEGRADED/UNAVAILABLE` |
| GPT-SoVITS Worker | 只加载 project-bound voice manifest/权重/参考并合成音频，不拥有 Life 或设备 authority | 模型失败只降级 Voice 通道，不把七海情绪重置为中性 |
| UI/Pet | 人机界面和表现 | 不得成为任务、Persona 或 Life 真相 |
| Neo4j/Chroma/Redis | 可选图、向量或运行辅助 | 不得因可选服务可用性改变 canonical writer |

Gateway 可随完整 Edge lifecycle 启动，也可由 Host 侧 `run_gateway_side.py` 独立启动；后者用于远端 Edge
关闭时验证本机 durable transport。GPT-SoVITS Worker 是 TTS actuator 按需创建的独立模型子进程，应用层与
模型层只交换窄 IPC。生产可以拆分进程，但 authority 边界不随部署方式改变。

## 代码锚点

- Host CLI：`host_brain/__main__.py`
- Host ASGI composition：`host_brain/app/asgi.py`、`host_brain/app/app_factory.py`、`host_brain/app/router_registry.py`
- Observability：`host_brain/obs_main.py`、`host_brain/app/obs_fastapi_app.py`
- Edge RPC：`edge_client/__main__.py`、`edge_client/app/fastapi_app.py`
- TTS parent/worker：`edge_client/actuators/tts_engine.py`、`edge_client/actuators/tts_persistent_worker.py`
- Gateway：`edge_client/runtime/bootstrap.py`、`edge_client/gateway/bootstrap.py`、`edge_client/gateway/server.py`
- 跨进程合同：`natsume_contracts/`
- 可选服务：`scripts/runtime/docker-compose.external.yml`

## 不能从本图推出的结论

- 图上有连线不等于全部 NE-01 已完成。Durable DAG 是 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。0099 使用 immutable case/proof/supersession，readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并复核 Python codec；单次最多 inventory 10,000 个 Graph。只有唯一 graph-scope exact FAILED bundle 可 default-off auto；结构漂移只 quarantine。未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，仅 0099 UoW bypass；0090 唯一、0091/0092 完整校验。System proof 以 case 时间跨进程收敛；healthy closure 仅 signed `NONE` 零业务写。主 Host/独立 Obs 物理 query-only。全部 reconciliation 默认 OFF、canary 未跑；外部 inventory/key lifecycle 与 general recovery未知，NE-01 保持 `IN_PROGRESS`。
- Terminal challenge 已从 OperatorQueryGateway context 绑定 principal，并把 ref/key/time与确定性 Outbox编入 service/store 同一 final canonical bytes；authority key 漂移被拒绝，HTTP→真实 Ed25519→Store round-trip 已通过。该证据仍不证明外部 deployment key lifecycle 或 inventory remediation。
- 可选服务启动成功不等于 Recall、Bond 或 rate limit 已生产启用。
- Edge/Gateway 存在不等于真实平台账号、硬件或密钥已验收。
- 本机 CUDA Worker 合成通过不等于远端 Edge 已部署、音频已播放或 Avatar 已同步执行。
