# 正式封测批次 3 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 3**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-05` 关键数据备份、副本恢复可读性校验、最小回滚与恢复验证

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-05 | PASS | 关键数据备份副本可读，回滚门禁命中后可真实切入保守路由，且可恢复回正常配置 |
| F-06 | PENDING | 尚未开始 6h soak |

## 关键发现

1. 现有周稳定性报告在当前阈值下会触发 `ROLLBACK_REQUIRED`，说明项目内现成回滚门禁可用于真实判定。
2. 当前项目的“最小回滚”本质是路由与灰度层回滚，而不是代码版本回退。
3. 回滚态 `Host` 仍可正常对外服务，请求中可观测到 `HOST_CHAT_FORCE_CLOUD` 命中标记。
4. 从回滚态恢复回正常配置后，请求中已不再出现 `HOST_CHAT_FORCE_CLOUD` 标记。

## 已落盘报告

- `50_recovery_rollback/F-05_20260315_backup_restore_rollback.md`
- `50_recovery_rollback/F-05_release_rollback_gate_20260315.json`
- `50_recovery_rollback/F-05_release_rollback_gate_20260315.md`
- `50_recovery_rollback/F-05_backup_snapshot_20260315_165142/manifest.json`

## 下一步建议顺序

1. 启动 `F-06` 6h soak 与资源采样。
2. 并行准备 `F-09 / F-14 / F-15 / F-16 / F-17` 的质量与时延样本集。

## 当前结论

- `F-05` 已具备通过证据。
- 正式封测整体仍为 **IN PROGRESS**，下一阻断项是 `F-06` 长稳。
