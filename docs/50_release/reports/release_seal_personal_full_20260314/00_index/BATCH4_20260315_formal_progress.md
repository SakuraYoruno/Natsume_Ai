# 正式封测批次 4 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 4**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-17` Chat 主链路时延样本集
- `F-09 / F-16` 真实 query 质量样本
- `F-14 / F-15` 可观测性与入口确认

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-09 | BLOCK | 七路召回虽返回 `OK`，但个性化高价值记忆面不足；复跑中 `route_episodic=0` 且 `episodic_memory` 被 `MemoryGuard` 跳过 |
| F-14 | BLOCK | 主链调用的 `get_context_compressor()` 在当前代码中不存在，实际走 `SKIPPED_NOT_WIRED`；统计口径也未读到真实审计表 |
| F-15 | BLOCK | 仅存在占位级 `session_summary` skill 与 `bond_summary` helper，正式总结调用链和真实 summary 产物均未形成 |
| F-16 | BLOCK | 文本质量样本均分 `3.2/5`，且严重不达标样本 `3` 条，表现为回答易泛化 |
| F-17 | PASS | `first_token/commit/tts_start/vts_start` 的 p95 全部低于计划阈值 |

## 关键发现

1. `F-17` 当前已过阈值：`first_token p95=3463.8ms`、`commit p95=6438.5ms`、`tts_start p95=2921.4ms`、`vts_start p95=20ms`。
2. 当前质量问题不在“检索是否运行”，而在“高价值个性化记忆是否真正进入稳定记忆面并主导最终回答”。
3. 复跑 `Q2 / Q3 / Q4` 时，`route_episodic / route_neo4j_bond / route_persona_utterance` 均为 `0` 命中，`episodic_memory.reason_code = MEMORY_GUARD_REJECTED:EPISODIC_NO_QUALIFYING_TAGS`。
4. `F-14` 当前已确认是主链压缩依赖未接线，不只是透传问题；`F-15` 当前也未形成满足封测口径的正式总结闭环。

## 已落盘报告

- `20_chain_quality/F-17_chat_latency_samples_20260315_170525.json`
- `20_chain_quality/F-17_chat_latency_samples_20260315_170525.md`
- `20_chain_quality/F-09_F-16_quality_samples_20260315_170759.json`
- `20_chain_quality/F-09_F-16_quality_samples_20260315_170759.md`
- `20_chain_quality/F-09_F-14_F-15_F-16_F-17_20260315_batch4.md`

## 下一步建议顺序

1. 继续运行 `F-06` 长稳 soak，等待样本窗拉长。
2. ~~针对 `F-09 / F-16` 的阻断样本，优先修复 `episodic_memory` 被 `MemoryGuard` 跳过~~ → **已修复**（见下方「代码修复」）。
3. 针对 `F-14` 先补 `get_context_compressor()` 真实接线并修正 obs 统计读表口径；针对 `F-15` 先补正式阶段/会话总结产物，再重新复测。

## 代码修复（2026-03-15 后续）

- **F-09 相关**：`observability_persist.py` 与 `main_chain.py` 已加固 episodic 写入路径，确保 `emotion_tag` 至少为 `NEUTRAL`、`turn_count` 至少为 2、`_emotion_tags` 永不为空，避免 `EPISODIC_NO_QUALIFYING_TAGS` 导致情节记忆无法写入、召回面为空。需**重新跑质量样本**验证 F-09/F-16。

## 当前结论

- 当前时延项已有通过证据。
- 当前质量项仍存在阻断，正式封测不能放行。
