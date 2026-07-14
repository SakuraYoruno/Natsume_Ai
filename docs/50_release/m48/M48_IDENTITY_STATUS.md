受众：MASTER / Identity Reviewer
标题：M48-A Identity Continuity 状态

# M48-A Identity Continuity 状态

版本：v1.0
日期：2026-07-11
状态：`BLOCKED`

## 事实

- 当前候选引用使用 `unavailable://identity/current-m48-a-candidate` 哨兵。
- 前一发布引用使用 `unavailable://identity/previous-release` 哨兵。
- 两个值只用于让严格 manifest 明确表达不可用状态，不是实际 snapshot，也不构成 identity continuity 证据。
- 仓库历史没有可受信、可验证、可回滚的 previous release IdentitySnapshot；审计禁止伪造一个来通过 Seal。

## 解锁条件

MASTER 必须提供或批准一个真实、immutable、可校验的 previous release identity ref，并完成字段级 diff、重大差异 Review、回滚目标验证和审计留痕。完成前 `identity_previous_release_continuity` 与 `identity_snapshot` rollback 均保持 `BLOCKED`。
