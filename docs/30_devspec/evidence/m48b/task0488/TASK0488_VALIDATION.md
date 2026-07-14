受众：CODEX
标题：TASK-0488 Gateway durable transport 验证记录

# TASK-0488 Gateway durable transport 验证记录

状态：`DONE`。实现 commit：`6359ef4b489ea1c103c84814dfacfa3409b19753`。

## 交付结果

- LINE、Feishu、WeCom 与 internal webhook 先用原始 body/header 完成严格验签，再进入解析和业务 adapter；重复 header/query/JSON key、非有限数、恶意 XML 和错误加密材料全部 fail closed。
- 中央 migration `0059_gateway_transport.sql` 提供 durable inbox/outbox/ack/DLQ、lease/fencing/attempt 与 reconciliation 状态；数据库只保存 raw-body SHA-256 和大小，不保存原文或凭据。
- `LEGACY_DRAIN` 为默认及非法值回退；`SHADOW` 仅验证，不持久化、不 ACK、不启 worker；只有 `DURABLE` 接管 HTTP、WeChat DB watcher 和 Telegram producer。
- 出站状态显式区分 `DEFINITELY_NOT_SENT`、`ADAPTER_CONFIRMED`、`MAY_HAVE_COMMITTED`。最后一种只进 reconciliation/DLQ，禁止普通重放。
- Edge→Host input 与 Host→Gateway reply 分离 HMAC 密钥；Host reply delivery ID、WeChat fallback ID 和 Telegram 缺失时间均保持重试稳定。
- 生命周期顺序为 HTTP ready → 出站依赖 ready → worker → intake producer。停机先禁新 intake；HTTP、worker 或 async loop 超时都会显式失败并保留可重试实例及尚需处置的依赖。

## 验证

| 层 | 结果 |
|---|---|
| 广覆盖 Gateway unit/contract 波 | 首次 `245 passed, 1 failed in 32.26s`；唯一失败是已移除的内存 msg-id 去重断言，改为验证 SecureIngress durable duplicate 后定向 `1 passed in 1.07s` |
| Telegram 稳定身份 / Gateway contract | `15 passed`；`77 passed` |
| TASK-0488 integration wave | 只运行一次，`2 passed in 4.04s` |
| 生命周期终审回归 | `50 passed in 3.55s` |
| py_compile / diff check / migration checksum | PASS；0059 SHA-256 为 `bad91de55866c6e9ffffed8a8d249abf077b146bce6b3705a5f95d11b91a8944` |

集成波只运行一次，没有全仓重复测试。测试全部使用临时 SQLite、fake adapter/HTTP transport；未访问 live/prod、真实平台、Pentest 网络、窗口、设备或物理执行器。

## 安全停止与 rollback

1. 先设 `EDGE_GATEWAY_ACCEPT_NEW_INTAKE=false`，同时阻断 HTTP、DB watcher 和 Telegram 新输入，保留 durable worker 排空。
2. 只有在 `pending=0`、`claimed=0`、`reconciliation_required=false` 后才切回 `EDGE_GATEWAY_TRANSPORT_MODE=LEGACY_DRAIN`。
3. `MAY_HAVE_COMMITTED` 必须人工/平台 receipt 对账后解决；不得普通 replay。
4. 保留 expand-only 0059、claim、receipt、DLQ 与 audit 证据，不执行破坏性 down migration，也不恢复匿名 webhook、共享密钥或双执行。

## 明确延期债务

- Host receipt 独立签名/TLS、verifier pre-parse durable replay/rate 和远端平台 message ID：`TASK-0592`。
- Host raw middleware / oversized module 拆分：`TASK-0500/TASK-0512/TASK-0522/TASK-0592`。
- Edge contracts-only 和 store/signing ownership：`TASK-0493/TASK-0584`。
- authenticated admin query 与 bridge 暴露：`TASK-0494`。

这些均为已登记 P2，不冒充 TASK-0488 已完成能力。
