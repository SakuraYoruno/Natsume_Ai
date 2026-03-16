# F-05 备份、恢复与最小回滚记录（2026-03-15）

## 结论

- `F-05 备份、恢复与最小回滚`：**PASS**

说明：本轮已完成本地关键数据快照、恢复副本可读性校验、回滚门禁判定、真实最小回滚演练、以及回滚后恢复到正常运行态的验证。

## 一、关键数据备份快照

本轮生成本地备份工件目录：

- `artifacts/release/reports/release_seal_personal_full_20260314/50_recovery_rollback/F-05_backup_snapshot_20260315_165142/`

其中包含：

1. `audit.sqlite3`
2. `audit.sqlite3-wal`
3. `audit.sqlite3-shm`
4. `manifest.json`

备份摘要：

- `audit.sqlite3`：`12623872 bytes`
- `audit.sqlite3-wal`：`4136512 bytes`
- `audit.sqlite3-shm`：`32768 bytes`

同时对运行态目录做了存在性与规模快照：

- `runtime/docker/neo4j/data`：`exists=true`，`file_count=84`
- `runtime/docker/neo4j/logs`：`exists=true`，`file_count=5`
- `runtime/docker/chroma/data`：`exists=true`，`file_count=0`
- `runtime/docker/redis/data`：`exists=true`，`file_count=4`

## 二、恢复副本可读性校验

在备份目录下创建恢复检查副本：

- `restore_check/audit.sqlite3`
- `restore_check/audit.sqlite3-wal`
- `restore_check/audit.sqlite3-shm`

对恢复副本执行 SQLite 校验结果：

- `PRAGMA integrity_check = ok`
- `table_count = 34`

判断：

1. 本轮备份副本可成功读取。
2. 审计主账本未出现结构损坏。
3. 关键数据满足“恢复后可读”要求。

## 三、回滚门禁判定

基于已有稳定性报告执行：

- 输入报告：`artifacts/trace/WEEKLY_STABILITY_REPORT_20260310_005912.json`
- 输出工件：
  - `50_recovery_rollback/F-05_release_rollback_gate_20260315.json`
  - `50_recovery_rollback/F-05_release_rollback_gate_20260315.md`

结果：

- `decision = ROLLBACK_REQUIRED`
- `rollback_required = true`

触发条件：

1. `summary.alert_count = 2`，超过阈值 `0`
2. `retrieval.degraded_rate_pct = 21.91`，超过阈值 `20`
3. `governor.failure_rate_pct = 24.22`，超过阈值 `15`

## 四、最小回滚真实演练

### 1. 回滚方式

按项目现有回滚手册，将 Host 聊天路径临时切换为更保守模式：

1. `HOST_CHAT_FORCE_CLOUD=true`
2. `HOST_CHAT_HOST_8B_ENABLED=false`
3. `HOST_GAMING_EDGE_CHAT_ENABLED=false`
4. `HOST_CHAT_GRAY_ENABLED=false`
5. `HOST_CHAT_GRAY_PERCENT=100`

### 2. 回滚后运行验证

在真实 `Host` 运行态下执行最小回滚并验证：

- `Host /health = 200`

回滚态探针请求：

- `request_id = req_a2893dca1e6542de9d474175605b1706`
- `decision = APPROVE`
- `reason_code = null`
- `contains_force_cloud_marker = true`
- `contains_gemini = true`
- `narrative = はい、日本語で一言お伝えしますね。`

判断：

1. 回滚配置已真实生效，而不是仅停留在脚本层。
2. 回滚后服务仍能正常响应请求。
3. 当前项目的最小回滚路径是可执行的。

## 五、从回滚态恢复到正常配置

完成回滚验证后，立即恢复回当前正式运行配置：

1. 停止回滚态 Host
2. 用常规 `.env` 重新启动 `Host`

恢复结果：

- `Host /health = 200`

恢复后探针请求：

- `request_id = req_0460be2be4b24b4380dcf59f9744005f`
- `decision = APPROVE`
- `reason_code = null`
- `contains_force_cloud_marker = false`
- `contains_gemini = false`
- `narrative = こんにちは、夏目藍です。何かお手伝いできますか？`

说明：

1. 当前环境已从回滚保护态恢复回正常配置。
2. 恢复过程中 `startup warmup` 曾命中一次 `VOICE_PLAYBACK_FAILED`，但该问题仅表现为启动期语音预热冲突告警，不影响 `Host /health=200` 与后续真实请求可用性。

## 六、本项判断

1. 已拿到关键数据备份副本。
2. 已证明恢复副本可读。
3. 已证明最小回滚路径可真实触发并维持服务可用。
4. 已证明可以从回滚态恢复回当前正式运行配置。

因此本轮将 `F-05` 判定为 **PASS**。
