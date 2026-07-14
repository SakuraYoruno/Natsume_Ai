受众：CODEX / MASTER
标题：TASK-0492 Service Catalog、端口 owner 与启动门禁验证记录

# TASK-0492 Service Catalog、端口 owner 与启动门禁验证记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`388010974a3043b1b0a0598eb77c5777e8e5b36a`。

## 已完成的本地实现

- 新增不可变、内容寻址的 `CatalogSnapshot`，17 个固定 endpoint 均声明唯一 `endpoint_id`、`listener_id`、owner、transport、bind、protocol version、health identity、exposure、auth policy、enabled 与源码证据。Catalog/Composition 不导入 Host/Edge/Bridge 实现。
- 默认清单以实际代码为准：Host Main `18080`、Obs `18082`、Monitor Proxy `18180`、Edge RPC `17800`、Gateway `19000`、Desktop Pet `18788`、Mate HTTP/UDP `18770/18771`、Unity Avatar `18770`、VRM Preview `18760` 及 Chroma/Neo4j/Redis 均已登记，真实 enabled/env key 与硬绑定行为已对齐。
- 当前 Mate UDP 代码默认 `0.0.0.0:18771` 且无认证。默认 Catalog 稳定报告 `SERVICE_CATALOG_PUBLIC_BIND_AUTH_REQUIRED`，不把它伪装为 loopback；该缺口由 TASK-0494 完成真实 Bridge 切流。
- VTS 安全 profile 在显式 Mate UDP loopback 时无静态问题；启用 VRM 且 Mate HTTP 同时启用，会在启动回调前报告 `SERVICE_CATALOG_BIND_CONFLICT`，精确指出 `edge.unity.avatar.bridge.http` 与 `host.mateengine.bridge.http` 同占 `127.0.0.1:18770/tcp`。
- 冲突分析区分 TCP/UDP、disabled/dependency/dynamic template、IPv4/IPv6/wildcard 与 IPv4-mapped IPv6。公开或非 loopback listener 必须使用 HMAC、token、mTLS、平台签名或 route policy；`NONE` 与 `PUBLIC_LIVENESS` 都不构成鉴权。
- 启动 authority 只接受 typed `CatalogSnapshot`。静态验证覆盖完整 Catalog；机器 bind/reservation 只探测候选 owner 的 enabled listener，避免顺序启动时把已运行的其他 owner 当作待抢占端口。
- `AUDIT` 只返回非阻断诊断且 `start_allowed=false`；`PREFLIGHT` 在冲突、占用、身份不符或 probe 失败时 fail closed；`ENFORCE` 还要求同一受信 probe 签发并消费短 TTL、snapshot-bound、endpoint-bound reservation。`PreparedServiceStart` 使用 coordinator-local HMAC、私有 registry、内容 fingerprint、TTL 与 one-shot replay 防护。
- read-only socket probe 不 kill、不 adopt、不自动换端口，也不把 check-then-close 冒充 reservation。HTTP 2xx 只有在 endpoint、owner、protocol、version 全部精确匹配时才构成 health identity；启动后失败只停止候选一次，不触碰 incumbent。
- 0062 未创建：endpoint ownership 是版本化静态定义，当前端口可用性与 reservation 是短期运行态；创建空 durable schema 会违反 Entry Gate 的 expand-only 约束。

## 验证结果

| 层 | 结果 |
|---|---|
| focused unit/contract 合并波 | `43 passed in 1.72s` |
| TASK-0492 integration | 首次且仅运行一次，`1 passed in 0.75s` |
| 独立复审 | TASK-0492-owned 实现 P0/P1 清零；production startup 接线与 ENFORCE provider 作为 protected handoff 登记 |
| static checks | `compileall`、`git diff --check` 与 forbidden-import contract 通过 |
| 默认 profile digest | `sha256:65698556c9a1490552b19860c5d7a8090599a5ea61ad079ea933d10c8bf032aa`；只报告真实 Mate UDP public-auth 缺口 |
| safe VTS profile digest | `sha256:c014c640d54904c2ce596c4da8030197f42d83637d11b504ea2c48d423350242`；无静态 issue |
| VRM conflict profile digest | `sha256:7a3ff87fafab2e264d492a1b47fc4cfc32a208c6cd83d835253beb08b9d28341`；精确报告 18770 双 owner |

唯一 integration 使用注入的内存 bind/reservation probe、精确身份 health probe 与 recording lifecycle callback。它验证 VTS 单 owner、VRM 冲突在 callback 前拒绝，以及 ENFORCE reservation 只消费一次、候选只启动一次、重放零 effect。测试未打开真实 socket、未生成进程、未停止服务，也未触发网络、设备或物理动作。

## 为什么还不能标 DONE

当前真实 Host、Obs、Edge RPC、Gateway、MateEngine、Unity 与 Monitor Proxy 启动入口尚未调用 `ServiceCatalogStartupCoordinator`。具体 `ReadOnlySocketBindProbe` 有意不签发 reservation，因此 production `ENFORCE` 也尚无可信 socket-handoff provider。Catalog 和门禁内核已可组合、可验证且 fail closed，但不能声称 production 已在启动前执行它们。

handoff 必须保留各 owner 的受保护 WIP，并完成：

1. Host/Obs/Edge/Gateway/Bridge 启动入口从同一冻结 env 构造 Catalog，按候选 owner 调用 coordinator，再把同一 snapshot/ref 贯穿 start 与 health；不得在业务模块重新读第二套 env。
2. `PREFLIGHT` 必须在任何 uvicorn/nginx/bridge callback 前执行。冲突时不得 kill、adopt、随机改端口或把任意 2xx 当成当前 owner。
3. `ENFORCE` 只有在受信 production reservation provider 能原子交接精确 listener 时才能开放；当前 read-only provider 下必须稳定拒绝。
4. TASK-0494 将 Mate UDP 默认从 wildcard 收敛到 loopback，并为远程暴露补 scope/rate/auth/audit；同时完成 18770 approved owner 的实际切流。
5. Host 裸 CLI 仍有 `0.0.0.0:8000` fallback，Gateway 仍硬绑定 `0.0.0.0`；在 owner 文件接线前不得宣称 Catalog 已覆盖所有启动方式。

完成 production construction/cutover tests 前，TASK-0492 必须保持 `IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`，不得改为 `DONE` 或声称生产已启用 `PREFLIGHT/ENFORCE`。

## Rollback

1. 保持 `HOST_SERVICE_CATALOG_MODE=AUDIT`；AUDIT 只产出证据且不授予启动 authority。
2. 停止候选 owner，只恢复已批准的 loopback owner；不得自动夺取、重派或随机替换 18770。
3. 保留静态 Catalog 与冲突证据；不要通过删除 endpoint、伪造 disabled 或把 public listener 标成 liveness 来制造绿色。
4. 若 reservation/provider 或 health identity 不确定，拒绝新启动并保留 incumbent，不执行 kill/adopt。

TASK-0493 可依赖本任务的 typed endpoint/owner 合同建立 Edge contracts-only import guard；TASK-0494 负责真实 Bridge exposure 与 startup handoff，TASK-0495/0496 负责跨域 golden paths 与 Safety Seal。
