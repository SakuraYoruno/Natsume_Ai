受众：CODEX / MASTER
标题：TASK-0491 Pentest durable authority、Lab isolation 与 RunStore 验证记录

# TASK-0491 Pentest durable authority、Lab isolation 与 RunStore 验证记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`7d4a202355d1d54ab0392f881952707c22122e39`。

## 已完成的本地实现

- `HOST_PENTEST_SECURE_RUN_MODE` 只接受 `READ_ONLY`、`SHADOW`、`LAB_ENFORCED`；默认和非法配置都不会产生主动网络、进程、监听器或外部 DNS 效果。旧 `PENTEST_WEB_LAB_MODE`、`PENTEST_ALLOW_EXPLOIT` 与 `PENTEST_WEB_RUN_REAL_PROBES` 不再构成授权。
- Secure run 只接受 TASK-0487 写入 0058 的真实 committed audit/claim。只读 `SQLiteSecureIngressAuthorityResolver` 精确核对 principal、tenant、key、credential kind、proof digest、method、path、scope、body digest、decision 与 expiry，再签发进程内 attested evidence。
- SecureIngress grant、Governor token intersection、Lab capability permit、execution decision、RunStore create/mutation authority均带完整内容 HMAC；派生 receipt、capability identity/digest 与 snapshot binding 会重新计算。即使通过 `object.__setattr__` 修改 frozen object，验证也会失败。
- receipt 在能力签发前以 principal、tenant、run、binding、expiry 一次性消费。0061 RunStore 在 `BEGIN IMMEDIATE` 内使用自身时钟二次核验 authority/receipt，完成 owner/tenant、CAS 与字段级 strict diff；一次执行只能追加一条精确 tool result，旧历史和业务字段不可替换。
- 网络、HTTP、DNS、listener 与 subprocess 路径统一经过 Lab choke point。目标仅允许 loopback/IANA documentation fixture；子进程只接受固定 registry 的可信绝对 binary，拒绝 path 型 argv、ambient `PATH` 和会改变执行语义的 kwargs，并强制 sanitized env、固定 cwd、DEVNULL stdin 与 one-shot executor lease。
- legacy HTTP create/approve/continue/cancel/knowledge mutation以及 run/events/report/artifact reads都在状态、存储、事件总线或 artifact 访问前稳定 `403`。本地自签 manifest、mock authority、caller governor bool/dict 与通用 RunStore writer 永久 fail closed。
- effect 结果区分 `EXECUTED`、`NOT_EXECUTED`、`UNCERTAIN`；明确 Guard 拒绝不会被误报为“可能已执行”，执行后持久化失败也不会谎称零效果。

## 验证结果

| 层 | 结果 |
|---|---|
| focused unit/security/contract 合并波 | `193 passed`，仅 2 条既有 invalid escape `DeprecationWarning` |
| TASK-0491 integration | 首次且仅运行一次，`1 passed` |
| 独立安全复审 | TASK-0491-owned 实现 P0/P1 清零；不可达 legacy artifact fallback 作为 protected handoff P2 登记 |
| migration checksum | `0061` SHA-256：`4aeda398f2f758b5c1e8cb2894aef93b17177137112d5145e720bfd607c37fa0` |
| static checks | `py_compile` / `compileall`、`git diff --check` 与 migration SHA 对照通过 |

唯一 integration 使用临时中央 migration catalog、临时 SQLite、真实 SecureIngress store/HMAC verifier/read-only resolver、真实 `TokenService`、sealed create authority 和 `@guarded` 纯内存 recording executor。它验证首次执行只产生一次 effect 与一次 CAS mutation；fresh Governor token 重放同一 receipt 时，executor、snapshot 和 mutation journal 都保持不变。测试没有外部网络、真实 Pentest binary、容器、浏览器、设备或物理动作。

## 为什么还不能标 DONE

受保护的 `host_brain/app/router_registry.py` 当前仍只挂载 legacy Pentest router。该 router 已安全封口，但 production 尚未挂载 `pentest.secure-run` command adapter，也没有由 composition 注入共享 0058 resolver、`TokenService`、0061 RunStore 与 server-owned executor registry。因此当前交付是可组合、可验证且默认关闭的安全执行内核，不是 production Pentest activation。

handoff 必须在保留 protected WIP 的前提下完成：

1. secure command route 先走 TASK-0487 raw-body SecureIngress，再把 exact decision 交给本任务服务；不能接受 caller principal、mock resolver、self-signed token 或 governor bool。
2. production composition 只构造一套共享 resolver/token/store/executor authority，并把 owner principal/tenant 贯穿 create、execute 与所有 query/read projection。
3. legacy mutation 继续拒绝；secure route 不得复用 `RUNS` 或 artifact JSON fallback。run/events/report/artifact read 必须先有 authenticated owner/tenant policy。
4. construction tests 必须证明真实 secure request 可达，而 self-signed、mock、cross-tenant、non-Lab 与 replay 都是零 effect。

完成这些接线和测试前，不得把 TASK-0491 标成 `DONE`，也不得声称 production 已能主动扫描或执行 exploit。

## Rollback

1. 设 `HOST_PENTEST_SECURE_RUN_MODE=READ_ONLY`；缺失和非法值本来就收敛到 fail-closed。
2. 保持 `PENTEST_WEB_LAB_MODE=false`、`PENTEST_ALLOW_EXPLOIT=false`、`PENTEST_WEB_RUN_REAL_PROBES=false`、`ONLINE_SCAN_MODE=offline`。
3. 保留 expand-only 0061、mutation/receipt 与 SecureIngress audit 供对账，不删除证据来制造可重试状态。
4. 不恢复自签 manifest、mock principal、caller governor flag、generic RunStore writer、匿名 artifact read 或任何未经过 Lab capability 的网络/进程路径。

TASK-0563 将继续完成 Pentest Pack 的 shared WorkOrder/RunStore 收敛；TASK-0495/0496 负责平台 golden paths 与 Safety Seal。本任务不提前声称这些后续能力。
