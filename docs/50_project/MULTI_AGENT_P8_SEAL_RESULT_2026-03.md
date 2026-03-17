# P8 封板结果（SEAL RESULT）

**日期**：2026-03
**状态**：**完成并正式验收通过（2026-03 封板）**
**定位**：第五轮正式基线 = P8，已封板
**范围依据**：MULTI_AGENT_P8_SCOPE_2026-03.md（v1.2 消歧义补丁定稿）

---

## 一句话结论

> **P8-1 已完成，P8 已正式验收并封板。**
> 在不破坏 P1～P7 基线的前提下，subgraph/local loop 全量 ledger 聚合已落地，三图全链路均可跨请求量化。
> **第五轮正式基线 = P8，已封板。**

---

## 1. 5 条验收标准逐条结论（5/5 满足）

| 验收条 | 标准描述 | 结论 | 对应测试 |
|--------|---------|------|---------|
| **3.1** | `subgraph_failure_metrics` 可跨请求聚合，含失败码分组 | ✅ 满足 | `TestSubgraphFailureMetrics`（3 用例） |
| **3.2** | `local_loop_failure_metrics` 可跨请求聚合，覆盖 P6 封板三码 | ✅ 满足 | `TestLocalLoopFailureMetrics`（3 用例） |
| **3.3** | `intent_budget_breach_metrics` 可跨请求聚合，P7 `budget_breach_metrics` 不动 | ✅ 满足 | `TestIntentBudgetBreachMetrics`（3 用例） |
| **3.4** | `graph_latency_metrics` 按三图 profile 分组，含 p50/p95，样本量 ≥ 10 才计算 | ✅ 满足 | `TestGraphLatencyMetrics`（4 用例） |
| **3.5** | 三图主链与 P1～P7 契约不退化 | ✅ 满足 | `TestThreeGraphStructureUnchanged` + `TestP7ObsFieldsNotDegraded` + `TestP8LedgerMethods`（19 用例） |

**总计：34 个测试用例，全部通过（34 passed, 0 failed）。**

---

## 2. 新增 ledger 与聚合范围

### 2.1 新增 ledger 表（持久化层）

| 表名 | 用途 | 写入时机 |
|------|------|---------|
| `p8_subgraph_failure_log` | 记录每次 run_graph 中的 subgraph/local loop 失败事件 | `graph_runtime._write_p8_ledger()` 在 run 完成后写入 |
| `p8_graph_run_log` | 记录每次 run_graph 执行记录（延迟 + budget breach） | `graph_runtime._write_p8_ledger()` 在 run 完成后写入 |

### 2.2 新增聚合模块

| 文件 | 职责 |
|------|------|
| `host_brain/observability/p8_extended_metrics.py` | 只负责从 ledger 查询与聚合，不直接承担持久化 |

提供四类聚合方法：

- `collect_subgraph_failure_metrics()` — Intent 子图失败分布（SUBGRAPH_FAILURE_* 等）
- `collect_local_loop_failure_metrics()` — local loop 失败分布（LOCAL_LOOP_* 三码）
- `collect_intent_budget_breach_metrics()` — Intent 图 budget breach 聚合
- `collect_graph_latency_metrics()` — 三图延迟分布（p50/p95，样本量 ≥ 10 才计算）

### 2.3 归类优先级（已实现，两者互斥，不重复计数）

| 优先级 | 判断条件 | 归入指标 |
|--------|---------|---------|
| 1（优先） | `error_code` ∈ `{LOCAL_LOOP_TIMEOUT, LOCAL_LOOP_MAX_STEPS, LOCAL_LOOP_INTERNAL_ERROR}` | 只计入 `local_loop_failure_metrics` |
| 2（其余） | 其余 Intent 子图失败码 | 只计入 `subgraph_failure_metrics` |

归类在 `_write_p8_ledger()` 写入 ledger 前完成，不在聚合查询时二次分类。

### 2.4 开关控制

- 环境变量：`P8_EXTENDED_METRICS_ENABLED`（默认 `false`）
- 开关关闭时：`metrics.collect()` 行为与 P7 完全一致，不引入新字段
- 开关开启时：四个新字段追加到 `metrics.collect()` 返回值

---

## 3. 与 P7 的兼容性说明

| 兼容项 | 结论 |
|--------|------|
| P7 `budget_breach_metrics` 字段结构 | ✅ 不动，P8 新增 `intent_budget_breach_metrics` 独立上报 |
| P7 `supervisor_metrics` 字段结构 | ✅ 不动，P8 不涉及 Supervisor 路径 |
| P7 `supervisor_decision_log` 写入逻辑 | ✅ 不动，P8 新增 `p8_subgraph_failure_log` / `p8_graph_run_log` 独立表 |
| P6 `LOCAL_LOOP_*` 三码口径 | ✅ 沿用，P8 未引入 P6 未定义的新失败码（`LOCAL_LOOP_TOOL_ERROR` 已排除） |
| 三图主链节点与边 | ✅ 不变，P8 改动仅在 ledger/metrics 层 |
| `execution_profile` 语义 | ✅ 不变，P8 仅读取该字段用于分组 |
| executor 业务语义 | ✅ 不扩权，`graph_executor.py` 无 P8 业务分支 |
| P1～P4 封板契约 | ✅ 不退化 |

---

## 4. 冻结非目标（P8 未纳入，后续阶段处理）

以下事项在 P8 中**未实施**，后续阶段按需规划：

- 全局 ReAct 全量默认开启
- code_agent ReAct、translate_agent ReAct（REFACTOR §6 暂缓项）
- 多 Agent 循环 handoff 无界扩展
- 新 Agent 类型或并行 agent
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 记忆 / RAG / 新业务场景 / 前端改造等
- P9 或更晚阶段能力

---

## 5. 实施文件清单（P8 最终落地）

### 新增文件（3 个）

| 文件路径 | 说明 |
|---------|------|
| `host_brain/observability/p8_extended_metrics.py` | P8 扩展观测聚合模块（只查询，不持久化） |
| `tests/unit/test_p8_extended_metrics.py` | P8 单元测试（验收 3.1～3.4 + 开关行为，15 用例） |
| `tests/integration/test_p8_integration.py` | P8 集成测试（验收 3.5，19 用例） |

### 修改文件（6 个）

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/audit/ledger_schema.py` | 新增 `p8_subgraph_failure_log`、`p8_graph_run_log` 两张表及索引 |
| `host_brain/audit/ledger_constants.py` | 新增 P8 列定义、`P8_LOCAL_LOOP_FAILURE_CODES`、`P8_TABLE_*` 常量；`_ALLOWED_TABLES` 追加两张新表 |
| `host_brain/audit/sqlite_ledger.py` | 新增 `p8_subgraph_failure_log`/`p8_graph_run_log` property 及 `log_p8_subgraph_failure()`/`log_p8_graph_run()` 写入方法 |
| `host_brain/observability/metrics.py` | 导入 P8 模块；`collect()` 按开关追加四个新字段 |
| `host_brain/integrations/graph_runtime.py` | `run_graph()` 完成后调用 `_write_p8_ledger()`；新增 `_write_p8_ledger()` 方法（含归类优先级逻辑） |
| `.env.example` | 新增 `P8_EXTENDED_METRICS_ENABLED=false` 说明 |

---

## 6. 最终结论

- **第一轮正式基线**：P1～P4，已封板
- **第二轮正式基线**：P5，已封板
- **第三轮正式基线**：P6，已封板
- **第四轮正式基线**：P7，已封板
- **第五轮正式基线**：**P8，已封板（2026-03）**

P8 封板后，P1～P8 形成完整封板链路。后续工作（P9 及以后）须在 P8 基线之上单独规划，不得以任何名义回改 P8 及以前的封板结论。
