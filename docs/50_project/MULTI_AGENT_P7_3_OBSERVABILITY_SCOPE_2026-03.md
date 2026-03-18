受众：PROJECT
标题：P7-3：当前统一观测面范围边界

# P7-3：当前统一观测面范围边界

**日期**：2026-03  
**依据**：MULTI_AGENT_P7_SCOPE §3.4、P7 验收汇总预审  
**定位**：明确 P7 阶段「观测面统一」的当前范围，避免被理解为“全量统一治理已完成”。

---

## 1. 当前统一观测面（已纳入）

以下观测**已**通过统一入口（ledger / metrics collect）提供：

- **Supervisor 路径**：supervisor_decision_log → supervisor_metrics（决策来源、失败码、selected_agent、llm fallback 率）。  
- **Budget breach（来自 Supervisor）**：从 supervisor_decision_log 中 failure_code ∈ BUDGET_BREACH_FAILURE_CODES 的条目聚合 → budget_breach_metrics（budget_breach_count、budget_breach_by_code、budget_breach_rate_pct）。  
- **Governor / 检索 / 延迟基线 / 情感等**：沿用现有 metrics 与 ledger 表，未收缩。

上述构成当前「**最小统一观测面**」：以 **Supervisor 决策与预算突破（Supervisor 侧）** 为统一 ledger 聚合入口。

---

## 2. 当前不纳入统一 ledger 聚合的观测

以下信息**不**在 P7 阶段写入统一 ledger 聚合视图，仅通过 **run 输出 / trace / payload** 审计：

- **subgraph_failure / local_loop failure 分布**：Intent 子图失败、LOCAL_LOOP_* 失败码、子图内 step 级 trace，目前由 **runtime.run_graph 返回的 payload.subgraph_failure、output.subgraph_trace** 承载；下游与排障通过单次 run 的 output 读取，**不**进入当前 metrics 的跨请求聚合。  
- **Intent 图内 budget breach 的跨请求聚合**：单次 run 若因 LOCAL_LOOP_TIMEOUT / LOCAL_LOOP_MAX_STEPS 等失败，可在该次 output 中看到；若需「各图延迟分布、错误分布、Intent 图 budget breach 占比」等**全量 ledger 聚合**，需后续阶段扩展（例如子图/local loop 结果写入 ledger 或 run 级采样后再聚合）。

---

## 3. 与 P7_SCOPE 3.4 的对应

- **3.4 要求**：能稳定观测并聚合 execution_profile 分布、supervisor 决策与失败码、subgraph_failure / local_loop failure 分布、budget breach 分布、fallback、最终输出是否成功、各图延迟与错误分布。  
- **当前满足口径**：  
  - execution_profile、supervisor、fallback、**Supervisor 侧** budget breach 已纳入或可由现有 metrics/ledger 推导。  
  - **subgraph_failure / local_loop failure 分布**、**各图延迟/错误分布**的**全量聚合**不在 P7 当前范围；子图与局部循环的观测仍以 **run 级 output/trace** 为主。  
- 因此 P7 验收与 seal result 中，3.4 按「**当前最小统一观测面**」满足，并明确：**subgraph/local loop 全量 ledger 聚合不属于 P7 当前范围，留待后续阶段**。

---

## 4. 结论

- 当前统一观测面 = **Supervisor 路径 + 来自 Supervisor 的 budget breach 聚合**；子图/local loop 仍通过 **run output / subgraph_trace / subgraph_failure** 审计。  
- 后续若需「subgraph/local loop failure 分布、各图延迟与错误分布」的稳定聚合，须在 ledger 或 run 级采样上扩展，单独立项。
