# P8：下一轮候选基线 — 范围与验收标准

**日期**：2026-03
**状态**：**完成并正式验收通过（2026-03 封板）**；本文档为 P8 范围与验收的单一依据
**定位**：在 P1～P7 全部封板的前提下，扩展观测面（subgraph/local loop 全量 ledger 聚合），不破坏四轮基线
**总原则**：先定范围与验收再实施；不把未在 P8_SCOPE 明确纳入的能力混入 P8；不得回改已封板结论。

---

## 0. 基线前提（已完成并冻结）

以下视为**当前正式基线**，P8 中不回改：

- **第一轮正式基线**：P1～P4，已封板
  - execution_profile / 三图分治、Supervised Graph 主路径、Intent 透明子图、subgraph_commit_summary / subgraph_failure 契约固化

- **第二轮正式基线**：P5，已封板
  - Supervisor rule/llm 双模、proposal 契约、失败码、灰度与回退、supervisor_decision_log / supervisor_metrics

- **第三轮正式基线**：P6，已封板
  - Intent 子图局部循环、local loop 边界与失败码、step 级 subgraph_trace；局部循环仅限 intent_agent_subgraph 内

- **第四轮正式基线**：P7，已封板
  - 预算治理、统一回退矩阵、观测聚合（最小统一观测面）、压测与故障注入基线；不改三图、不扩 executor、不新增 Agent

已冻结架构事实（与 REFACTOR 一致）：

1. **先选图再跑图**；Chat / Supervised / Intent 三图分治
2. **executor 为通用执行器**，不承载业务分派或业务循环语义
3. **Supervised Graph** 主链仍为 supervisor_dispatch → agent_dispatch → judge_agent → renderer
4. **Intent 图** 外层仍为 intent_agent_entry → safety_verifier → judge_agent → renderer
5. **subgraph_commit_summary / subgraph_failure / subgraph_trace** 及 P7 预算/回退/观测契约已固化
6. **P1～P7 已全部封板**，后续工作不得以旧阶段补丁名义混入

---

## 1. 项目阶段状态

| 阶段 | 状态 |
|------|------|
| P1～P4 | 完成并正式验收通过 |
| P5 | 完成并正式验收通过（2026-03 封板） |
| P6 | 完成并正式验收通过（2026-03 封板） |
| P7 | 完成并正式验收通过（2026-03 封板） |
| **P8** | **完成并正式验收通过（2026-03 封板）** |

**第一轮正式基线**：P1～P4，已封板。
**第二轮正式基线**：P5，已封板。
**第三轮正式基线**：P6，已封板。
**第四轮正式基线**：P7，已封板。
**第五轮正式基线**：P8，已封板（2026-03）；范围与验收以 **MULTI_AGENT_P8_SCOPE_2026-03.md** 为准（本文档）。

---

## 2. P8 唯一目标（窄定义）— 定稿

**一句话定位**：
在遵守三图与 execution_profile、不撤销已封板结论的前提下，将 **subgraph/local loop 的失败分布与延迟分布纳入统一 ledger 聚合**，补全 P7 遗留的观测缺口，使三图全链路均可跨请求量化。

### 2.1 纳入范围（P8 唯一目标）

**P8-1：subgraph/local loop 全量 ledger 聚合**

P7 已建立「最小统一观测面」（Supervisor 路径 + Supervisor 侧 budget breach），但以下观测仍仅通过 run 级 output/trace 审计，无法跨请求聚合：

| 当前缺口 | 说明 |
|----------|------|
| subgraph_failure 分布 | Intent 子图失败码（SUBGRAPH_FAILURE_*）无跨请求聚合 |
| local_loop failure 分布 | LOCAL_LOOP_TIMEOUT / LOCAL_LOOP_MAX_STEPS 等失败码无跨请求聚合 |
| Intent 图 budget breach 聚合 | Intent 图内预算突破仅在单次 run output 可见，无 ledger 聚合 |
| 各图延迟分布 | Chat / Supervised / Intent 三图的 p50/p95 延迟无统一聚合视图 |

**P8-1 目标**：将上述四类观测写入统一 ledger 聚合，与 P7 已有的 Supervisor 侧观测形成完整三图观测面。

### 2.2 范围外（硬约束）

P8 **不得**：

- 破坏 P1～P7 已封板契约与验收结论
- 改动三图主链结构（Chat / Supervised / Intent 图的节点与边不变）
- 扩展 executor 业务语义（executor 保持通用执行器定位）
- 新增 Agent 类型（不新增 chat_agent 以外的新 Agent）
- 将 ReAct / Handoff 试点、多 Agent 循环 handoff、全局 react_scratchpad 等 REFACTOR §6 暂缓项纳入 P8
- 以 P8 名义回改 REFACTOR 三条硬性约束或四轮基线定义
- 将未在 P8_SCOPE 明确纳入的能力以"顺带""优化"名义混入
- 将 P9 及以后阶段能力提前包装成 P8

---

## 3. P8 验收标准（定稿，共 5 条）

实施完成后须逐条满足以下验收条件：

### 3.1 subgraph_failure 分布可跨请求聚合

- `metrics.collect()` 返回值中包含 `subgraph_failure_metrics`
- `subgraph_failure_metrics` 至少含：`subgraph_failure_count`、`subgraph_failure_by_code`（按失败码分组计数）
- 覆盖 Intent 图内所有 `SUBGRAPH_FAILURE_*` 失败码
- 有对应单元测试验证字段存在性与计数逻辑

### 3.2 local_loop failure 分布可跨请求聚合

- `metrics.collect()` 返回值中包含 `local_loop_failure_metrics`
- `local_loop_failure_metrics` 至少含：`local_loop_failure_count`、`local_loop_failure_by_code`（按失败码分组计数）
- 覆盖 `LOCAL_LOOP_TIMEOUT`、`LOCAL_LOOP_MAX_STEPS`、`LOCAL_LOOP_INTERNAL_ERROR` 三个 P6 封板失败码（**不得**引入 P6 未定义的新失败码，如 `LOCAL_LOOP_TOOL_ERROR`）
- 有对应单元测试验证字段存在性与计数逻辑

### 3.3 Intent 图 budget breach 可跨请求聚合

- **口径已选死**：保留 P7 已有的 `budget_breach_metrics` 不动（继续代表 Supervisor 侧最小统一观测面）；**新增** `intent_budget_breach_metrics`，不修改已有字段
- `intent_budget_breach_metrics` 至少含：`intent_budget_breach_count`、`intent_budget_breach_by_code`（按失败码分组计数）
- 覆盖 Intent 图内 `BUDGET_BREACH_*` 失败码（与 P7 `BUDGET_BREACH_FAILURE_CODES` 兼容）
- 有对应单元测试验证字段存在性与计数逻辑

### 3.4 三图延迟分布可统一聚合

- `metrics.collect()` 返回值中包含 `graph_latency_metrics`
- `graph_latency_metrics` 按 `execution_profile` 分组，至少含：`p50_ms`、`p95_ms`、`sample_count`
- 覆盖 `CHAT_FAST`、`WORKFLOW_SUPERVISED`、`INTENT_EXEC` 三个 profile
- **测量口径**（已写死，不得在实现中自行解释）：
  - 统计 `run_graph` 总耗时（从调用入口到返回结果），单位毫秒
  - 按 `execution_profile` 分组，每个 profile 独立维护样本序列
  - p50/p95 计算要求样本量 ≥ 10；样本量不足时，`p50_ms`/`p95_ms` 置 `null`，`sample_count` 正常记录
- 有对应单元测试验证字段存在性与分组逻辑

### 3.5 三图结构与 P1～P7 契约不变

- Chat / Supervised / Intent 三图主链节点与边不变
- `execution_profile` 语义不变，入口层写入逻辑不变
- executor 不新增业务语义
- P7 已有的 `supervisor_metrics`、`budget_breach_metrics`（Supervisor 侧）不退化
- 有集成测试验证三图主链可正常运行（可复用 P7 集成测试用例）

---

## 4. 实现约束（定稿）

### 4.1 观测数据来源与归类优先级

- subgraph_failure / local_loop failure 数据来源：`run_graph` 返回的 `payload.subgraph_failure`、`output.subgraph_trace`，由 `graph_runtime` 在 run 完成后写入 ledger 专用表，再由 `p8_extended_metrics.py` 查询聚合
- 三图延迟数据来源：`run_graph` 执行时间戳，由 `graph_runtime` 在 run 完成后按 `execution_profile` 写入 ledger 专用表
- **不得**在 executor 内部新增业务聚合逻辑；持久化只在 `graph_runtime` 写入 ledger，聚合查询只在 `p8_extended_metrics.py`

**归类优先级（已写死，两者互斥，不重复计数）**：

| 优先级 | 判断条件 | 归入指标 |
|--------|---------|---------|
| 1（优先） | `error_code` 属于 `LOCAL_LOOP_*`（即 `LOCAL_LOOP_TIMEOUT`、`LOCAL_LOOP_MAX_STEPS`、`LOCAL_LOOP_INTERNAL_ERROR`） | 只计入 `local_loop_failure_metrics`，**不计入** `subgraph_failure_metrics` |
| 2（其余） | 其余 Intent 子图失败码（`SUBGRAPH_FAILURE_*` 等） | 只计入 `subgraph_failure_metrics` |

> 原因：local loop 失败通过 `subgraph_failure.error_code` 上报，若不先归类会导致重复计数。归类规则以 error_code 前缀为准，实现时须在写入 ledger 前完成分类，不得在聚合查询时再做二次分类。

### 4.2 与 P7 观测的兼容性

- P7 已有的 `supervisor_metrics`、`budget_breach_metrics` 字段结构不变
- P8 新增字段以**追加**方式扩展 `metrics.collect()` 返回值，不修改已有字段
- `budget_breach_metrics` 保持 P7 口径不动；Intent 图 budget breach 通过新增的 `intent_budget_breach_metrics` 独立上报（见 §3.3）

### 4.3 可关闭 / 可灰度

- P8 新增聚合逻辑须有独立开关（如 `P8_EXTENDED_METRICS_ENABLED`），默认 `false`
- 开关关闭时，`metrics.collect()` 行为与 P7 完全一致，不引入新字段
- 开关开启时，新增字段追加到 `metrics.collect()` 返回值

### 4.4 不破坏 P1～P7 契约

- 不改动 `graph_contract.py` 中 P1～P7 已定义的字段语义
- 不改动三图 builder（`build_chat_graph`、`build_supervised_graph`、`build_intent_graph`）的节点与边
- 不改动 `execution_profile` 的写入逻辑与语义
- 不改动 `agent_dispatch_handler` 的 allowlist 与分派逻辑

---

## 5. 冻结非目标（不纳入 P8）

以下事项**不属于 P8**，不得以任何名义混入：

- 全局 ReAct 全量默认开启
- code_agent ReAct、translate_agent ReAct（REFACTOR §6 暂缓项）
- 多 Agent 循环 handoff 无界扩展
- 新 Agent 类型或并行 agent
- executor 业务扩权
- 三图结构或 execution_profile 语义变更
- 记忆 / RAG / 新业务场景 / 前端改造等
- 将 P9 或更晚阶段能力包装成 P8

---

## 6. 文件改动清单（P8 最小实现面）

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `host_brain/observability/p8_extended_metrics.py` | P8 扩展观测聚合逻辑（只负责查询与聚合，不直接承担持久化）：subgraph_failure_metrics、local_loop_failure_metrics、intent_budget_breach_metrics、graph_latency_metrics |
| `tests/unit/test_p8_extended_metrics.py` | P8 扩展观测单元测试（验收 3.1～3.4） |
| `tests/integration/test_p8_integration.py` | P8 集成测试（验收 3.5：三图主链不退化） |

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/observability/metrics.py` | 在 `collect()` 中按 `P8_EXTENDED_METRICS_ENABLED` 开关追加 P8 新增字段；不改动已有字段 |
| `host_brain/integrations/graph_runtime.py` | run 完成后按归类优先级（§4.1）将 subgraph_failure / local_loop failure / execution_profile / 延迟写入 ledger 专用表 |
| `.env.example` | 新增 `P8_EXTENDED_METRICS_ENABLED=false` 说明 |

### ledger 层改动（必须，不可省略）

> 跨请求聚合依赖持久化存储，仅改 `metrics.py` 和 `graph_runtime.py` 不够，须同步改动 ledger 层。

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/persistence/ledger_schema.py`（或等效文件） | 新增 P8 专用表定义：`p8_subgraph_failure_log`、`p8_graph_run_log`（含 execution_profile、latency_ms、budget_breach_code 等字段） |
| `host_brain/persistence/ledger_constants.py`（或等效文件） | 新增 P8 相关常量：`LOCAL_LOOP_FAILURE_CODES`、`P8_TABLE_*` 等 |
| `host_brain/persistence/sqlite_ledger.py`（或等效文件） | 实现 P8 专用表的写入方法，供 `graph_runtime` 调用 |

> 若项目中 ledger 层文件路径与上述不同，以实际路径为准；但**必须**有对应的 schema 定义、常量定义和写入方法，不得将持久化逻辑内联到 `graph_runtime` 或 `p8_extended_metrics`。

---

## 7. 与现有文档的关系

- **MULTI_AGENT_ARCH_REFACTOR_2026-03.md**：架构总则、阶段顺序与文档关系仍以此为总表；P8 作为下一轮候选基线，范围与验收以本文档为准；REFACTOR §10/§11 已引用本文档。
- **MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md**：能力与契约参考；P8 不涉及 ReAct/Handoff，本阶段不对齐该文档中的 ReAct 设计思路。
- **MULTI_AGENT_P7_3_OBSERVABILITY_SCOPE_2026-03.md**：P8 直接补全该文档 §2 中「当前不纳入统一 ledger 聚合的观测」；P8 实施后，§2 所列缺口应全部消除。
- **MULTI_AGENT_P4/P5/P6/P7_SCOPE 与对应 SEAL_RESULT**：P8 不得回改上述已封板结论；P8 新增聚合字段须与 P7 `BUDGET_BREACH_FAILURE_CODES`、P6 `LOCAL_LOOP_*` 失败码保持兼容。

若 P8_SCOPE 与其它文档在 P8 范围上冲突，以本文档为准；若涉及 P1～P7 已封板事实，以对应 seal result 为准。

---

## 8. 结论

- **P1～P7 已全部形成完整封板链路。**
- **P8 已完成并正式验收通过（2026-03 封板）**；封板结论见 **MULTI_AGENT_P8_SEAL_RESULT_2026-03.md**。
- **P8 唯一目标**：subgraph/local loop 全量 ledger 聚合（P8-1），已落地，三图全链路均可跨请求量化。
- **5 条验收标准**全部满足（5/5），34 个测试用例全部通过。
- **第五轮正式基线 = P8，已封板。** P1～P8 形成完整封板链路。

---

## 变更记录

- 2026-03-17 v1.0：初版规划稿，P8 可选方向占位，待定稿。
- 2026-03-17 v1.1：定稿。P8 唯一目标确定为「subgraph/local loop 全量 ledger 聚合」；5 条验收标准写死；实现约束、文件改动清单、冻结非目标补全。
- 2026-03-17 v1.2：消歧义补丁。①修正 local_loop 失败码口径（`LOCAL_LOOP_INTERNAL_ERROR`，去除 P6 未定义的 `LOCAL_LOOP_TOOL_ERROR`）；②钉死 budget_breach 口径（保留 P7 `budget_breach_metrics` 不动，新增 `intent_budget_breach_metrics`）；③补全 ledger 层改动清单（schema/constants/sqlite_ledger）；④写死归类优先级规则（LOCAL_LOOP_* 优先归入 local_loop_failure_metrics，两者互斥不重复计数）；⑤写死延迟测量口径（run_graph 总耗时、毫秒、样本量 ≥ 10 才计算分位数）。
- 2026-03-17 v1.3：封板。P8-1 实施完成，5/5 验收标准满足，34 个测试用例全部通过；状态更新为「完成并正式验收通过（2026-03 封板）」；第五轮正式基线 = P8。
