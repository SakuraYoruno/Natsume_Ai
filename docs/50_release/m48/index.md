受众：MASTER
标题：M48-A 发布证据目录

# M48-A 发布证据目录

版本：v1.0
日期：2026-07-11
状态：`BLOCKED`

## 机器可读资产

- `M48_RELEASE_MANIFEST.json`：release ID、代码 commit、identity refs、required IDs 与 artifact hashes。
- `M48_EVIDENCE.json`：测试、构建、环境、identity 与生产演练分类。
- `M48_ROLLBACK_MATRIX.json`：严格 rollback contract。
- `M48_SEAL_RESULT.json`：runner 输出；当前为 `BLOCKED`。

## 人类可读资产

- `M48_SEAL_RESULT.md`：Seal 决策与阻塞项。
- `M48_ROLLBACK_MATRIX.md`：回滚范围与演练状态。
- `M48_IDENTITY_STATUS.md`：identity continuity 哨兵与解锁条件。
- `KNOWN_LIMITATIONS.md`：live/prod 限制和 V1.x backlog。

验证报告：`docs/35_quality/audits/NLE_WP80_TASK0486_VALIDATION_20260711.md`。
