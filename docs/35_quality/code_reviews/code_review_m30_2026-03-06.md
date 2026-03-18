受众：MASTER
标题：M30 代码审查报告

# M30 代码审查报告

**里程碑：** M30 — 测试同步修复（Test Sync Hotfix）
**生成时间：** 2026-03-06 10:18
**测试结果：** 0 failed / 2094 passed / 10 skipped（修复前：17 failed / 2077 passed）
**状态：** ✅ DONE

---

## 一、修复背景

最新代码拉取后（HEAD = `e8f41d7`），全量测试暴露 **17 个失败**，全部为测试/代码同步问题，无逻辑 Bug。根因分为 5 类：

| 类别 | 数量 | 根因 |
|------|------|------|
| Bond fixture 非法 stage/trend 值 | 5 | fixture 未随 M28-H 中文化更新 |
| Bond 集成测试中英文枚举不兼容 | 4 | 集成测试期望英文，`to_summary()` 返回中文 |
| Governor R8 rule_trace 数量变化 | 2 | R8 规则加入后 trace 从 7 条变 8 条 |
| Obs metrics 新字段 | 1 | `emotion_metrics` 字段新增，测试未更新 |
| VTS edge/host 映射表不同步 | 5 | edge_client 仍是旧的 6 种，host 已扩展至 16 种 |

---

## 二、修复详情

### TASK-0283 M30-A：Bond fixture 非法 stage/trend 值（5 个失败）

**修复文件：**

| 文件 | 修改内容 |
|------|---------|
| `tests/fixtures/context_pack/good/minimal_valid.json` | `stage: "warm"→"STRANGER"`, `trend: "up"→"稳定"` |
| `tests/fixtures/canon/world/context_pack_with_world_canon.json` | `stage: "ESTABLISHED"→"STRANGER"` |
| `tests/fixtures/llm_gateway/good/prompt_payload.json` | `stage: "warm"→"STRANGER"`, `trend: "up"→"稳定"` |
| `tests/fixtures/bond/good/bond_event_for_summary.json` | `stage: "warm"→"STRANGER"`, `trend: "up"→"稳定"` |
| `tests/contract/test_bond_summary_contract.py` | `test_t_74` 期望值同步更新 |

**根因：** `context_pack.py` 的 `_validate_bond_summary_slot()` 校验 `stage` 必须在 `_VALID_STAGES_CN ∪ _VALID_STAGES_EN` 中，`"warm"/"stable"/"ESTABLISHED"` 均不合法。

---

### TASK-0284 M30-B：Bond 集成测试中英文枚举不兼容（4 个失败）

**修复文件：**

| 文件 | 修改内容 |
|------|---------|
| `tests/integration/test_bond_routes_api.py` | `test_summary_stage_is_valid`：改为中文枚举集合校验 |
| `tests/integration/test_bond_routes_api.py` | `test_summary_trend_is_valid`：`("RISING","STABLE","DECLINING")` → 中文趋势集合 |
| `tests/integration/test_bond_routes_api.py` | `test_summary_stage_updates_after_promotion`：`STAGE_ACQUAINTANCE` → `"相识"` |
| `tests/integration/test_bond_dynamics_stage_integration.py` | `test_summary_stage_matches_state`：`STAGE_ACQUAINTANCE` → `"相识"` |

**根因：** M28-H（OPT-21）将 `BondScoreState.to_summary()` 全面中文化，`ACQUAINTANCE→"相识"`，`RISING→"上升"`，`STABLE→"稳定"`，`DECLINING→"下降"`。

---

### TASK-0285 M30-C：Governor rule_trace 数量硬编码（2 个失败）

**修复文件：**

| 文件 | 修改内容 |
|------|---------|
| `tests/contract/test_governor_rate_limit_contract.py` | `assertEqual(7, len(rule_trace))` → `assertEqual(8, ...)` |
| `tests/contract/test_governor_rules_contract.py` | `assertEqual(7, len(rule_trace))` → `assertEqual(8, ...)` |

**根因：** M28-D（OPT-8）新增 R8 情感门控规则，`evaluate_governor_gates()` 的 `rule_trace` 从 7 条增加到 8 条。

---

### TASK-0286 M30-D：Obs metrics required_keys 缺少 emotion_metrics（1 个失败）

**修复文件：**

| 文件 | 修改内容 |
|------|---------|
| `tests/contract/test_obs_metrics_contract.py` | `required_keys` 添加 `'emotion_metrics'` |

**根因：** M29-Q（OPT-46）在 `MetricsCollector.collect()` 中新增了 `emotion_metrics` 字段，测试的 `required_keys` 集合未包含该字段。

---

### TASK-0287 M30-E：edge_client VTS 映射表同步（5 个失败）

**修复文件：**

| 文件 | 修改内容 |
|------|---------|
| `edge_client/actuators/vts_driver.py` | `EMOTION_TO_EXPRESSION_ID` 从 6 种扩展至 16 种（新增 EXCITED/CALM/FRUSTRATED/ANXIOUS/LOVING/EMBARRASSED/CONFUSED/BORED/PROUD/GRATEFUL） |
| `tests/contract/test_vts_reconnect_contract.py` | `len == 6` → `len == 16` |
| `tests/contract/test_vts_degraded_contract.py` | 未知 tag 从 `CONFUSED`/`excited` 改为 `SLEEPY`/`sleepy` |
| `tests/fixtures/channels/vts_unknown_tag_degraded.json` | `emotion_tag: "CONFUSED"→"SLEEPY"` |

**根因：** M28-L（OPT-34）已将 `host_brain/channels/vts.py` 的 `EMOTION_TO_EXPRESSION_ID` 扩展至 16 种，但 `edge_client/actuators/vts_driver.py` 的映射表仍是旧的 6 种，导致 `EXCITED`/`CONFUSED` 等新增标签在 edge 侧被当作未知 tag 处理。

---

## 三、最终测试结果

```
2094 passed, 0 failed, 10 skipped
```

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| passed | 2077 | 2094 | +17 |
| failed | 17 | 0 | -17 ✅ |
| skipped | 10 | 10 | 0 |

---

## 四、修改文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `edge_client/actuators/vts_driver.py` | 源码 | VTS 映射表扩展至 16 种 |
| `tests/fixtures/context_pack/good/minimal_valid.json` | fixture | stage/trend 合法化 |
| `tests/fixtures/canon/world/context_pack_with_world_canon.json` | fixture | stage 合法化 |
| `tests/fixtures/llm_gateway/good/prompt_payload.json` | fixture | stage/trend 合法化 |
| `tests/fixtures/bond/good/bond_event_for_summary.json` | fixture | stage/trend 合法化 |
| `tests/fixtures/channels/vts_unknown_tag_degraded.json` | fixture | emotion_tag 改为真正未知的 SLEEPY |
| `tests/contract/test_bond_summary_contract.py` | 测试 | 期望值同步 |
| `tests/contract/test_governor_rate_limit_contract.py` | 测试 | rule_trace 数量 7→8 |
| `tests/contract/test_governor_rules_contract.py` | 测试 | rule_trace 数量 7→8 |
| `tests/contract/test_obs_metrics_contract.py` | 测试 | required_keys 添加 emotion_metrics |
| `tests/contract/test_vts_degraded_contract.py` | 测试 | 未知 tag 改为 SLEEPY，fixture 期望值同步 |
| `tests/contract/test_vts_reconnect_contract.py` | 测试 | 映射表数量 6→16 |
| `tests/contract/test_canon_store_contract.py` | 测试 | bond_summary.stage 合法化 |
| `tests/integration/test_bond_routes_api.py` | 测试 | stage/trend 期望值改为中文枚举 |
| `tests/integration/test_bond_dynamics_stage_integration.py` | 测试 | stage 期望值改为中文 |

---

## 五、质量门禁

- ✅ `release_ready = true`
- ✅ `zero_regressions = true`（0 failed）
- ✅ `total_tests_passed = 2094`（≥ 2077 基线）
- ✅ `edge_host_vts_sync = true`（edge/host 映射表已同步）
- ✅ `bond_summary_cn_consistent = true`（中文枚举全链路一致）
