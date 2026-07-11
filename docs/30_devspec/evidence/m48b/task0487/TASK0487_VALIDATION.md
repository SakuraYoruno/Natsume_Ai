受众：CODEX
标题：TASK-0487 Secure Ingress 验证记录

# TASK-0487 Secure Ingress 验证记录

状态：`DONE`。实现 commit：`45199c086960bc81c6cfa5eb0d8aab1c9735ea03`。

## 交付结果

- 新增零 Host/Edge/framework/storage 依赖的 `natsume.identity.v1`：Principal、credential metadata/runtime proof 分离、canonical round-trip、严格 scope matching 和稳定 fail-closed reason。
- 新增 Secure Ingress 决策链：transport attempt rate → principal → HMAC/opaque token → scope → authority rate → durable claim → atomic claim+audit。
- 新增 fsync emergency JSONL。primary audit 失败时拒绝执行；拒绝原因不会被 audit 故障覆盖，primary 与 emergency sink 都禁止 secret-pattern material。
- 新增 server-owned Host、Obs、Gateway、Bridge surface policy；调用者不能提交自定义 action/scope policy。
- 新增中央 `0058_secure_ingress.sql`，无第二 migration catalog、无 runtime business DDL。
- 修复中央 catalog 的历史 0009/gap/FTS 可重放问题：全部例外按 filename、checksum、anchor、exact error 与 postcondition 绑定，并新增 physical asset ledger。

## 验证

| 层 | 结果 |
|---|---|
| TASK-0487 unit/contract | `125 passed in 5.18s` |
| 既有 migration runner/guard regression | `31 passed in 1.13s` |
| TASK-0487 integration wave | `1 passed in 2.09s` |
| natsume_contracts boundary / runtime DDL / whitespace | PASS |

集成波只运行一次任务级场景；第一次执行曾因测试按随机 `audit_ref` 排序误取行而失败，修正为按返回的 `audit_ref` 精确定位后通过。后续安全修复影响了该边界，因此按新验证规则只重跑同一个受影响集成波，没有运行全仓测试。

## 安全停止与 rollback

- `HOST_SECURE_INGRESS_MODE` 默认及非法值均为 `OFF`；`SHADOW` 永不返回执行许可。
- TASK-0487 不接 production route、不发外部消息、不跑真实 Pentest、不触发设备/窗口/物理动作。
- 0058 只做 expand；本地验证使用空库/临时 clone。失败时丢弃 clone，不对用户库执行破坏性 down migration。
- 实际 Gateway、Obs、Bridge 切流分别由 TASK-0488、TASK-0489、TASK-0494 独占；rollback 不能恢复不安全旧入口。
