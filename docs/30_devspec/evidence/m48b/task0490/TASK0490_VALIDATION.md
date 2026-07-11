受众：CODEX / MASTER
标题：TASK-0490 Skill/Provider supply-chain 验证与 protected handoff 记录

# TASK-0490 Skill/Provider supply-chain 验证与 protected handoff 记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`161daa3c3c3bface5f98d2d212a44a4faa0a1816`。

## 已完成的本地实现

- `HOST_SUPPLY_CHAIN_ACTIVATION_MODE` 只接受 `BUILTINS_ONLY`、`QUARANTINE`、`STAGED`；默认和非法配置都收敛到 fail-closed 的 `BUILTINS_ONLY`。`QUARANTINE` 只允许验证与物理隔离，`STAGED` 最多写入 durable `STAGED`，两者都不会在 Host 进程中 import、activate 或 execute 外部 Python。
- 严格签名 manifest 将 `bundle_kind`、`publisher_id`、name/version、完整 canonical inventory、SBOM 与 sandbox policy 绑定到专用 `HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON` trust statement。调用方不能把已签名 SKILL 重标为 PROVIDER，也不能伪造 publisher。
- verifier 对重复 JSON key、非有限数、未知字段、路径穿越/歧义/大小写碰撞、symlink/reparse/special file、深度、目录、文件和总大小上限均 fail closed；成功审计只包含 signer/digest/SBOM/sandbox 等安全元数据。
- physical quarantine 采用 content-addressed 目录、无跟随链接的有界复制、文件/目录 fsync、临时目录复验、per-digest 跨进程锁和原子 no-replace 发布。verify→copy 期间 source 或 manifest/evidence 被切换时，先拒绝再接触 durable catalog。
- catalog migration `0060_skill_supply_chain.sql` 提供 append-only operation 与 bundle state/fencing 约束；repository 校验 migration checksum/schema，runtime 不建表，数据库不保存 raw bundle、signature 或 secret。虽然 schema 为后续阶段预留 `ACTIVE`，TASK-0490 intake service 本身最多到 `STAGED`。
- 旧 Hub source install/search、raw custom-provider install、custom-provider package import 和 package-manager auto-repair 均已封口；旧接口在网络、subprocess、package-manager 或 active package mutation 之前稳定拒绝。

## 验证结果

| 层 | 结果 |
|---|---|
| focused unit/security/contract 波 | `136 passed, 2 skipped` |
| TASK-0490 integration | 首次且仅运行一次，`1 passed` |
| 独立安全审计 | TASK-0490-owned 实现与测试的 P0/P1 已清零 |
| migration checksum | `0060` SHA-256：`ea7471978f063b6152f3f1699f6896fe5782bede412214b2162b3592b9a1618e` |

integration 使用临时 SQLite、临时 quarantine 和带不可执行 marker 的已签名 PROVIDER bundle，覆盖 0060 migration、验签、隔离、durable intake、重复幂等、STAGED-only、签名后篡改拒绝与零 import。没有启动 live/prod 服务，没有外部网络、真实 provider、Pentest、窗口、设备或物理动作。

## 为什么还不能标 DONE

仍有两个明确的 production handoff，不属于本任务可安全覆盖的文件所有权：

1. 受保护的 `host_brain/skills/provider_catalog.py` 仍拥有 `HOST_SKILL_EXTRA_PROVIDERS` production 动态 import 路径。合并时必须保留该文件现有 WIP，同时保证 `BUILTINS_ONLY`、`QUARANTINE`、`STAGED` 都不会调用任意 extra-provider import。
2. 新 signed-bundle intake 尚未作为 production route 接到 TASK-0487 SecureIngress 和 durable decision audit。该 route 必须具备 authenticated principal、明确 scope、rate、nonce、idempotency 与不含 raw bundle/signature/secret 的 durable allow/reject audit。

handoff 必须补 production construction tests，证明任意 extra provider 不能被 Host 动态导入，且 signed intake 只到 quarantine/`STAGED`，不会在 Host 内激活或执行。完成前不得把 TASK-0490 标为 `DONE`，也不得声称已经具备 production activation 或 sandbox execution。

## Rollback

1. 设 `HOST_SUPPLY_CHAIN_ACTIVATION_MODE=BUILTINS_ONLY`；缺失和非法值本来就会收敛到该模式。
2. 保持 `HOST_SKILL_HUB_ENABLED=false`、`HOST_SKILL_CUSTOM_PROVIDERS_ENABLED=false`、`HOST_SKILL_EXTRA_PROVIDERS=`、`HOST_SKILL_AUTO_REPAIR_ENABLED=false`。
3. 保留 expand-only 0060、physical quarantine 和 audit refs 用于对账；不要删除证据来制造可重试状态。
4. 不恢复 raw Python source install、Host-process dynamic import、package-manager auto-repair 或 unsigned active-package overwrite。

真正的 sandbox worker、governed activation 与 rollback 由 `TASK-0520/TASK-0554/TASK-0572/TASK-0576` 承接；TASK-0490 当前交付的是可验证、可隔离、可审计且默认关闭的供应链入口地基。
