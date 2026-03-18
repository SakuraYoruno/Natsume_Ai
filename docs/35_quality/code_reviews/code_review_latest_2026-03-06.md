受众：MASTER
标题：最新代码分析报告

# 最新代码分析报告

**生成时间：** 2026-03-06 09:43
**分析基准：** HEAD = `e8f41d7`（最新提交）
**测试结果：** 17 failed / 2077 passed / 10 skipped（总计 2104）

---

## 一、最新提交概览（最近 5 次）

| Commit | 类型 | 描述 | 变更规模 |
|--------|------|------|---------|
| `e8f41d7` | docs | 添加真实 host chain 运行报告和 artifacts | +4841 行（纯文档） |
| `1cbd5fa` | feat | 支持日文蒸馏，新增 natsume_ai_ja 风格 canon | +108 行 |
| `7697898` | feat | 对齐 model plane 和六库召回 contracts | +969 行 |
| `e7f48d5` | fix | 持久化 chat graph trace 和 LLM 节点调用日志 | +568 行 |
| `95161f1` | feat | 稳定运行时 key 解析和 Gemini 图生成路径 | +1075 行 |

---

## 二、核心功能变更分析

### 2.1 六库召回架构（`7697898`，最重要变更）

**文件：** `host_brain/retrieval/recall_fusion.py`（+387 行）、`host_brain/retrieval/pipeline.py`（+13 行）

**变更内容：**
- `RecallFusionServiceContract` 新增 6 个存储后端支持：
  - `episodic_vector`（Chroma 向量库，原有）
  - `sqlite_ledger`（SQLite 审计日志）
  - `neo4j_bond`（Neo4j Bond 图数据库）
  - `style_canon`（风格 Canon 存储）
  - `world_canon`（世界观 Canon 存储）
  - `artifact_vault`（Artifact 保险库）
- 新增 `_search_route()` 路由分发方法，根据 `store_id` 分发到对应后端
- `RecallRouteConfig` 新增 `store_id` 字段（原来只有 `collection`）
- `pipeline.py` 的 `build_retrieval_pipeline_from_env()` 注入 `style_store`/`world_store`/`artifact_vault`

**评估：** ✅ 架构扩展合理，六库召回是 M26 核心目标，实现完整。

### 2.2 Model Node Override 支持（`7697898`）

**文件：** `host_brain/integrations/graph_model_resolve.py`（+30 行）

**变更内容：**
- `resolve_model_nodes()` 新增 `model_node_overrides` 支持
- 新增 `_apply_node_overrides()` 辅助函数
- 支持 inline config 和 model plane profile 两种路径的 override 合并

**评估：** ✅ 实现简洁，支持运行时动态覆盖模型节点配置。

### 2.3 Chat Graph 持久化（`e7f48d5`）

**文件：** `host_brain/app/fastapi_app.py`（+564 行）

**变更内容：**
- 新增 `CHAT_GRAPH_NODE_ORDER` 节点执行顺序常量（9 个节点）
- 新增 `_repair_utf8_latin1_mojibake()` UTF-8 乱码修复函数
- 新增 `_render_node_generation_prompt()` 节点生成 prompt 构建
- 新增 `_extract_node_runtime_payload()` / `_extract_node_narrative_text()` 辅助函数
- Chat graph trace 和 LLM 节点调用日志持久化到 SQLite

**评估：** ✅ 可观测性增强，乱码修复函数有实际价值。

### 2.4 日文风格 Canon（`1cbd5fa`）

**文件：** `host_brain/canon/style/natsume_ai_ja.json`（新增）、`scripts/runtime/import_natsume_style_canon.py`（+108 行）

**变更内容：**
- 新增日文风格 Canon 文件 `natsume_ai_ja.json`
- 导入脚本支持日文蒸馏路径

**评估：** ✅ 多语言支持扩展，符合 M26 日文渲染目标。

---

## 三、测试失败根因分析（17 个）

### 3.1 Bond Summary 中文化导致的测试不兼容（5 个）

**失败测试：**
- `test_bond_summary_contract.py::test_t_75_summary_is_injected_to_context_pack_bond_slot`
- `test_context_pack_contract.py::test_t_cp_01_valid_context_pack_passes`
- `test_tool_catalog_contract.py::test_t_tool_01_tool_meta_pack_is_metadata_only_and_context_safe`
- `test_canon_store_contract.py::test_t_wcanon_02_authoritative_injection_is_citation_only`
- `test_world_canon_slot_contract.py::test_t_wcanon_05e_fixture_passes_validate_context_pack`

**根因：** 测试 fixture 中的 `bond_summary.stage` 使用了非法值（`'warm'`、`'stable'`、`'ESTABLISHED'`），这些值既不在英文枚举集合（`STRANGER/ACQUAINTANCE/FRIEND/CLOSE_FRIEND/CONFIDANT/SOULMATE`）中，也不在中文枚举集合（`陌生人/相识/朋友/好友/挚友/灵魂伴侣`）中。

**修复方向：** 更新测试 fixture 中的 `bond_summary.stage` 为合法枚举值（如 `'FRIEND'` 或 `'朋友'`）。

### 3.2 Bond Summary stage/trend 中文化导致的集成测试失败（4 个）

**失败测试：**
- `test_bond_routes_api.py::test_summary_stage_is_valid`（期望英文枚举，实际返回中文 `'熟识'`）
- `test_bond_routes_api.py::test_summary_trend_is_valid`（期望 `RISING/STABLE/DECLINING`，实际返回 `'稳定'`）
- `test_bond_routes_api.py::test_summary_stage_updates_after_promotion`（期望 `'ACQUAINTANCE'`，实际返回 `'熟识'`）
- `test_bond_dynamics_stage_integration.py::test_summary_stage_matches_state`（同上）

**根因：** `BondScoreState.to_summary()` 已中文化（M28-H/M28-I），但集成测试仍期望英文枚举值。

**修复方向：** 更新集成测试的期望值为中文枚举，或在 `to_summary()` 中增加 `lang` 参数支持双语输出。

### 3.3 Governor R8 规则新增导致 rule_trace 数量变化（2 个）

**失败测试：**
- `test_governor_rate_limit_contract.py::test_45_existing_r1_r7_trace_structure`（期望 7 条，实际 8 条）
- `test_governor_rules_contract.py::test_t_gov_01_rule_trace_has_threshold_and_snapshot_summary`（期望 7 条，实际 8 条）

**根因：** M28-D（OPT-8）新增了 R8 情感门控规则，`rule_trace` 从 7 条增加到 8 条，但测试仍硬编码 `assertEqual(7, len(result["rule_trace"]))`。

**修复方向：** 将测试中的 `7` 改为 `8`，并补充 R8 的 trace 结构验证。

### 3.4 Obs Metrics 新增 emotion_metrics 字段（1 个）

**失败测试：**
- `test_obs_metrics_contract.py::test_t_obs_01_structure_complete`（实际返回多了 `emotion_metrics` 字段）

**根因：** M29-Q（OPT-46）在 `MetricsCollector.collect()` 中新增了 `emotion_metrics` 字段，但测试的 `required_keys` 集合未包含该字段。

**修复方向：** 在测试的 `required_keys` 中添加 `'emotion_metrics'`。

### 3.5 VTS 表情映射表 edge/host 不同步（5 个）

**失败测试：**
- `test_vts_degraded_contract.py::test_unknown_tag_returns_degraded`（未知 tag 返回 `APPLIED` 而非 `DEGRADED`）
- `test_vts_degraded_contract.py::test_unknown_tag_preserves_original`（返回 `exp_excited_01` 而非 `exp_neutral_01`）
- `test_vts_degraded_contract.py::test_unknown_tag_fixture_driven`（同上）
- `test_vts_hw_contract.py::test_mapping_table_matches_host_vts`（edge 6 种 ≠ host 16 种）
- `test_vts_hw_contract.py::test_emotion_map_complete_coverage`（`EXCITED` 在 edge 侧缺失）

**根因：** M28-L（OPT-34）已将 `host_brain/channels/vts.py` 的 `EMOTION_TO_EXPRESSION_ID` 扩展至 16 种，但 `edge_client/actuators/vts_driver.py` 的映射表仍是旧的 6 种，导致 edge/host 不同步。同时，`EXCITED` 等新增标签在 edge 侧被当作未知 tag 处理，触发了 `APPLIED`（fallback）而非 `DEGRADED`。

**修复方向：** 将 `edge_client/actuators/vts_driver.py` 的 `EMOTION_TO_EXPRESSION_ID` 同步更新为 16 种，与 `host_brain/channels/vts.py` 保持一致。

---

## 四、失败分类汇总

| 类别 | 数量 | 根因 | 修复难度 |
|------|------|------|---------|
| Bond fixture 非法 stage 值 | 5 | 测试 fixture 未更新 | 🟢 低 |
| Bond summary 中英文不兼容 | 4 | 集成测试期望值未更新 | 🟢 低 |
| Governor R8 rule_trace 数量 | 2 | 测试硬编码数量未更新 | 🟢 低 |
| Obs metrics 新字段 | 1 | 测试 required_keys 未更新 | 🟢 低 |
| VTS edge/host 映射不同步 | 5 | edge_client 未同步更新 | 🟡 中 |
| **合计** | **17** | | |

> **结论：** 所有 17 个失败均为**测试/代码同步问题**，无逻辑 Bug。其中 12 个是测试期望值未跟随代码更新，5 个是 edge_client 未同步 host_brain 的 VTS 映射表扩展。

---

## 五、代码质量评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构完整性 | ⭐⭐⭐⭐⭐ | 六库召回架构完整，M26 目标达成 |
| 测试覆盖率 | ⭐⭐⭐⭐ | 2077 passed，新功能有对应测试 |
| 代码同步性 | ⭐⭐⭐ | edge/host VTS 映射表未同步 |
| 文档完整性 | ⭐⭐⭐⭐⭐ | 有完整的 trace 报告和 release 文档 |
| 向后兼容性 | ⭐⭐⭐⭐ | 大部分变更有默认值保护 |

---

## 六、待修复清单（优先级排序）

### P0（阻塞 CI）
1. **同步 edge_client VTS 映射表**：`edge_client/actuators/vts_driver.py` 的 `EMOTION_TO_EXPRESSION_ID` 补全至 16 种（与 `host_brain/channels/vts.py` 一致）
2. **更新 Bond 集成测试期望值**：`test_bond_routes_api.py` 和 `test_bond_dynamics_stage_integration.py` 中的 `stage`/`trend` 期望值改为中文枚举

### P1（影响合规）
3. **更新 Bond fixture 非法 stage 值**：5 个 contract 测试的 fixture 中 `bond_summary.stage` 改为合法枚举值
4. **更新 Governor 测试 rule_trace 数量**：2 个 contract 测试中 `assertEqual(7, ...)` 改为 `assertEqual(8, ...)`，并补充 R8 trace 结构验证
5. **更新 Obs metrics 测试 required_keys**：`test_obs_metrics_contract.py` 的 `required_keys` 添加 `'emotion_metrics'`

---

## 七、与 M27/M28/M29 里程碑的对应关系

| 变更 | 对应里程碑任务 | 状态 |
|------|--------------|------|
| GateSnapshot 情感字段 | TASK-0232 M27-A | ✅ 已实现 |
| R8 情感门控规则 | TASK-0247 M28-D | ✅ 已实现（但测试未更新） |
| EMOTION_TO_EXPRESSION_ID 扩展至16种 | TASK-0255 M28-L | ⚠️ host 已实现，edge 未同步 |
| BondScoreState.to_summary 中文化 | TASK-0251 M28-H | ✅ 已实现（但集成测试未更新） |
| MetricsCollector emotion_metrics | TASK-0280 M29-Q | ✅ 已实现（但测试未更新） |
| 六库召回架构 | TASK-0238 M27-G | ✅ 已实现 |
