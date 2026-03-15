# 个人全功能封测执行报告（batch-02 historical bootstrap diagnosis）

## 1. 基本信息

| 项 | 填写 |
|---|---|
| batch_id | 20260314_batch02_voice_preflight |
| exec_date | 2026-03-14 |
| operator | Codex |
| environment | historical single-node bootstrap diagnosis only, invalid for formal LAN dual-node seal test |
| scope | F-11 / F-12 historical bootstrap diagnosis |
| final_decision | BLOCK |

## 2. 总结

1. 本轮执行项：`F-11`、`F-12`
2. 通过数量：`0`
3. 阻断数量：`2`
4. 当前结论：`该批次仅为历史 bootstrap 排障记录，不纳入正式双节点封测结论`

## 3. 阻断项

| ID | 问题 | 影响 | 证据路径 | 修复建议 | 复测结论 |
|---|---|---|---|---|---|
| F-11 | `VTS_CONNECTION_FAILED` | 无法观察真实表情切换与 talk gate | `20_chain_quality/F-11_vts_expression_preflight.md` | 修复 VTube Studio 安装与 API 监听后复测 | pending |
| F-12 | `TTS_MODEL_UNAVAILABLE` | 无法生成真实可听语音，也无法进入口型同步 | `20_chain_quality/F-12_tts_vts_sync_preflight.md` | 补齐 `models/gpt_sovits` 或配置 `EDGE_TTS_MODEL_PATH` 后复测 | pending |

## 4. 白盒观察摘要

| 项 | 观察结论 | 证据路径 |
|---|---|---|
| TTS 实际可听 | 未达开始条件 | `20_chain_quality/F-12_tts_vts_sync_preflight.md` |
| VTS 表情真实可见 | 未达开始条件 | `20_chain_quality/F-11_vts_expression_preflight.md` |
| VTS 口型同步真实可见 | 未达开始条件 | `20_chain_quality/F-12_tts_vts_sync_preflight.md` |
