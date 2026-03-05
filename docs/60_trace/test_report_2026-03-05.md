# Natsume_Ai Host 端全量运测报告

**报告版本**：v1.0
**执行日期**：2026-03-05
**执行时间**：09:25 ~ 09:47（UTC+8）
**执行人**：CI / 配对编程 Agent
**基准提交**：`36bdc5c` chore(config): migrate to pure .env
**M20 提交**：`1284db2` feat M20: TASK-0200/0201/0202 心跳检测、压缩前保存、Episodic混合检索
**测试环境**：Windows 11 / Python 3.11.x / pytest 8.x

---

## 一、总体结果

| 层级 | 文件数 | 收集用例 | 通过 | 失败 | 跳过 | 耗时 |
|------|--------|---------|------|------|------|------|
| **Unit** | 36 | 519 | **519** ✅ | 0 | 0 | 1.48s |
| **Contract** | 101 | 985 | **975** | **10** ❌ | 0 | 26.94s |
| **Integration** | 9 | 111 | **109** ✅ | 0 | 2 ⏭️ | 0.74s |
| **E2E** | 8 | 46 | **45** | **1** ❌ | 0 | 0.78s |
| **合计** | **154** | **1661** | **1648** | **11** | **2** | **~27s** |

> **通过率：99.34%**（1648 / 1659 有效用例）

---

## 二、Unit 层详情（519 / 519 全绿 ✅）

### 2.1 模块覆盖清单

| 测试文件 | 用例数 | 覆盖模块 | 状态 |
|----------|--------|----------|------|
| `test_long_term_distiller.py` | 36 | LongTermDistiller 记忆蒸馏 | ✅ |
| `test_bond_stage_engine.py` | 33 | BondStageEngine 情感引擎 | ✅ |
| `test_asr_event_emitter.py` | 30 | ASR 事件发射器 | ✅ |
| `test_tts_playback_queue.py` | 23 | TTS 播放队列 | ✅ |
| `test_fallback_model_gate.py` | 22 | 降级模型门控 | ✅ |
| `test_context_compressor.py` | 22 | ContextCompressor（含 M20-B 新增 T-CTX-06~09） | ✅ |
| `test_vts_expression_map.py` | 21 | VTS 表情映射 | ✅ |
| `test_edge_resource_gate.py` | 20 | Edge 资源门控 | ✅ |
| `test_wechat_channel.py` | 19 | 微信渠道 | ✅ |
| `test_recall_fusion_hybrid.py` | 19 | **混合检索融合（M20-C 新增）** | ✅ |
| `test_agent_health_check.py` | 19 | **AgentHealthCheck 心跳（M20-A 新增）** | ✅ |
| `test_barge_in.py` | 18 | 打断检测 | ✅ |
| `test_tool_broker.py` | 17 | ToolBroker | ✅ |
| `test_skill_hot_loader.py` | 17 | Skill 热加载 | ✅ |
| `test_p1_followup.py` | 17 | P1 追问 | ✅ |
| `test_mcp_governance.py` | 17 | MCP 治理 | ✅ |
| `test_desktop_note_skill.py` | 17 | 桌面便签技能 | ✅ |
| `test_proactive_skill.py` | 13 | 主动发起技能 | ✅ |
| `test_scheduler_persistence.py` | 12 | 调度器持久化 | ✅ |
| `test_scheduler_engine.py` | 12 | 调度器引擎 | ✅ |
| `test_reranker_service.py` | 11 | 重排序服务 | ✅ |
| `test_focus_mode.py` | 11 | 专注模式 | ✅ |
| `test_dream_skill.py` | 11 | 梦境技能 | ✅ |
| `test_trace_node_builder.py` | 10 | Trace 节点构建 | ✅ |
| `test_diary_skill.py` | 10 | 日记技能 | ✅ |
| `test_canon_cursor_pagination.py` | 10 | Canon 游标分页 | ✅ |
| `test_memory_distillation.py` | 9 | 记忆蒸馏 | ✅ |
| `test_canon_routes_tx.py` | 9 | Canon 路由事务 | ✅ |
| `test_session_query.py` | 6 | 会话查询 | ✅ |
| `test_service_health_aggregator.py` | 6 | 服务健康聚合 | ✅ |
| `test_metrics_timeseries.py` | 6 | 指标时序 | ✅ |
| `test_hotwords_cache_ttl.py` | 5 | 热词缓存 TTL | ✅ |
| `test_obs_routes_trace_fallback.py` | 3 | OBS 路由 Trace 降级 | ✅ |
| `test_canon_hotwords.py` | 3 | Canon 热词 | ✅ |
| `test_asr_hotword_injection.py` | 3 | ASR 热词注入 | ✅ |
| `test_canon_vector_projection.py` | 2 | **Canon 向量投影（本次拉取新增）** | ✅ |

### 2.2 M20 专项用例明细

#### M20-A：AgentHealthCheck 心跳检测（19 用例）

| 用例 ID | 测试类 | 覆盖点 | 结果 |
|---------|--------|--------|------|
| T-HB-01 | `TestHeartbeatOkSilentDiscard` | OK 响应不写审计 | ✅ |
| T-HB-01b | `TestHeartbeatOkSilentDiscard` | OK 大小写不敏感 | ✅ |
| T-HB-02 | `TestHeartbeatAlertPush` | ALERT 响应写审计 | ✅ |
| T-HB-02b | `TestHeartbeatAlertPush` | 审计事件含原始响应 | ✅ |
| T-HB-03 | `TestHeartbeatLLMUnavailable` | LLM 异常触发 ALERT | ✅ |
| T-HB-03b | `TestHeartbeatLLMUnavailable` | 无 LLM caller 触发 ALERT | ✅ |
| T-HB-04a~h | `TestParseHeartbeatResponse` | 8 种响应变体解析 | ✅ |
| T-HB-05 | `TestListHealth` | list_health 返回任务 | ✅ |
| T-HB-05b | `TestListHealth` | 任务字段完整性 | ✅ |
| T-HB-06 | `TestResponseTruncation` | 长响应截断 | ✅ |
| T-HB-06b | `TestResponseTruncation` | 短响应不截断 | ✅ |

#### M20-B：ContextCompressor 压缩前静默保存（新增 4 用例，共 22）

| 用例 ID | 覆盖点 | 结果 |
|---------|--------|------|
| T-CTX-06 | summary 策略触发 pre_compress_save | ✅ |
| T-CTX-07 | hybrid 策略触发 pre_compress_save | ✅ |
| T-CTX-08 | sliding_window 策略不触发 pre_compress_save | ✅ |
| T-CTX-09 | pre_compress_save_enabled=False 时不触发 | ✅ |

#### M20-C：Episodic 混合检索增强（19 用例）

| 用例 ID | 覆盖点 | 结果 |
|---------|--------|------|
| T-RCL-HYB-01 | 纯向量召回（use_hybrid=False） | ✅ |
| T-RCL-HYB-02 | 混合召回权重融合（向量 70% + FTS5 30%） | ✅ |
| T-RCL-HYB-03 | FTS5 无结果时降级为纯向量 | ✅ |
| T-RCL-HYB-04 | 向量 + FTS5 结果去重合并 | ✅ |
| T-RCL-HYB-05 | 权重校验（vector_weight + fts_weight ≠ 1.0 报错） | ✅ |
| T-RCL-HYB-06 | FTS5 db_path 缺失时降级 | ✅ |
| + 13 边界/参数化用例 | 六库多路召回重排边界覆盖 | ✅ |

---

## 三、Contract 层详情（975 / 985，10 失败）

### 3.1 通过模块（101 个文件，975 用例）

以下模块全部通过（仅列出重点）：

| 模块 | 用例数 | 状态 |
|------|--------|------|
| `test_governor_rate_limit_contract.py` | 47 | ✅ |
| `test_scheduler_contract.py` | 43 | ✅ |
| `test_rpc_event_contract.py` | 36 | ✅ |
| `test_vts_degraded_contract.py` | 35 | ✅ |
| `test_asr_recovery_contract.py` | 35 | ✅ |
| `test_vts_reconnect_contract.py` | 31 | ✅ |
| `test_vts_hw_contract.py` | 30 | ✅ |
| `test_asr_hw_contract.py` | 28 | ✅ |
| `test_cross_timeline_contract.py` | 27 | ✅ |
| `test_startup_integration_contract.py` | 26 | ✅ |
| `test_retrieval_recall_fusion_contract.py` | 2 | ✅ |
| `test_retrieval_pipeline_contract.py` | 3 | ✅ |
| `test_episodic_contract.py` | 2 | ✅ |
| 其余 88 个文件 | ~624 | ✅ |

### 3.2 失败用例详情

#### 失败组 A：`test_model_plane_release_contract.py`（4 个）

**根因**：`configs/model_plane/model_profiles.yaml` 文件不存在。
该文件在本次拉取的配置迁移提交（`36bdc5c`）中被删除，迁移至 `.env` 管理，但 `release_manifest.py` 的 `_load_model_plane_meta()` 方法仍硬编码读取该路径，未做文件不存在的优雅降级处理。

| 用例 | 期望 | 实际 | 错误 |
|------|------|------|------|
| `test_checklist_includes_model_plane_digests` | 正常返回 checklist | `FileNotFoundError` | `model_profiles.yaml` 缺失 |
| `test_checklist_model_plane_has_checksum` | 含 checksum 字段 | `FileNotFoundError` | 同上 |
| `test_checklist_status_ready_when_consistent` | `STATUS_READY` | `FileNotFoundError` | 同上 |
| `test_model_plane_version_parseable` | `profiles_path.exists()` | `AssertionError: False` | 同上 |

**修复建议**：在 `release_manifest.py:_load_model_plane_meta()` 中添加文件存在性检查，文件不存在时返回 `None` 并记录 WARNING，而非抛出异常。

---

#### 失败组 B：`test_orchestration_graph_contract.py`（4 个）

**根因**：与失败组 A 同源。`model_profiles.yaml` 缺失导致 model_plane 节点加载失败，编排图状态级联降为 `FAILED`（期望 `OK` 或 `DEGRADED`）。
附加日志：`WAL activation failed: journal_mode returned MEMORY`（SQLite 内存模式下 WAL 不可用，属测试环境已知限制，非新增问题）。

| 用例 | 期望状态 | 实际状态 |
|------|----------|----------|
| `test_t_orch_02_model_plane_profile_loading_without_inline_model_nodes` | `STATUS_OK` | `FAILED` |
| `test_t_orch_04_profile_selector_is_deterministic_with_allowlist` | `OK` 或 `DEGRADED` | `FAILED` |
| `test_t_orch_05_model_node_carries_governance_refs` | `OK` 或 `DEGRADED` | `FAILED` |
| `test_t_orch_05_model_node_requires_approval_token` | `DEGRADED` | `FAILED` |

**修复建议**：同失败组 A，修复 `_load_model_plane_meta()` 后此组用例应自动恢复。

---

#### 失败组 C：`test_quality_contract.py`（1 个）

**根因**：`fastapi_app.py` 中本次拉取新增的 `panel_page` 路由函数缺少返回类型注解，quality contract 静态扫描失败。

| 用例 | 期望 | 实际 |
|------|------|------|
| `test_fastapi_panel_page_has_return_annotation` | `annotations` 含 `return` | `annotations == {}` |

**修复建议**：在 `fastapi_app.py` 的 `panel_page` 函数签名添加 `-> HTMLResponse`。

---

#### 失败组 D：`test_web_ui_contract.py`（1 个）

**根因**：前端构建产物（`index.html`）为 React CSR 空壳（`<div id="root"></div>`），实际内容由 JS 运行时渲染。Contract 测试假设 SSR 或静态内嵌关键词（`request_id` / `searchInput`），与当前 CSR 架构不符。

| 用例 | 期望 | 实际 |
|------|------|------|
| `test_panel_contains_key_elements` | HTML 含 `request_id` 或 `searchInput` | SPA 空壳，均不含 |

**修复建议**：更新 contract 测试，改为检测 `<div id="root">` 或 `<title>` 等 SPA 特征，或改为检测 JS bundle 文件存在性。

---

## 四、Integration 层详情（109 / 111，2 skipped ✅）

| 测试文件 | 用例数 | 通过 | 跳过 | 状态 |
|----------|--------|------|------|------|
| `test_bond_routes_api.py` | 18 | 18 | 0 | ✅ |
| `test_bond_dynamics_stage_integration.py` | 18 | 18 | 0 | ✅ |
| `test_obs_routes_integration.py` | 16 | 16 | 0 | ✅ |
| `test_tool_routes_api.py` | 15 | 15 | 0 | ✅ |
| `test_m4_e2e.py` | 12 | 12 | 0 | ✅ |
| `test_wechat_channel_audit.py` | 11 | 11 | 0 | ✅ |
| `test_mcp_audit_chain.py` | 10 | 10 | 0 | ✅ |
| `test_performance_budget.py` | 9 | 9 | 0 | ✅ |
| `test_rate_limit_redis_integration.py` | 2 | 0 | **2** ⏭️ | 需 Redis 服务 |

> **跳过说明**：`test_rate_limit_redis_integration.py` 的 2 个用例需要本地 Redis 服务，CI 环境未启动，属预期跳过。

---

## 五、E2E 层详情（45 / 46，1 失败）

| 测试文件 | 用例数 | 通过 | 失败 | 状态 |
|----------|--------|------|------|------|
| `test_wechat_vts_voice_e2e.py` | 19 | 19 | 0 | ✅ |
| `test_cold_start_e2e.py` | 5 | 5 | 0 | ✅ |
| `test_exec_chain_e2e.py` | 5 | 5 | 0 | ✅ |
| `test_fault_injection_e2e.py` | 7 | 7 | 0 | ✅ |
| `test_main_chain_e2e.py` | 6 | 6 | 0 | ✅ |
| `test_env_readiness_e2e.py` | 2 | 2 | 0 | ✅ |
| `test_wechat_vts_voice_rpc_e2e.py` | 1 | 1 | 0 | ✅ |
| `test_production_drill_e2e.py` | 1 | 0 | **1** ❌ | 见下 |

### E2E 失败详情

**用例**：`test_t_drill_02_five_orchestration_control_scenarios_are_stable`
**根因**：与 Contract 失败组 B 同源。E2E drill 套件内部运行编排控制场景，`model_profiles.yaml` 缺失导致 model_plane 节点失败，使 `failed_count` 从预期的 `2` 变为 `3`。

```
AssertionError: 2 != 3
# 期望 failed_count=2（已知的 2 个降级场景）
# 实际 failed_count=3（多出 model_plane 加载失败）
```

**修复建议**：同失败组 A，修复 `_load_model_plane_meta()` 后此用例应自动恢复。

---

## 六、性能基准

| 指标 | 数值 |
|------|------|
| 全量测试总耗时 | **27.05s** |
| 最慢单用例 | `test_metrics_chat_count_increments` 2.07s |
| Unit 层平均耗时 | ~2.8ms / 用例 |
| Contract 层平均耗时 | ~27ms / 用例 |
| Integration 层平均耗时 | ~6.7ms / 用例 |
| E2E 层平均耗时 | ~17ms / 用例 |

---

## 七、问题优先级汇总与修复路径

| 优先级 | 问题描述 | 影响用例数 | 根因文件 | 修复方案 |
|--------|----------|-----------|----------|----------|
| 🔴 **P1** | `model_profiles.yaml` 缺失 | **9 个**（4 contract + 4 contract + 1 e2e） | `release_manifest.py:_load_model_plane_meta()` | 添加文件存在性检查，缺失时返回 `None` + WARNING |
| 🟡 **P2** | `panel_page` 缺少返回类型注解 | 1 个 | `fastapi_app.py` | 函数签名加 `-> HTMLResponse` |
| 🟡 **P2** | Web UI contract 假设 SSR | 1 个 | `test_web_ui_contract.py` | 更新测试适配 CSR 架构 |
| ℹ️ **INFO** | SQLite WAL 内存模式警告 | 0（仅日志） | `sqlite_ledger.py:596` | 已知测试环境限制，无需修复 |
| ℹ️ **INFO** | Redis 跳过 2 个 | 2 个（预期跳过） | `test_rate_limit_redis_integration.py` | 需启动 Redis 服务 |
| ℹ️ **INFO** | `asyncio.get_event_loop()` DeprecationWarning | 0（仅警告） | `test_canon_cursor_pagination.py:188` | 建议改用 `asyncio.new_event_loop()` |

---

## 八、M20 里程碑验收结论

| 任务 | 描述 | 新增用例 | 验收状态 |
|------|------|----------|----------|
| TASK-0200 (M20-A) | AgentHealthCheck 心跳精神状态检测 | 19 | ✅ **通过** |
| TASK-0201 (M20-B) | ContextCompressor 压缩前静默保存 | 4 | ✅ **通过** |
| TASK-0202 (M20-C) | Episodic 混合检索增强（向量 70% + FTS5 30%） | 19 | ✅ **通过** |

**M20 全部 3 个子任务验收通过，新增 42 个专项用例，全部绿灯。**

---

## 九、结论

- **整体健康度**：✅ 良好，通过率 **99.34%**
- **M20 交付质量**：✅ 全部通过，无回归
- **当前阻塞项**：🔴 P1 问题（`model_profiles.yaml` 缺失）导致 9 个用例失败，根因为本次配置迁移引入的文件删除与代码读取路径不同步，建议在下一个 PR 中修复 `_load_model_plane_meta()` 的降级逻辑
- **非阻塞项**：P2 问题（类型注解 + Web UI contract）建议随下次迭代一并修复

---

## 十、Host 端全链路真实运行追踪报告

**追踪执行时间**：2026-03-05 10:13:06（UTC+8）
**追踪脚本**：`tools/trace_six_lib_recall.py`
**追踪目的**：验证 host 端所有服务节点的真实运行状态、六库多路召回链路完整性、FTS5 混合检索效果，以及各节点是否存在短路或异常。

---

### 10.1 节点 0：Host 端启动报告 & 服务健康聚合

| 指标 | 值 |
|------|-----|
| boot_status | **DEGRADED** |
| required_total | 14 |
| required_unavailable | **2** |
| optional_unavailable | 0 |
| startup_issues | 0 |

**服务目录（14 个）：**

| 服务 ID | Owner | Required | 状态 | 说明 |
|---------|-------|----------|------|------|
| `host.audit.sqlite_ledger` | host | ✅ | **UP** | 审计账本正常 |
| `host.governor.decision` | host | ✅ | **UP** | 决策引擎正常 |
| `host.skills.loader` | host | ✅ | **UP** | 技能加载器正常 |
| `host.integrations.llm_gateway` | host | ✅ | **DOWN** ❌ | LLM 网关不可用（无 API Key 配置） |
| `host.retrieval.embedding` | host | ✅ | **UP** | Embedding 服务正常 |
| `host.retrieval.reranker` | host | ✅ | **UP** | Reranker 服务正常 |
| `host.vision.screen_ocr` | host | ✅ | **UP** | 屏幕 OCR 正常 |
| `host.vision.camera_recognizer` | host | ✅ | **UP** | 摄像头识别正常 |
| `edge.rpc.gateway` | edge | ✅ | **UP** | Edge RPC 网关正常 |
| `edge.rpc.asr` | edge | ✅ | **UP** | ASR 服务正常 |
| `edge.rpc.tts` | edge | ✅ | **UP** | TTS 服务正常 |
| `edge.rpc.barge_in` | edge | ✅ | **UP** | 打断检测正常 |
| `edge.rpc.fallback_llm` | edge | ✅ | **DOWN** ❌ | Edge 降级 LLM 不可用（无本地模型） |
| `edge.rpc.resource_gate` | edge | ✅ | **UP** | 资源门控正常 |

> **DEGRADED 根因**：`host.integrations.llm_gateway` 和 `edge.rpc.fallback_llm` 在无 API Key / 无本地模型的测试环境下标记为 DOWN，属预期降级，不影响检索链路。

---

### 10.2 节点 1：六库 Chroma 集合初始化

| 集合名 | 向量维度 | 验证结果 |
|--------|---------|---------|
| `episodic` | 16 | ✅ 通过 |
| `canon_style` | 16 | ✅ 通过 |
| `canon_world` | 16 | ✅ 通过 |
| `long_term` | 16 | ✅ 通过 |
| `distilled` | 16 | ✅ 通过 |
| `bond` | 16 | ✅ 通过 |

六库全部初始化成功，`InMemoryChromaClient` 集合配置验证通过。

---

### 10.3 节点 2：六库记忆写入

| 集合 | 写入条数 | 内容类型 |
|------|---------|---------|
| `episodic` | 3 | 情节记忆（猫咪、工作压力、轻音乐） |
| `canon_style` | 2 | Canon 风格（说话风格、语气） |
| `canon_world` | 2 | Canon 世界观（价值观、情感连接） |
| `long_term` | 2 | 长期记忆（偏好、养猫记忆） |
| `distilled` | 2 | 蒸馏摘要（性格、AI 接受度） |
| `bond` | 2 | 情感记录（感谢、bond 阶段） |
| **合计** | **13** | — |

所有记忆使用 `hash-embed-v1` 确定性伪嵌入（SHA-256 → 16 维向量），写入成功。

---

### 10.4 节点 3：FTS5 全文检索器

| 指标 | 值 |
|------|-----|
| 索引写入条数 | 13 |
| Tokenizer | unicode61 |
| 验证查询 `猫咪` 命中数 | **0** ⚠️ |

> **⚠️ 发现问题**：FTS5 `unicode61` tokenizer 对中文字符的分词粒度为单字，查询 `猫咪`（2字）无法匹配索引中的 `猫咪`（因为 FTS5 MATCH 语法将 `猫咪` 视为一个 token 而非两个单字的组合）。这导致 `hybrid_fused=False`，所有候选的 `fts_score=0.0`，混合检索实际退化为纯向量检索。
>
> **影响**：当前 FTS5 对中文的支持依赖 unicode61 单字分词，查询词需要是单字才能命中。多字中文词组（如 `猫咪`、`工作压力`）无法通过 FTS5 MATCH 命中。
>
> **建议修复**（纳入 M21-C 追踪）：在 `FullTextSearcher.search()` 中对中文查询词进行字符级分割（`" ".join(list(query))`），或改用 `jieba` 分词后拼接 FTS5 查询语法。

---

### 10.5 节点 4：六路召回路由配置

| 路由 ID | 集合 | top_k | score_weight | node_selector |
|---------|------|-------|-------------|---------------|
| `route_episodic` | episodic | 3 | 1.00 | * |
| `route_canon_style` | canon_style | 2 | 0.90 | * |
| `route_canon_world` | canon_world | 2 | 0.85 | * |
| `route_long_term` | long_term | 2 | 0.80 | * |
| `route_distilled` | distilled | 2 | 0.75 | * |
| `route_bond` | bond | 2 | 0.70 | * |

六路路由全部配置成功，权重递减设计（episodic 最高 1.0，bond 最低 0.7）。

---

### 10.6 节点 6：六库多路召回真实执行结果

执行 3 次真实查询，每次均触发全部 6 路召回：

#### 查询 1：`用户喜欢猫咪，最近工作压力大，想聊聊放松的话题`

| 路由 | 集合 | 候选数 | 状态 |
|------|------|--------|------|
| route_bond | bond | 2 | ✅ OK |
| route_canon_style | canon_style | 2 | ✅ OK |
| route_canon_world | canon_world | 2 | ✅ OK |
| route_distilled | distilled | 2 | ✅ OK |
| route_episodic | episodic | 3 | ✅ OK |
| route_long_term | long_term | 2 | ✅ OK |

**召回耗时**：0.25ms | **融合候选总数**：13 | **路由错误**：0

**Top-5 融合候选（向量余弦相似度 × score_weight）：**

| 排名 | doc_id | final_score | 集合 | snippet |
|------|--------|-------------|------|---------|
| #1 | `mem_ep_003` | 0.883174 | episodic | 用户喜欢听轻音乐，特别是钢琴曲 |
| #2 | `mem_ep_002` | 0.836319 | episodic | 用户提到最近工作压力很大，需要放松 |
| #3 | `mem_ep_001` | 0.790947 | episodic | 今天和用户聊了很多关于猫咪的话题 |
| #4 | `mem_cs_002` | 0.768202 | canon_style | 夏目的语气：亲切自然，偶尔俏皮 |
| #5 | `mem_cs_001` | 0.734371 | canon_style | 夏目的说话风格：温柔、体贴，喜欢用比喻 |

#### 查询 2：`夏目的性格特点和说话风格`

**召回耗时**：0.13ms | **融合候选总数**：13 | **路由错误**：0

**Top-5 融合候选：**

| 排名 | doc_id | final_score | 集合 | snippet |
|------|--------|-------------|------|---------|
| #1 | `mem_ep_002` | 0.919711 | episodic | 用户提到最近工作压力很大，需要放松 |
| #2 | `mem_ep_003` | 0.915869 | episodic | 用户喜欢听轻音乐，特别是钢琴曲 |
| #3 | `mem_cs_002` | 0.794800 | canon_style | 夏目的语气：亲切自然，偶尔俏皮 |
| #4 | `mem_ep_001` | 0.760247 | episodic | 今天和用户聊了很多关于猫咪的话题 |
| #5 | `mem_cs_001` | 0.735647 | canon_style | 夏目的说话风格：温柔、体贴，喜欢用比喻 |

> **⚠️ 向量检索语义偏差**：查询 `夏目的性格特点和说话风格` 的 Top-1/2 命中了 episodic 库（`mem_ep_002/003`），而非 canon_style 库。根因是 `hash-embed-v1` 使用 SHA-256 确定性伪嵌入，不具备真实语义理解能力，向量相似度由字节分布决定而非语义。生产环境需替换为真实 OpenAI/本地 Embedding 模型。

#### 查询 3：`用户的长期记忆和情感状态`

**召回耗时**：0.20ms | **融合候选总数**：13 | **路由错误**：0

**Top-5 融合候选：**

| 排名 | doc_id | final_score | 集合 | snippet |
|------|--------|-------------|------|---------|
| #1 | `mem_ep_003` | 0.916481 | episodic | 用户喜欢听轻音乐，特别是钢琴曲 |
| #2 | `mem_ep_002` | 0.898431 | episodic | 用户提到最近工作压力很大，需要放松 |
| #3 | `mem_cs_002` | 0.824714 | canon_style | 夏目的语气：亲切自然，偶尔俏皮 |
| #4 | `mem_cs_001` | 0.805977 | canon_style | 夏目的说话风格：温柔、体贴，喜欢用比喻 |
| #5 | `mem_ep_001` | 0.771411 | episodic | 今天和用户聊了很多关于猫咪的话题 |

---

### 10.7 节点 7：Embedding 服务链路

| 指标 | 值 |
|------|-----|
| 状态 | **DEGRADED** ⚠️ |
| reason_code | `RETR_EMBED_UNAVAILABLE` |
| 向量维度 | 0（降级） |
| 耗时 | 0.03ms |

> **根因**：`EmbeddingServiceContract` 使用 `build_embedding_provider_registry(env={}, openai_api_key="")` 构建，无 OpenAI API Key，provider 不可用，触发降级。
>
> **影响**：Pipeline 中的 Embedding 节点降级，导致 Pipeline 整体 DEGRADED。但直接调用 `recall_service.recall()` 时传入预计算向量，不经过 EmbeddingServiceContract，因此六库召回本身不受影响。
>
> **结论**：Embedding 节点在无 API Key 的测试环境下预期降级，生产环境配置 `OPENAI_API_KEY` 后可恢复。

---

### 10.8 节点 8：Reranker 服务链路

| 指标 | 值 |
|------|-----|
| 状态 | **DEGRADED** ⚠️（降级但有输出） |
| reason_code | `RETR_RERANK_UNAVAILABLE` |
| 输入候选数 | 13 |
| 输出 top-k 数 | 5 |
| 耗时 | 0.07ms |

**Reranker Top-5（降级模式，按原始 score 排序）：**

| 排名 | doc_id | score | snippet |
|------|--------|-------|---------|
| #1 | `mem_ep_003` | 1.000000 | 用户喜欢听轻音乐，特别是钢琴曲 |
| #2 | `mem_ep_002` | 0.923077 | 用户提到最近工作压力很大，需要放松 |
| #3 | `mem_ep_001` | 0.846154 | 今天和用户聊了很多关于猫咪的话题 |
| #4 | `mem_cs_002` | 0.769231 | 夏目的语气：亲切自然，偶尔俏皮 |
| #5 | `mem_cs_001` | 0.692308 | 夏目的说话风格：温柔、体贴，喜欢用比喻 |

> **结论**：Reranker 在无真实 cross-encoder 模型时降级为按原始向量分数排序，输出结构完整，不短路。

---

### 10.9 节点 9：Evidence Pack 构建

| 指标 | 值 |
|------|-----|
| 状态 | **DEGRADED**（继承 Reranker 降级） |
| evidence_refs 数量 | 5 |
| serialization_version | `evidence_pack_v1` |
| 耗时 | 0.06ms |

**Evidence Refs（5 条）：**
- `evd://mem_ep_003/1`
- `evd://mem_ep_002/2`
- `evd://mem_ep_001/3`
- `evd://mem_cs_002/4`
- `evd://mem_cs_001/5`

> **结论**：Evidence Pack 节点正常构建，evidence_refs 格式符合 `evd://{doc_id}/{rank}` 规范，不短路。

---

### 10.10 节点 10：完整 Pipeline 端到端

| 查询 | Pipeline 状态 | reason_chain | 耗时 |
|------|--------------|-------------|------|
| 查询 1 | DEGRADED | `[RETR_EMBED_UNAVAILABLE, RETR_RECALL_INPUT_INVALID, RETR_RERANK_INPUT_INVALID, RETR_EPACK_INPUT_INVALID]` | 0.11ms |
| 查询 2 | DEGRADED | 同上 | 0.10ms |
| 查询 3 | DEGRADED | 同上 | 0.09ms |

> **根因**：Pipeline 中 Embedding 节点降级（无 API Key）→ 输出空向量 → Recall 节点收到空向量触发 `RETR_RECALL_INPUT_INVALID` → 级联降级。
>
> **重要说明**：Pipeline 的 DEGRADED 是由 Embedding 配置缺失引起的级联降级，**不是链路短路**。直接调用 `recall_service.recall()` 传入预计算向量时，六库召回链路完全正常（3 次查询均 OK，13 候选，0 路由错误）。

---

### 10.11 节点 11：审计链路（SQLite Ledger）

| op_kind | 事件数 |
|---------|--------|
| EMBEDDING | 4 |
| RECALL | 7 |
| RERANK | 4 |
| EVIDENCE_PACK | 4 |
| PIPELINE | 3 |
| **合计** | **22** |

| status | 事件数 |
|--------|--------|
| OK | 4 |
| DEGRADED | 18 |

> **结论**：Ledger 审计链路完整，所有 22 个检索事件均已写入 `retrieval_event_log`，无事件丢失。OK 的 4 条均为直接调用 `recall_service.recall()` 的成功事件。

---

### 10.12 节点 12：链路完整性检查 & 短路节点检测

| 检查项 | 结果 |
|--------|------|
| 六路召回路由全部 OK | ✅ 通过（6/6 路由正常，0 路由错误） |
| Embedding 节点 | ❌ **DEGRADED**（无 API Key，预期降级） |
| Reranker 节点 | ✅ 正常（降级但有输出，不短路） |
| Evidence Pack 节点 | ✅ 正常（降级但有输出，不短路） |
| Ledger 审计链路 | ✅ 正常（22 条事件） |
| FTS5 中文检索 | ⚠️ **警告**（unicode61 tokenizer 无法匹配中文词组） |

---

### 10.13 链路问题汇总与分析

#### 问题 1（P1-ENV）：Embedding 节点降级
- **现象**：`EmbeddingServiceContract` 状态 DEGRADED，reason_code=`RETR_EMBED_UNAVAILABLE`
- **根因**：测试环境无 `OPENAI_API_KEY`，provider 不可用
- **影响范围**：Pipeline 端到端链路级联降级；直接调用 recall 不受影响
- **是否短路**：**否**，降级有输出，链路不中断
- **修复方案**：生产环境配置 `OPENAI_API_KEY` 或本地 Embedding 模型

#### 问题 2（P2-FTS）：FTS5 中文词组检索失效
- **现象**：FTS5 查询 `猫咪` 命中 0 条，所有候选 `hybrid_fused=False`，`fts_score=0.0`
- **根因**：`unicode61` tokenizer 按单字分词，`猫咪` 作为整体 token 无法匹配索引中的单字
- **影响范围**：混合检索（向量 70% + FTS5 30%）实际退化为纯向量检索，FTS5 权重未生效
- **是否短路**：**否**，降级为纯向量检索，链路不中断
- **修复建议**：在 `FullTextSearcher.search()` 中对中文查询进行字符级分割，或引入 `jieba` 分词

#### 问题 3（INFO）：向量检索语义偏差
- **现象**：`hash-embed-v1` 伪嵌入导致语义查询结果与预期不符（如查询 `夏目的性格` 命中 episodic 而非 canon_style）
- **根因**：SHA-256 确定性伪嵌入不具备语义理解，仅用于测试环境
- **影响范围**：测试环境召回质量，不影响生产
- **修复方案**：生产环境替换为真实 Embedding 模型（OpenAI text-embedding-3-small 等）

#### 问题 4（INFO）：SQLite WAL 模式警告
- **现象**：`WAL activation failed: journal_mode returned MEMORY, expected WAL`
- **根因**：内存数据库（`:memory:`）不支持 WAL 模式，属测试环境已知限制
- **影响范围**：无，仅日志警告
- **修复方案**：无需修复，生产环境使用文件路径数据库时 WAL 正常

---

### 10.14 六库多路召回链路完整性结论

```
六库召回链路图：

  查询输入
    │
    ▼
  [Embedding]  ← 测试环境 DEGRADED（无 API Key）
    │ 空向量（降级）
    ▼
  [RecallFusionServiceContract]
    ├── route_episodic    → episodic    (3 candidates) ✅
    ├── route_canon_style → canon_style (2 candidates) ✅
    ├── route_canon_world → canon_world (2 candidates) ✅
    ├── route_long_term   → long_term   (2 candidates) ✅
    ├── route_distilled   → distilled   (2 candidates) ✅
    └── route_bond        → bond        (2 candidates) ✅
    │ 融合 13 候选（向量余弦相似度 × score_weight）
    ▼
  [FTS5 HybridFusion]  ← 中文词组匹配失效，退化为纯向量
    │ 13 候选（fts_score=0.0）
    ▼
  [Reranker]  ← 无真实模型，降级排序（有输出）
    │ top-5 候选
    ▼
  [EvidencePack]  ← 正常构建
    │ 5 evidence_refs
    ▼
  [Ledger 审计]  ← 22 条事件全部写入 ✅
```

**结论**：
- ✅ **六库多路召回链路完整，无节点短路**
- ✅ **六路路由全部正常，0 路由错误，13 候选融合成功**
- ✅ **Ledger 审计链路完整，22 条事件无丢失**
- ⚠️ **FTS5 中文词组检索失效**（unicode61 tokenizer 限制，混合检索退化为纯向量，纳入 M21-C 修复）
- ⚠️ **Embedding/Reranker 在测试环境降级**（无 API Key，预期行为，不短路）
- ℹ️ **Pipeline 级联降级**由 Embedding 配置缺失引起，非链路设计问题

---

## 十一、Qwen3 Embedding / Reranker 适配变更留痕

**变更时间**：2026-03-05 11:20（UTC+8）
**变更类型**：功能增强 — 本地推理模型适配
**触发背景**：Host 端 GPU 升级至 RTX 5080 16G，具备本地运行 0.6B 量级模型的能力，决策切换至 Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B 方案，替代原 OpenAI API Embedding + heuristic/cross_encoder 方案。

---

### 11.1 变更文件清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `host_brain/retrieval/reranker.py` | 功能新增 | 新增 `qwen3_reranker` provider 及注册逻辑 |
| `.env.example` | 配置更新 | 更新 embedding/reranker/chroma 相关配置示例 |

---

### 11.2 `reranker.py` 变更详情

#### 11.2.1 新增 provider 注册（`build_reranker_provider_registry`）

```python
# 变更前
registry["cross_encoder"] = _build_cross_encoder_provider(device=device)

# 变更后（追加一行）
registry["cross_encoder"] = _build_cross_encoder_provider(device=device)
registry["qwen3_reranker"] = _build_qwen3_reranker_provider(device=device)
```

`qwen3_reranker` 与 `cross_encoder` 并列注册，通过 `RETR_RERANK_ROUTES_JSON` 中的 `provider` 字段选择。

#### 11.2.2 新增 `_build_qwen3_reranker_provider` 实现

**架构差异**：Qwen3-Reranker 是 **CausalLM 生成式重排**，不能使用 `CrossEncoder` 接口，需要独立实现。

| 对比项 | `cross_encoder` provider | `qwen3_reranker` provider |
|--------|--------------------------|---------------------------|
| 模型类型 | `CrossEncoder`（BERT-like） | `AutoModelForCausalLM`（Qwen3） |
| 推理方式 | `model.predict(pairs)` | 取最后 token 的 yes/no logit |
| 分数来源 | sigmoid(logit) | softmax([yes_logit, no_logit])[0] |
| Prompt 格式 | 无 | 官方 system + user + `<think>\n\n</think>` |
| 精度 | float32 | float16（GPU）/ float32（CPU） |
| 批量推理 | ✅ | ✅（左填充，统一长度） |
| 模型缓存 | ✅ | ✅（首次加载后复用） |

**Prompt 模板**（官方格式）：
```
<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Candidate Document,
output your judgement, the answer should be "yes" or "no".<|im_end|>
<|im_start|>user
Query: {query}
Candidate Document: {doc}<|im_end|>
<|im_start|>assistant
<think>

</think>

```

**分数计算**：
```python
last_logits = outputs.logits[:, -1, :]          # 最后一个 token 的 logits
yes_logits  = last_logits[:, yes_token_id]       # "yes" token 的 logit
no_logits   = last_logits[:, no_token_id]        # "no" token 的 logit
scores = softmax([yes_logits, no_logits], dim=-1)[:, 0]  # yes 的概率 → 相关性分数
```

**Token ID 兼容处理**：
```python
yes_token_id = tokenizer.convert_tokens_to_ids("yes")
no_token_id  = tokenizer.convert_tokens_to_ids("no")
# 若为 unk（部分版本），回退到大写
if yes_token_id == tokenizer.unk_token_id:
    yes_token_id = tokenizer.convert_tokens_to_ids("Yes")
    no_token_id  = tokenizer.convert_tokens_to_ids("No")
```

**降级兼容**：`qwen3_reranker` 抛出异常时，`RerankerServiceContract` 的 `except Exception` 分支捕获，自动降级为 fallback 排序，不短路链路。

---

### 11.3 `.env.example` 配置变更详情

#### Chroma Collection 向量维度

| 配置项 | 变更前 | 变更后 | 说明 |
|--------|--------|--------|------|
| `CHROMA_COLLECTION_PROFILES_JSON` | `{"episodic":384,"canon":384}` | `{"episodic":1024,"canon":1024}` | Qwen3-Embedding-0.6B 输出维度为 1024 |

#### Embedding 路由

| 配置项 | 变更前 | 变更后 | 说明 |
|--------|--------|--------|------|
| `RETR_EMBED_ROUTES_JSON` model_id | `sentence-transformers/all-MiniLM-L6-v2` | `Qwen/Qwen3-Embedding-0.6B` | 切换至 Qwen3 |
| `RETR_EMBED_ROUTES_JSON` dim | `384` | `1024` | 对应维度更新 |
| `RETR_EMBED_DEVICE` | `cpu` | `cuda` | RTX 5080 16G GPU 推理 |
| `RETR_EMBED_DIM`（回退值） | `16` | `1024` | 回退路径维度同步 |

#### Reranker 路由

| 配置项 | 变更前 | 变更后 | 说明 |
|--------|--------|--------|------|
| `RETR_RERANK_ROUTES_JSON` provider | `cross_encoder` | `qwen3_reranker` | 切换至生成式重排 |
| `RETR_RERANK_ROUTES_JSON` model_id | `BAAI/bge-reranker-base` | `Qwen/Qwen3-Reranker-0.6B` | 切换至 Qwen3 |
| `RETR_RERANK_ROUTES_JSON` timeout_ms | `90000` | `30000` | RTX 5080 推理速度更快，缩短超时 |
| `RETR_RERANK_DEVICE` | `cpu` | `cuda` | RTX 5080 16G GPU 推理 |

---

### 11.4 模型性能预估（RTX 5080 16G）

| 模型 | 参数量 | 精度 | 预估推理延迟 | 显存占用 |
|------|--------|------|-------------|---------|
| Qwen3-Embedding-0.6B | 0.6B | float16 | ~10–30ms / 条 | ~1.2 GB |
| Qwen3-Reranker-0.6B | 0.6B | float16 | ~50–200ms / 批（20候选） | ~1.2 GB |
| **合计** | — | — | — | **~2.4 GB** |

> RTX 5080 16G 显存充裕，两个模型同时加载后仍剩余约 13.6 GB，不影响主 LLM 推理。

---

### 11.5 落地前置条件

在 `.env` 中将 `model_id` 改为本地路径后，需执行以下操作：

```bash
# 1. 下载模型
huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir models/Qwen3-Embedding-0.6B
huggingface-cli download Qwen/Qwen3-Reranker-0.6B  --local-dir models/Qwen3-Reranker-0.6B

# 2. 更新 .env（使用本地路径）
# RETR_EMBED_ROUTES_JSON={"host_default":{"provider":"sentence_transformers","model_id":"models/Qwen3-Embedding-0.6B","dim":1024,...}}
# RETR_RERANK_ROUTES_JSON={"host_default":{"provider":"qwen3_reranker","model_id":"models/Qwen3-Reranker-0.6B","top_k":5,...}}

# 3. 重建 ChromaDB collections（维度从旧值变为 1024，旧数据不兼容）
# 手动删除 chroma_data/ 目录后重启，或运行重置脚本
```

> ⚠️ **重要**：切换 Embedding 模型后，ChromaDB 中已有的 collection 必须删除重建，因为向量维度已变更，旧数据与新模型不兼容。

---

### 11.6 变更影响评估

| 影响项 | 评估 |
|--------|------|
| 现有单元测试 | ✅ 无影响（`qwen3_reranker` 在无 torch/transformers 时抛出 RuntimeError，被 contract 降级捕获，测试用 mock provider 不受影响） |
| 现有 contract 测试 | ✅ 无影响（`build_reranker_provider_registry` 新增注册不破坏现有 `heuristic`/`cross_encoder` 路由） |
| 现有 integration 测试 | ✅ 无影响（测试环境不加载真实模型） |
| 生产 ChromaDB 数据 | ⚠️ **需重建**（维度变更，旧数据不兼容） |
| 生产 Embedding 延迟 | ✅ 改善（本地 GPU 推理 vs 网络 API 调用） |
| 生产 Reranker 质量 | ✅ 改善（Qwen3 生成式重排 vs heuristic 词频重排） |

---

### 11.7 提交信息

```
feat(retrieval): add qwen3_reranker provider for Qwen3-Reranker-0.6B

- Add _build_qwen3_reranker_provider() in reranker.py
  - Uses AutoModelForCausalLM (CausalLM, not CrossEncoder)
  - Official prompt template: system + user + <think></think>
  - Score = softmax([yes_logit, no_logit])[0]
  - float16 on CUDA, float32 on CPU
  - Batch inference with left-padding
  - Model cache (load once, reuse)
  - unk_token fallback for yes/no token id

- Register qwen3_reranker in build_reranker_provider_registry()

- Update .env.example:
  - CHROMA_COLLECTION_PROFILES_JSON: dim 384 → 1024
  - RETR_EMBED_ROUTES_JSON: all-MiniLM-L6-v2 → Qwen3-Embedding-0.6B, dim 384 → 1024
  - RETR_EMBED_DEVICE: cpu → cuda
  - RETR_EMBED_DIM: 16 → 1024
  - RETR_RERANK_ROUTES_JSON: cross_encoder/bge-reranker-base → qwen3_reranker/Qwen3-Reranker-0.6B
  - RETR_RERANK_ROUTES_JSON timeout_ms: 90000 → 30000
  - RETR_RERANK_DEVICE: cpu → cuda

Target hardware: RTX 5080 16G
Estimated VRAM: ~2.4 GB (both models loaded simultaneously)
```
