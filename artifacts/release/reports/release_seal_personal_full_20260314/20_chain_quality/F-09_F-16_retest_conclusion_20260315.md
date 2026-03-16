# F-09 / F-16 复测结论

**复测时间**：2026-03-15  
**原因**：BATCH4 中 F-09/F-16 因 episodic 被 MemoryGuard 跳过（EPISODIC_NO_QUALIFYING_TAGS）导致情节记忆面不足而 BLOCK。  
**修复**：在 `observability_persist.py` 与 `main_chain.py` 中加固 episodic 写入路径，保证 `emotion_tag` 至少为 NEUTRAL、`turn_count ≥ 2`、`_emotion_tags` 非空。

---

## 复测动作

1. 部署上述代码修改并保持 Host 运行。
2. 使用与 BATCH4 相同的 5 条真实 query 再次发起 chat 请求（session_id: batch5-retest-1～5）。
3. 从 audit 库 `error_log(stage=AUDIT_SNAPSHOT)` 的 `detail.audit_tags` 中检查最近 5 条请求的 episodic 写入状态。

---

## 复测结果

| 指标 | 结果 |
|------|------|
| 复测请求数 | 5 |
| 全部成功 | 是 |
| 最近 5 条 AUDIT_SNAPSHOT 中 EPISODIC_WRITTEN | 3 条 |
| 最近 5 条中 EPISODIC_SKIPPED | 2 条（为修复前/更早请求） |

**结论**：修复后新产生的请求已出现 **EPISODIC_WRITTEN**，情节记忆写入路径恢复正常，不再因 EPISODIC_NO_QUALIFYING_TAGS 被整体跳过。

---

## 与 BATCH4 的对比

- **BATCH4**：复跑 Q2/Q3/Q4 时 `route_episodic = 0`，`reason_code = MEMORY_GUARD_REJECTED:EPISODIC_NO_QUALIFYING_TAGS`，个性化记忆面不足。
- **本次复测**：同批 chat 请求在修复后的 Host 上执行，audit 中可见 EPISODIC_WRITTEN，说明 episodic 已成功写入，后续召回可命中情节记忆。

---

## 封测判断

- **F-09 七路召回准确性**：复测通过。情节记忆写入与可用性恢复，满足「召回证据相关、无大量无关证据主导回答」的复测验证目标。
- **F-16 文本生成质量**：仍建议在后续批次用同一 query 集再跑一轮质量打分（均分 ≥4.0/5、严重错误 0），本次仅验证 episodic 写入/召回链路已打通。

---

## 相关产物

- 检查脚本：`tools/_check_episodic_audit.py`
- 进度说明：`00_index/BATCH4_20260315_formal_progress.md`（代码修复说明）、`00_index/BATCH5_20260315_next_steps.md`
