受众：CODEX
标题：NLE WP-21 / TASK-0467 Durable TaskStore 验证报告

# NLE WP-21 / TASK-0467 Durable TaskStore 验证报告

版本：v1.2
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0467 / LTE-WP-21`
基线提交：`b2b2ae4e4`
实现提交：本报告所在 `TASK-0467` 单任务提交

## A. 行为变化

1. `0052_task_runtime.sql` 保持已落地内容不变；新增 forward-only `0054_task_runtime_recovery.sql`，把旧 0052 reservation 无损升级为 lease/fence/expiry 与 reconciliation attestation 模型。旧 `RESERVED` 行迁为 `RECOVERY_REQUIRED` 并写 migration outbox。所有 legacy receipt（含 NULL、空串、控制符与超长 TEXT）原样保存在仅审计 raw 列；运行时不返回 raw，只暴露 `legacy-receipt://0052/rowid-*` 安全 ref、SHA-256 digest 与 migration marker。
2. 新增 `DurableTaskStore`：Graph/Node/Attempt/Budget/Reservation 使用 expected-version CAS；Attempt 只允许一个 live owner，Lease 使用递增 fencing token。所有公开 mutation 与 outbox pending 查询只读取构造时注入的可信 clock，不接受 caller 时间回拨参数。Attempt 进入 SUCCEEDED/FAILED/CANCELLED 时同事务关闭 active lease；startup 另扫 terminal orphan lease，消除崩溃窗口。
3. node cancel 在同一事务级联 attempt `CANCEL_REQUESTED`、该 node 的全部 active lease `CANCELLED` 与 reservation `RECOVERY_REQUIRED`，不依赖 attempt 是否仍是 live 状态。旧 worker随即不能 heartbeat、reserve、commit 或上报成功。存在未澄清 reservation 时 node 保持 `CANCEL_REQUESTED`，仅在 signed reconciliation 后收敛 `CANCELLED`；只允许 direct CANCELLED 的冻结状态会在 unresolved operation 存在时拒绝终态。`WAITING_APPROVAL` 无未澄清工作时按 v1 合同直接取消。
4. 所有 durable state mutation 与 Outbox 在同一 SQLite transaction 内提交；Outbox 精确绑定 aggregate type/id/version、event type、attempt id 与 canonical `taskstore://event/...` payload。发布时间只由 Store clock 填充；evidence 只允许最多 32 个有界 durable refs。错误事件、远未来 availability、错误 payload 或 raw evidence 均回滚状态。
5. operation fingerprint 使用 canonical JSON + SHA-256；同一业务 scope/fingerprint 的并发预留始终只有一行。预留强制绑定 active attempt lease/owner/fence；expiry 或 owner lease 失效只进入 `RECOVERY_REQUIRED`，不自动重放副作用。
6. 所有 reconciliation action 都要求隔离 authority 的 Ed25519 attestation。签名字节与持久化行同时绑定 action + outcome：`COMMIT + SIDE_EFFECT_CONFIRMED + receipt_ref`，或 `RELEASE/REACQUIRE + NO_SIDE_EFFECT`。运行时只持公钥/verification-only protocol，不含私钥或 signer。终态 COMMIT/RELEASE 不依赖已失效 lease；仅 REACQUIRE 要求新的 active lease/fence。未知 key、篡改、跨 reservation、旧 recovery version、action 替换、假 receipt 与重放均拒绝。
7. durable read 层重验 Graph/Node/Budget/Outbox/legacy shadow 的 schema、结构、SQL/JSON identity 与不可变字段；canonical dump 使用 `allow_nan=False`，loads 递归拒绝 duplicate object keys、NaN/±Infinity 与数值溢出产生的非有限 float。损坏数据 fail closed 且不覆盖；冻结 TASK-0466 v1 公共 parser 的兼容 fallback 保持不变。
8. Budget reservation 对 token/tool/time/concurrency/retry/model cost 统一预留与消费；超限请求被拒绝但 attempted usage 与超限维度保留，可与实际 consumed 对账。
9. `AgentJobRegistry` 保持 `LEGACY` read authority。shadow 只保存最小 projection/digest；旧 `updated_at_ms` 拒绝、同时间同 digest 幂等无写、同时间不同 digest 稳定 CAS 冲突，避免乱序回滚。

## B. 文件与范围

| 文件 | 动作 | 说明 |
|---|---|---|
| `host_brain/audit/migrations/0052_task_runtime.sql` | ADD | central expand-only task runtime schema；8 个权威表与并发索引 |
| `host_brain/audit/migrations/0054_task_runtime_recovery.sql` | ADD | 0052→0054 forward recovery/attestation 升级；旧 RESERVED 转待澄清并保留数据 |
| `host_brain/agent_runtime/task_store.py` | ADD | transaction、CAS、attempt ownership、lease/heartbeat/recovery、idempotency、budget、outbox、legacy shadow |
| `host_brain/agent_runtime/job_registry.py` | MODIFY | 可选 dual-write/shadow reconciliation；legacy read authority 不变 |
| `host_brain/agent_runtime/__init__.py` | MODIFY | 导出 TaskStore 公共 API |
| `requirements.txt` | MODIFY | 显式声明 Ed25519 verifier 的 `cryptography` 权威依赖 |
| `tests/contract/test_nle_task_store_contract.py` | ADD | T-NLE-06 |

确认：未新增 canonical scheduler、真实 Worker、副作用执行器或 SSE adapter；未启用 durable read authority；未修改历史 migration `0009` 重号或 `0023～0038` 缺口；未访问原工作区、ignored DB/model/secret/cache。

## C. 测试证据

| 测试组 | 结果 | 关键断言 |
|---|---:|---|
| T-NLE-06 TaskStore contract | 35 passed | 0052→0054 全 receipt 域、CAS/可信时钟、terminal lease cleanup、Ed25519 action/outcome/status matrix、cancel/wait reconciliation、finite strict JSON、outbox 强绑定、budget、legacy 时序 |
| Canonical contracts + Agent Runtime 关联回归 | 142 passed | 原 117 项矩阵加 25 个 T-NLE-06 合同；冻结 v1 parser、WorkOrder/Runtime policy、internal routes 与既有 Agent API 不回退 |
| Migration runner + NLE boundary contract | 7 passed | migration idempotency/drift/rollback 与生产边界基线通过；仅有 2 个既有 escape-sequence warning |
| Static scan | passed | `tools/static_scan/run_all.py --root .`，0 violations |
| Changed-scope doc freshness | passed | 15 个 changed files 命中 4 条规则，0 failures；2 条无关规则 skipped |
| Compile/diff/whitespace | passed | agent_runtime compileall、diff check 与 trailing whitespace 检查通过 |

说明：142 项关联回归包含上述 35 项 T-NLE-06；总计不是 177 个独立测试。关联矩阵保持原 117 项文件集合，计数精确增加 25。

## D. 停止条件、风险与回滚

- 对账停止条件未命中：legacy 与 durable shadow 比较的是同一最小安全 projection；差异返回 `MATCH`、`FIELD_MISMATCH`、`DURABLE_SHADOW_MISSING`、`MIRROR_WRITE_FAILED` 等可解释分类。
- 原子性停止条件未命中：错误 aggregate/event/attempt/payload、重复 idempotency key、caller availability 与 raw evidence 均整事务回滚。
- Authority 边界：生产模块只实现 Ed25519 公钥 verifier/verification protocol；私钥与签发器仅存在于测试 authority fixture。`cryptography` 缺失时 verifier 初始化 fail closed。实际 authority key custody/rotation 与 actor 授权仍由后续 composition/governor 配置。
- 崩溃窗口边界：expiry/cancel 只能进入 `RECOVERY_REQUIRED`；没有签名 outcome attestation 不得 commit/release/reacquire。未澄清 reservation 会阻止 node 进入取消终态。
- 集成顺序风险：`0052` 已落地且在本次修复中保持字节级不变；`0053` 由并行 TASK-0465 提供，TASK-0467 仅新增 forward-only `0054`。单分支不伪造 0053，也不宣称 full catalog gate。主线必须连续集成 TASK-0465（0053）→TASK-0467（0054），再跑空库、旧 0052 库和重复应用的完整 catalog gate。本分支证据仅为隔离的 0052→0054 数据保留与 sequential runner replay。
- Shadow 装配风险：`AgentJobRegistry` 只有在显式注入已迁移 Store 后才 dual-write；未装配或 Store 故障时继续 legacy-only，并产生可解释 reconciliation，不自动触碰生产 DB。
- Payload 风险：legacy shadow 只保留摘要与 digest，不能用于恢复原 payload；这是避免 durable raw thought/敏感正文的设计边界，不是第二 payload store。任何 durable JSON/schema/identity 损坏均返回 `TASK_STORE_DURABLE_DATA_INVALID`，不会以空对象继续或覆盖。
- Legacy receipt 风险：0052 raw receipt 为迁移审计数据，可能包含控制符或大文本；普通 TaskStore read 永不返回它，只返回 safe ref、digest 与 null marker。若未来提供专用审计读取，必须另行受权、限流与脱敏。
- 回滚：解除 TaskStore 注入并保持 AgentJobRegistry legacy read；撤销 Python adapter 即可恢复旧行为。0052/0054 均为 expand/forward-only，回滚不删表、不破坏证据。

## E. 自动推进裁决

- [x] T-NLE-06、关联回归、static/compile/diff 门禁通过。
- [x] TASK-0467 可标记 DONE 并按单任务提交。
- [x] 未授权 canonical scheduler、Worker 或 durable read authority 切流。
- [x] TASK-0469 仍须等待 TASK-0462 与 TASK-0468；本任务未越过依赖 Gate。
