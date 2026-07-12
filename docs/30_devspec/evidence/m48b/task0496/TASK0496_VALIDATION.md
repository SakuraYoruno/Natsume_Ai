受众：CODEX / MASTER / Release Reviewer
标题：TASK-0496 M48-B Platform Safety Seal 验证结果

# TASK-0496 Platform Safety Seal

结论：`BLOCKED`。评估基线：`32403cd21ce49d7f5f24beebcdc87d73d7965812`。TASK-0497 与 M49 不得解锁。

## 已通过的部分

- M48-B Entry Gate 仍为 `OPEN`，五项本地 default-off 实施条款均 PASS。
- TASK-0487～0495 九份机器可读 evidence 均存在且 JSON 有效。
- TASK-0487、0488、0493 已 `DONE`；0489～0492、0494～0495 的 owned 安全内核已落地并保持 default-off/fail-closed。
- TASK-0495 已用五域 `ISOLATED_ONLY` profile 完成双 fresh-root golden、construction negative matrix 与 fault injection；focused `172 passed`，最终 changed-boundary atomic E2E `1 passed`，最终复审无 P0/P1。
- 0489～0491 与 0494-owned 的新 secure ingress/consumer 在缺少接线时拒绝、read-only 或 disabled；legacy Host Mate raw producer/startup 并非 fail closed，已作为独立 Seal blocker 登记，不能由这项 PASS 覆盖。

这些 PASS 只证明本地 owned 控制和隔离验证，不构成 production Safety Seal。

## Seal blockers

| Gate | 状态 | 代码/证据事实 |
|---|---|---|
| OBS production router/lifespan | BLOCKED | `router_registry.py` 未 mount secure command router；`asgi.py` 未构造、发布、drain/close 单一 `ObsCommandComposition`。 |
| Skill/Provider production intake | BLOCKED | `provider_catalog.py` 仍可 `importlib` custom/extra provider；无 raw-body SecureIngress signed-intake production route。 |
| Pentest production secure-run | BLOCKED | production 只挂 legacy fail-closed router；无共享 resolver/TokenService/0061 RunStore/server-owned executor composition。 |
| Service Catalog startup/reservation | BLOCKED | coordinator 未进入生产 startup；只读 probe 不能提供/消费原子 reservation。 |
| Host Mate producer/startup | BLOCKED | HTTP producer unsigned；raw UDP `32145` 仍直接 `sendto` 且 getter 默认启用；startup 未先消费 Catalog，并保留停止既有进程路径。 |
| Edge/Gateway WeChat | BLOCKED | Edge 仍追加 unsigned JSONL；Gateway 仍对多个平台共用 generic HMAC key，未消费 dedicated callback credential。 |
| Catalog auth/bind/listener owner | BLOCKED | Mate HTTP/UDP 仍声明 `AuthKind.NONE`；Mate UDP catalog 默认 `0.0.0.0`；Mate HTTP 与 VRM 仍竞争 `embodiment.bridge.http:18770`，尚无唯一 owner/resolution 批准。 |
| Listener/live cutover | BLOCKED | 无批准的 reservation、plugin deployment、listener/device/channel/provider/production route cutover；所有证据均明确未声称 production cutover。 |
| M48-A Release Seal | BLOCKED | `M48_SEAL_RESULT.json.status=BLOCKED`；previous identity、Redis/live service、production target 与 identity/production rollback 未闭环。 |
| Exception expiry | BLOCKED | TASK-0491 production secure-run deferral 的到期点为 protected handoff before TASK-0495/TASK-0496，当前已到 Seal 边界仍未关闭。 |
| MASTER Safety Seal | BLOCKED | Entry Gate 批准明确不替代 Platform Safety Seal；当前没有独立最终签署。 |

原工作区的 `router_registry.py`、`provider_catalog.py`、`agent_runtime_registry.py`、`production_executor.py`、`production_studio.py`、`skill_ids.py` 仍为未提交的受保护 WIP。Entry Gate 将其 owner 冻结为 `MASTER / auto-jp-media-translation`，当前 integration branch 未改动这些文件；在没有显式 handoff 和三方合并前不能擅自覆盖。

## 审计方式

三路并行只读审计分别核验：

1. OBS、Skill/Provider、Pentest production router/catalog/composition；
2. Service Catalog、Mate/WeChat/Gateway producer/startup/reservation/live cutover；
3. M48-A Release Seal、identity/live/prod/rollback、exception expiry 与 MASTER 解锁条件。

三路结论均为 `BLOCKED`。本轮没有修改 production 代码，也没有启动 listener、网络、进程、MateEngine、微信、provider 或物理设备测试；当前阻塞来自 owner/production construction/授权与已有 Release Seal 证据，而不是新增 owned 代码需要反复跑测试。

## 解锁顺序

1. 对六个受保护 WIP 文件完成显式 owner handoff 和三方合并，保留用户改动。
2. 接入 OBS/Pentest production composition，封死 provider in-process dynamic import，并挂 signed quarantine-only intake；通过 production construction 正负测试。
3. 冻结 Catalog 的 Mate authenticated policy、非 public 默认 bind 与唯一 `18770` owner/resolution，再接入 startup 与可信 one-shot reservation；完成 Host/Edge signed producer、Gateway dedicated credential，并消除或认证 raw effect path。
4. 在获批窗口完成 identity/live/prod 与 rollback 证据，使 TASK-0486 达到正式 Seal 条件。
5. 运行最终 rollback-bound construction/cutover wave，解决所有剩余例外的 owner/expiry/rollback。
6. 由 MASTER 单独签发 M48-B Platform Safety Seal；之后 TASK-0497 才能从 `PROGRAMMED` 解锁。

## Rollback / stop

- 保持 Secure Ingress、OBS/Pentest mutation 与新 secured Bridge ingress/consumer 为现有 default-off、query-only、read-only 或 fail-closed；此句不覆盖 legacy raw Mate producer。
- 保持 `NATSUME_BRIDGE_EXPOSURE_MODE=DISABLED` 及各 Bridge kill switch 为 false，并显式设置 `HOST_MATEENGINE_SIGNAL_ENABLED=false`（或撤下 raw signal routes）。
- reservation/owner/credential 不确定时拒绝 candidate，保留 incumbent；禁止 kill/adopt/retarget。
- 保留 durable claim/audit/quarantine/run evidence，不得删除记录来重试可能已执行效果。
- TASK-0486 或 TASK-0496 未 Seal 时，不得开始 TASK-0497。
