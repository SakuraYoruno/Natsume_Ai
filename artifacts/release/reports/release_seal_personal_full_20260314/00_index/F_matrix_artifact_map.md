# F-01 ~ F-33 产物归档对照表

适用目录：`artifacts/release/reports/release_seal_personal_full_20260314/`

使用规则：

1. 每个 `F-xx` 至少保留一份系统侧证据和一份结论摘要。
2. 涉及白盒观测的项，必须额外保留电脑侧截图、录屏、音频或观察记录。
3. 推荐文件名前缀统一为 `F-xx_`，后接主题和时间戳。
4. 如果同一项多次复测，使用 `retry1`、`retry2` 或绝对时间区分。

## 1. 健康、恢复、稳定性

| ID | 子目录 | 建议文件名 | 必备证据 |
|---|---|---|---|
| F-01 | `10_health_runtime/` | `F-01_health_summary.md` `F-01_health_snapshot.json` | `/health` `/ready` `/v1/edge/health` 响应、VTS 连接状态、结论 |
| F-02 | `10_health_runtime/` | `F-02_config_keys.md` `F-02_config_snapshot.json` | 启动日志、配置快照、关键密钥引用检查、拒绝样本 |
| F-03 | `10_health_runtime/` | `F-03_dependency_readiness.md` `F-03_dependency_probe.json` | Redis/Neo4j/Chroma/模型连通结果、失败说明 |
| F-04 | `10_health_runtime/` | `F-04_restart_recovery.md` `F-04_restart_timeline.json` | 重启前后时间线、恢复时延、恢复后功能验证 |
| F-05 | `50_recovery_rollback/` | `F-05_backup_restore.md` `F-05_restore_check.json` | 备份记录、恢复后计数、启动结果、最小回滚结果 |
| F-06 | `10_health_runtime/` | `F-06_soak_summary.md` `F-06_resource_curve.json` | 6h soak 记录、RSS/线程/句柄/错误率曲线 |
| F-07 | `10_health_runtime/` | `F-07_failure_reason_map.md` `F-07_failure_cases.json` | `reason_code`、失败样本、恢复动作、复测结果 |

## 2. 主链路、质量与时延

| ID | 子目录 | 建议文件名 | 必备证据 |
|---|---|---|---|
| F-08 | `20_chain_quality/` | `F-08_chat_chain.md` `F-08_chat_chain_trace.json` | chat 主链路请求、trace、最终回答、结论 |
| F-09 | `20_chain_quality/` | `F-09_recall_quality.md` `F-09_recall_samples.json` | query、top-k 召回、最终引用、准确性评分 |
| F-10 | `20_chain_quality/` | `F-10_emotion_chain.md` `F-10_emotion_chain_trace.json` | 情感字段流转、系统侧 trace、电脑侧白盒观察记录 |
| F-11 | `20_chain_quality/` | `F-11_vts_expression.md` `F-11_vts_expression.mp4` | 表情映射、VTS 事件序列、电脑侧表情变化录屏或截图 |
| F-12 | `20_chain_quality/` | `F-12_tts_vts_sync.md` `F-12_tts_vts_sync.mp4` `F-12_tts_timeline.json` | 音频时间点、VTS 时间点、电脑侧可听可见证据 |
| F-13 | `20_chain_quality/` | `F-13_edge_backfill.md` `F-13_edge_backfill.json` | 中断、重试、回补、恢复后审计链 |
| F-14 | `20_chain_quality/` | `F-14_context_compress.md` `F-14_context_compress.json` | 压缩前后对比、保真检查、后续问答结果 |
| F-15 | `20_chain_quality/` | `F-15_summary_quality.md` `F-15_summary_samples.json` | 阶段总结、会话总结、覆盖率与臆造检查 |
| F-16 | `20_chain_quality/` | `F-16_text_quality.md` `F-16_text_quality_samples.json` | query 集、输出文本、评分、严重错误清单 |
| F-17 | `20_chain_quality/` | `F-17_chat_latency.md` `F-17_chat_latency.json` | `first_token/commit/tts_start/vts_start` 阶段时延 |
| F-18 | `20_chain_quality/` | `F-18_scene_matrix.md` `F-18_scene_matrix.json` | `CHAT/WORK_CODE/WORK_TRANSLATE/EXEC_INTENT/VISION_UI` 成功链与时延 |

## 3. 模块级能力矩阵

| ID | 子目录 | 建议文件名 | 必备证据 |
|---|---|---|---|
| F-19 | `30_module_matrix/` | `F-19_agent_checkpoint.md` `F-19_agent_checkpoint.json` | run、checkpoint、resume、finish 记录 |
| F-20 | `30_module_matrix/` | `F-20_tools_skills_mcp.md` `F-20_tools_skills_mcp.json` | 常用 tools、skills、MCP provider 实跑结果 |
| F-21 | `30_module_matrix/` | `F-21_canon.md` `F-21_canon.json` | 热词注入、proposal approve/rollback、召回验证 |
| F-22 | `30_module_matrix/` | `F-22_bond_summary.md` `F-22_bond_summary.json` | bond event、summary 注入、答案一致性 |
| F-23 | `30_module_matrix/` | `F-23_vision_automation.md` `F-23_vision_automation.mp4` | screenshot、plan、automate、电脑侧动作变化 |
| F-24 | `30_module_matrix/` | `F-24_wechat.md` `F-24_wechat.json` | callback、dispatch、reply、去重与丢失检查 |
| F-25 | `30_module_matrix/` | `F-25_scheduler_proactive.md` `F-25_scheduler_proactive.json` | create/enable/disable/trigger、push/pull 记录 |
| F-26 | `30_module_matrix/` | `F-26_distillation_vault_recall.md` `F-26_distillation_vault_recall.json` | 写入、蒸馏、归档、召回、过期清理结果 |

## 4. WebUI、Obs 与审计

| ID | 子目录 | 建议文件名 | 必备证据 |
|---|---|---|---|
| F-27 | `40_webui_obs/` | `F-27_webui_pages.md` `F-27_webui_pages.png` `F-27_webui_pages.har` | 页面截图、控制台、HAR、交互结论 |
| F-28 | `40_webui_obs/` | `F-28_obs_field_map.md` `F-28_obs_field_map.json` | `/obs/*`、`/api/ui/*`、字段来源核对 |
| F-29 | `40_webui_obs/` | `F-29_audit_trace.md` `F-29_audit_trace.json` | `request_id` 串联的日志、trace、电脑侧互证结果 |

## 5. 安全、fallback 与隔离

| ID | 子目录 | 建议文件名 | 必备证据 |
|---|---|---|---|
| F-30 | `60_security_isolation/` | `F-30_security.md` `F-30_security_cases.json` | HMAC/replay/auth/token/rate-limit/未授权工具拒绝样本 |
| F-31 | `60_security_isolation/` | `F-31_provider_fallback.md` `F-31_provider_fallback.json` | provider 成功、失败、降级、恢复证据 |
| F-32 | `60_security_isolation/` | `F-32_session_isolation.md` `F-32_session_isolation.json` | 多会话交错样本、串号检查、结论 |
| F-33 | `60_security_isolation/` | `F-33_endpoint_coverage.md` `F-33_endpoint_coverage.json` | 已启用关键端点清单、逐项核验记录 |

## 6. 原始工件落位建议

原始工件统一放 `70_raw_evidence/`，并按下列子主题命名：

1. `70_raw_evidence/F-10_*`：情感链原始 trace、日志片段。
2. `70_raw_evidence/F-11_*`：VTS 表情截图、录屏。
3. `70_raw_evidence/F-12_*`：TTS 音频、录屏、时间点导出。
4. `70_raw_evidence/F-23_*`：Vision 自动化录屏、截图。
5. `70_raw_evidence/F-27_*`：WebUI 原始截图、HAR、控制台导出。
6. `70_raw_evidence/F-29_*`：原始 trace、审计导出。

## 7. 最终结论文件

最终至少输出以下两个文件：

1. `00_index/release_seal_execution_report_YYYYMMDD.md`
2. `00_index/release_seal_execution_report_YYYYMMDD.json`

建议再保留一份总目录索引：

`00_index/release_seal_manifest_YYYYMMDD.md`
