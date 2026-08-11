受众：MASTER
标题：M48-B Entry Gate Packet

# M48-B Entry Gate Packet

状态：`APPROVED_FOR_LOCAL_IMPLEMENTATION`；Entry Gate 为 `OPEN`，TASK-0487 已解锁为 `PLANNED`。生产/live/物理切流不在本批准范围内。

机器可读 SSOT：`M48B_ENTRY_GATE_PACKET.json`。机检结果：`M48B_ENTRY_GATE_RESULT.json`。

## Gate 判定

| 条款 | 准备结果 | 说明 |
|---|---|---|
| 1. TASK-0461 基线与 dirty owner | PASS | 基线 manifest 26 项，hash 与 TASK-0461 冻结值一致；M48-A 当前冻结于 `29bedcaf`。 |
| 2. 文件、migration、切流 owner | PASS | 13 个文件 scope、5 个 migration reservation、8 个 cutover surface 已唯一登记，并获用户委托的 CODEX MASTER 批准。 |
| 3. 五域负例隔离 | PASS（准备态） | Gateway、Obs、Pentest、Skill/Provider、Bridge 均登记 test-only principal、每测临时 secret、loopback channel、禁止真实进程/物理副作用；共享 harness 为 `tests/fixtures/m48b_isolation.py`。 |
| 4. 中央 migration catalog | PASS（登记态） | 唯一 catalog 为 `host_brain/audit/migrations`；0058～0062 已预留，禁止第二 catalog 和历史 SQL 重写。 |
| 5. P0 flag/stop/evidence/rollback | PASS（登记态） | TASK-0487～0495 九个切片均已登记；flag 仍为计划配置，不代表功能已实现。 |

五项条款均满足后，Gate 已从 `BLOCKED` 变为 `OPEN`。批准只解锁本地 default-off 实现；本 Packet 不把任何 TASK-0487+ 能力描述为已实现，也不授权生产/live/物理副作用切流。

M48-A 的 532 个变更文件以 `030ad1f98^..29bedcaf` 整体冻结。MASTER 批准后，只把 JSON matrix 明列的 M48-B scope 临时转交对应 primary owner；其余 M48-A 文件、历史证据与 0050～0057 migrations 不转交。

## 并行与独占顺序

1. TASK-0487 先独占创建 identity contract 与 Secure Ingress 决策链。
2. 0487 完成后，TASK-0488、0489、0490、0491、0492 可在 owner scope 不交叉时并行。
3. TASK-0493 等待 0492；其 import guard 与 0488 的 Edge 文件迁移串行。
4. TASK-0494 等待 0487、0492、0493；`bootstrap.py` / WeChat adapter 与 0488 串行。
5. TASK-0495 汇合验证；TASK-0496 最后执行 Safety Seal。

受保护 WIP `provider_catalog.py`、`agent_runtime_registry.py`、`router_registry.py`、`production_executor.py`、`production_studio.py`、`skill_ids.py` 继续由 `MASTER / auto-jp-media-translation` 持有。M48-B 只能先建 adapter；未 handoff 前不得直接修改或做最终接线。

## Migration 冻结

| Asset | Primary owner | 用途 |
|---|---|---|
| `0058_secure_ingress.sql` | TASK-0487 | principal / nonce / rate / idempotency / audit |
| `0059_gateway_transport.sql` | TASK-0488 | durable inbox / outbox / ack / DLQ |
| `0060_skill_supply_chain.sql` | TASK-0490 | digest / signature / SBOM / quarantine |
| `0061_pentest_run_store.sql` | TASK-0491 | 仅共享 store 无法表达时创建 |
| `0062_service_catalog.sql` | TASK-0492 | 仅 catalog 必须 durable 时创建 |

现有 catalog 的重复 `0009` 与缺失 `0023～0038` 是已冻结基线债务。TASK-0487 作为 M48-B catalog steward，只能在 MASTER 批准后向现有 runner/guard 增加 checksum-bound compatibility policy；不得重写 0001～0057，也不得用 M48-A 的离线 subset clone 冒充完整 catalog 绿色。完整 catalog 或 empty/old DB clone 失败时，任何 0058+ schema cutover 必须停止。

## 安全 rollback 下限

Rollback 恢复的是旧的稳定处理面，不恢复匿名写、无签名 webhook、自签 Pentest 授权、未签名热导入或 `0.0.0.0` 无认证 Bridge。若旧入口低于安全下限，rollback 行为是停止新写并保留只读/排空能力。

## 验证节奏

- 小任务：只跑变更相关 unit / contract / static checks。
- 大切片汇合：只跑一次对应 integration / golden / recovery wave。
- 失败修复：只重跑失败项和受影响边界，不重复未变化的整套测试。
- TASK-0621/0622：所有任务完成后统一执行一次全量真实环境验收与最终发布门禁。

## MASTER 审批

- 状态：`APPROVED`
- 身份：`USER_DELEGATED_CODEX_MASTER`
- 审批范围：文件 owner、migration owner、cutover owner、protected-WIP reservation、catalog baseline debt disposition
- 当前 ref：`M48B_ENTRY_GATE_MASTER_APPROVAL.md`
- 限制：仅本地 default-off implementation；不授权生产数据迁移、live service、外部消息、真实 Pentest、设备或物理动作。
