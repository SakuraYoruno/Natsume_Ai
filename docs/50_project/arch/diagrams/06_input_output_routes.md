受众：PROJECT
标题：图 06——Gateway 与 Edge 输入输出

# 图 06：Gateway 与 Edge 输入输出

版本：v2.3
核对日期：2026-07-30

Gateway 处理“平台消息”，Edge RPC 处理“本机感知与动作”。两者可以在同一 Edge lifecycle 中运行，但 transport authority 和 effect authority 不应混为一体。

## 平台入站

```mermaid
sequenceDiagram
    participant P as Message Platform
    participant A as Platform Adapter
    participant S as Gateway Server
    participant D as Durable Gateway Inbox
    participant H as Host Input Runtime
    participant T as Governed Host Runtime

    P->>A: webhook/event
    A->>A: verify platform signature
    A->>S: normalized platform payload
    S->>D: accept inbox with idempotency key
    D-->>S: accepted/duplicate receipt
    D->>H: authenticated Host input
    H->>H: verify HMAC; bind principal/tenant + sender/conversation scope
    H->>T: submit canonical request
    T-->>H: authenticated Host receipt
    H-->>D: completion receipt
    D-->>P: platform-specific acknowledgement
```

Gateway durable inbox 负责重复消息、claim、retry 和 uncertain/DLQ 状态。Host 业务是否允许执行仍由 Secure Ingress、Governor 和 Task Runtime 决定，Gateway receipt 不能替代 Host Acceptance。允许后的 Host Input 会在 AffectFusion/Appraisal 前，把可信 principal/tenant 与签名 `from_id`、`conversation_id` 编译为 `SecureGatewayConversationScopeV1`：sender 只决定 opaque relationship subject，conversation 只决定 opaque session，workspace 只能来自 tenant；缺 sender 直接 durable reject，不能回落到 `default` 或用群会话代替关系对象。Runtime Context 会重新校验同一 scope，因此服务 principal 不会进入七海的关系记录。

## 平台出站

```mermaid
sequenceDiagram
    participant T as Accepted TaskGraph
    participant E as Acceptance Effect Dispatcher
    participant H as Host Gateway Delivery
    participant O as Durable Gateway Outbox
    participant A as Platform Adapter
    participant P as Message Platform

    T->>E: surface.reply_delivery effect intent
    E->>H: accepted reply payload + lineage
    H->>O: enqueue idempotent outbox
    O->>A: claim immutable delivery plan
    A->>P: platform API
    P-->>A: platform confirmation
    A-->>O: ack receipt/digest
    O-->>H: committed delivery status
    H-->>E: effect receipt
```

平台 API 返回不明确时，Gateway 把状态保留为 retryable 或 uncertain，而不是盲目重发并宣称成功。出站 adapter 只能执行已经验收的 delivery plan，不能重写回复内容或重新决定目标用户。

该不确定性也进入 Graph cancellation fence：Acceptance Effect handler/transport 返回 `FAILED` 不能证明平台未收到消息，因此 cancellation 必须继续 `RECONCILIATION_REQUIRED`；只有 `COMMITTED` 且存在 durable receipt 才可自动收口，不能用错误码猜测 `NO_SIDE_EFFECT`。0098 已把签名 Effect 对账接成默认关闭的 append-only sidecar：PENDING proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/`graph_revision`，只进入之后的新 decision evidence；已有 FINALIZED decision 的 correction 仅作 trusted-read overlay，旧 decision、fence 和 evidence 均不改写。生产 gate 只阻断目标数据库中实际存在且未修复/未隔离的 legacy inventory；部署密钥/凭据、完整 key lifecycle 与真实 canary仍未完成。

0099 terminal reconciliation 不替代 transport/effect 对账。它在 `recover_startup` 后单次最多 inventory 10,000 个 terminal Graph，case/proof/supersession 只追加；migration 不碰业务行。Readiness 比较 3 table/4 index/10 trigger canonical SQL 并复核 Python codec；classifier 要求唯一 0090、完整 0091/0092 与 exact failure Outbox refs。只有唯一 graph-scope exact FAILED bundle 可由默认开启的 system auto 精确处理；结构漂移 quarantine。隔离真实 SQLite restart canary 已证明 exact FAILED 自动释放预算，而 signed-required CANCELLED 保持不变并阻断 readiness。它不是 general recovery，当前为 `AUTO_EXACT_ON / LOCAL_SQLITE_CANARY_PASS / OPERATOR_OFF / HTTP_CANARY_NOT_RUN`。

## Edge 设备动作

```mermaid
flowchart LR
    TASK["Accepted node/effect"]
    CMD["Versioned Edge command\ncommand_id + idempotency + signature"]
    RPC["Edge RPC transport"]
    CLAIM["Durable/replay claim"]

    subgraph Actions["Actuators and senses"]
        SPEAK["TTS / speak / barge-in"]
        AVATAR["Avatar / VTS / VRM"]
        DESKTOP["screenshot / automate"]
        ASR["ASR / voice control"]
    end

    RECEIPT["Edge effect receipt"]
    EVENT["Host /v1/edge/events + projection"]

    TASK --> CMD --> RPC --> CLAIM
    CLAIM --> SPEAK
    CLAIM --> AVATAR
    CLAIM --> DESKTOP
    CLAIM --> ASR
    SPEAK --> RECEIPT
    AVATAR --> RECEIPT
    DESKTOP --> RECEIPT
    ASR --> RECEIPT
    RECEIPT --> EVENT
```

真实动作必须绑定 command identity、重放保护和 receipt。语音/Avatar 状态、截图内容或设备 telemetry 是观察材料，不拥有 Host 的任务、Persona 或事实裁决权。

## 受治理陪伴表达的 Host 出箱边界

```mermaid
flowchart LR
    A["Task Acceptance"] --> L["Life foreground receipt = APPLIED"]
    L --> P["Terminal Action Preview"]
    P --> C["从 frozen plan、accepted narrative、protocol 重建 command"]
    C --> O["Host durable embodiment outbox"]
    O --> R["Host admission / exact reopen"]
    R -. "尚未发生物理动作" .-> E["Edge RPC / TTS / Avatar"]
    E --> X["独立 physical receipt"]
```

受治理 Chat 不能直接把 worker 提供的 Voice/Avatar command 送往设备。Host 先重开同一
`life.foreground_completed` receipt 和 exact Action Preview，再以 immutable `AffectiveTurnPlanV1`、
accepted narrative 与 protocol 重新推导 command，并按 idempotency key 写入本地 outbox。outbox 的
`PENDING/DISPATCHING/IN_DOUBT/APPLIED/REJECTED` 只描述 Host 的 durable admission 或后续对账状态；
它不是 TTS 已播放、VTS/VRM 已转场的证据。远端 Edge 位于另一台电脑，本波没有启动。

## 三类入口

| 入口 | 用途 | Authority |
|---|---|---|
| `POST /v1/chat` | 前端/API 直接对话 | Host Secure Ingress + Governor |
| Host input contract | Gateway 平台消息回流 | Gateway transport receipt + Host business admission |
| `POST /v1/edge/events` | Edge 执行/状态事件 | Edge command receipt 与 Host projection |

Edge event 不是用户对话输入；平台 inbox 也不是设备 receipt。把三类 payload 复用成一个松散 JSON 会破坏 idempotency、身份和审计边界。

## 代码锚点

- Gateway lifecycle：`edge_client/runtime/bootstrap.py`、`edge_client/gateway/bootstrap.py`
- Gateway server/security：`edge_client/gateway/server.py`、`webhook_security.py`
- 归一化：`edge_client/gateway/normalizer.py`
- Durable delivery：`edge_client/gateway/delivery_runtime.py`
- Transport store：`edge_client/persistence/sqlite.py` 与 `host_brain/persistence/gateway_transport_repository.py`
- 平台 adapters：`edge_client/gateway/adapters/`
- Host input runtime：`host_brain/input_runtime/`
- Accepted delivery handler：`host_brain/app/acceptance_effect_handlers.py`
- Accepted companion-expression outbox：`host_brain/digital_life/companion_expression_acceptance.py`、`host_brain/app/accepted_companion_expression_handler.py`
- Edge HTTP/RPC：`edge_client/app/fastapi_app.py`、`edge_client/rpc/`
- Host Edge event：`host_brain/app/app_factory.py` 及对应 input runtime handler
- 跨进程合同：`natsume_contracts/edge/`

## 配置与验证边界

- Gateway 是否启用由 `EDGE_GATEWAY_ENABLED` 和 `EDGE_GATEWAY_PLATFORMS` 决定。
- Edge RPC 监听地址来自 `EDGE_RPC_HOST/EDGE_RPC_PORT`。
- 生产鉴权需要独立的 Gateway/Host/Edge secret profile；不同边界不得复用一把通用密钥。
- `task_terminal.reconcile` 使用独立默认关闭的 Secure Ingress Operator 权限和 verification-only Ed25519 公钥环；完整签名命令只进入 0119 专用 append-only 表，通用 TaskGraph 仅携带 ref/digest，并经 durable Review、Action Preview 与 Acceptance Effect 后重开执行。不得复用 Gateway/Edge transport secret 充当签名 authority。
- 没有真实平台账号或设备时，只能标 `NOT_TESTED/DEGRADED`，mock receipt 不能证明真实投递或硬件动作。
