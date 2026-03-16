# 封测后续执行（BATCH5 及以后）

**更新时间**：2026-03-15  
**前提**：BATCH4 中 F-17 已 PASS；F-09/F-14/F-15/F-16 为 BLOCK；已对 F-09 做代码修复。

---

## 1. 已完成的修复

- **F-09 情节记忆写入**：`observability_persist.py`、`main_chain.py` 已加固，确保 `emotion_tag` 至少为 `NEUTRAL`、`turn_count ≥ 2`、`_emotion_tags` 非空，避免 `EPISODIC_NO_QUALIFYING_TAGS` 导致无情节记忆可召回。
- **复测要求**：重启 Host 后，用与 F-09/F-16 相同的 5 条真实 query 再跑一轮质量样本，检查 `route_episodic` 是否有候选、回答是否更贴合个性化记忆。

---

## 2. 建议执行顺序

| 顺序 | 项 | 动作 | 产出 |
|------|---|------|------|
| 1 | **F-09/F-16 复测** | 用 5 条真实 query 再跑质量样本，核对 episodic 写入与召回 | `20_chain_quality/F-09_F-16_retest_*.json/.md` |
| 2 | **F-06 长稳 soak** | Host 连续运行 ≥6h，期间定期（如每 30min）记录 RSS/线程/句柄、偶发 chat 探活 | `10_health_runtime/F-06_soak_*.md` |
| 3 | **F-14 复验** | 确认 `get_context_compressor()` 已接线（`dependencies.py` 已有），长会话（≥8 轮）后检查 `metadata.context_compressor` 与 `/obs/context-compressor/stats` | 更新 F-14 结论 |
| 4 | **F-15** | 补阶段/会话总结正式调用链与产物后再测 | 待实现后复测 |
| 5 | **F-18～F-33 / F-34～F-52** | 按原计划与补充计划逐项执行 | 各模块证据目录 |

---

## 3. F-06 长稳 soak 操作要点

- **时长**：不少于 6 小时（封测计划 5.1）。
- **观察**：RSS、线程数、句柄数、错误率；期间可间歇性发 1～2 次 chat 探活。
- **通过标准**：无单调资源泄漏、无崩溃、无越跑越慢。
- **持续采集已就绪**：运行 `python tools/diag_f06_soak_collect.py --interval 30` 会在后台每 30 分钟采样 Host 进程 RSS 与 `/health`，追加到 `10_health_runtime/F-06_soak_samples_YYYYMMDD.json` 与同名的 `.md`。详见 `10_health_runtime/README_F-06_soak.md`。

---

## 4. 需人工/环境 vs 可脚本化，及微信实测

- **已人工测过**：F-10/F-11/F-12（上午在 Edge 电脑侧看 VTS 表情、听 TTS、观口型）。
- **仍需你手动操作**：F-04/F-05（重启、备份回滚）、F-23（Vision 电脑侧）、**F-24/F-40 微信**（含监听）、F-27（浏览器 WebUI）、F-29（与电脑侧观察互证）等。详见 **`00_index/MANUAL_VS_AUTO_AND_WECHAT.md`**。
- **微信**：**尚未实测**。真实测需本机运行 Edge 微信监听（`wechat_listener` + Hook 如 wcf）并收发明实微信消息；暂不接微信时可先用 `POST /v1/host/input`（source=WECHAT）做模拟实测，结论标 N/A 或「模拟通过」。

## 5. 当前结论

- F-17：**PASS**（含 CPU 嵌入/重排实测耗时报告）。
- F-09：已做写入路径修复，复测结论已落盘。
- F-14/F-15：仍为 BLOCK，待接线与实现后复测。
- F-06：长稳 soak 持续采集已启动（见 `10_health_runtime/README_F-06_soak.md`）。
