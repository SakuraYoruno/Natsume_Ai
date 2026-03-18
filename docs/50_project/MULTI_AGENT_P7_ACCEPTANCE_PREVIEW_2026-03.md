受众：PROJECT
标题：P7：验收汇总预审

# P7：验收汇总预审

**日期**：2026-03  
**依据**：MULTI_AGENT_P7_SCOPE §3（8 条验收标准）  
**定位**：封板前逐条定性，不直接宣告 8/8 满足；明确 3.4 / 3.6 口径后再决定是否正式验收通过。

---

## 1. 结论先行

- **P7 已基本完成**，可进入正式验收汇总；**不建议在未收窄口径前直接判「正式验收通过」**。  
- 建议先补齐：**(1) P7-3 观测面边界说明**、(2) **P7-4 最小压测结果摘要**，再撰写 P7_SEAL_RESULT 并更新阶段为「完成并正式验收通过」。  
- 以下 8 条按 **满足 / 基本满足 / 未完成** 定性；其中 3.4、3.6 为「基本满足」，需在 seal result 或范围文档中写明当前满足口径。

---

## 2. 8 条验收标准逐条定性

| 条款 | 定性 | 说明 |
|------|------|------|
| **3.1 三图结构与阶段边界保持不变** | ✅ 满足 | Chat / Supervised / Intent 主链与 execution_profile 未改；P7 仅补治理与观测。 |
| **3.2 预算治理形成统一契约** | ✅ 满足 | 图级/子图级/节点级预算字段与配置键、BUDGET_BREACH_FAILURE_CODES、is_budget_breach_failure_code 已落地；预算突破有稳定失败码与 trace。 |
| **3.3 三图失败收口矩阵固定** | ✅ 满足 | P7_2_FAILURE_MATRIX 已固化；失败位置、类型、收口策略、可见输出、不跨图均成表。 |
| **3.4 观测面统一** | ⚠️ 基本满足 | 见 §3。当前以 **最小统一观测面** 达成：supervisor_decision_log + budget_breach_metrics（来自 Supervisor 路径）；subgraph_failure / local_loop failure **未**进入统一 ledger 聚合，仍通过 run output / subgraph_trace 审计。需在 seal result 或 P7-3 观测范围文档中明确此边界。 |
| **3.5 故障注入可重复运行** | ✅ 满足 | LLM 失败、子图节点失败、local loop 超时/重试耗尽等均有可重复集成用例；收口符合矩阵。 |
| **3.6 压测口径固定** | ⚠️ 基本满足 | 见 §4。压测**口径**与**基线文档/索引**已固定；**验收级输出**（延迟、成功率、fallback 率、关键失败码分布的汇总结果）需至少有一份最小压测结果摘要支撑。补齐后可按「压测口径固定」满足验收。 |
| **3.7 executor 不扩权** | ✅ 满足 | 未在 executor 中增加预算/回退/循环/分派等业务语义；P6 结构断言已覆盖。 |
| **3.8 可封板、可维护** | ✅ 满足 | 范围文档（P7_SCOPE）、契约/实施（P7-1/2/3/4 文档与代码）、集成验收（失败矩阵与故障注入基线）、seal result 路径均明确；冻结非目标已写清。 |

---

## 3. 3.4 观测面统一 — 当前满足口径

- **已具备**：execution_profile 与图选型由现有链路提供；supervisor 决策来源与失败码、budget breach（来自 supervisor_decision_log）已纳入 metrics（supervisor_metrics、budget_breach_metrics）；fallback 次数与类型见 supervisor_metrics.llm_fallback_rate_pct 等。  
- **当前边界**：**统一 ledger 聚合入口仅包含 Supervisor 路径**。subgraph_failure / local_loop failure 分布、Intent 图内 budget breach 的**全量 ledger 聚合**不属于 P7 当前范围，仍通过 **run 输出的 subgraph_trace、subgraph_failure、payload** 做审计与排障。  
- **验收口径**：在 P7 正式验收与 seal result 中，3.4 按「**当前最小统一观测面**」满足，并明确「subgraph/local loop 全量 ledger 聚合留待后续阶段」。

---

## 4. 3.6 压测口径固定 — 当前满足口径

- **已具备**：Chat / Supervised / Intent 三图压测**口径**已定义（P7_4_FAULT_INJECTION_BASELINE）；故障注入**基线用例索引**与可重复运行用例已固化；输出可观测项（延迟、成功率、fallback 率、关键失败码分布）已在文档中约定。  
- **待闭环**：至少产出一份**最小压测结果摘要**（例如：Chat 一组、Supervised rule/llm 各一组、Intent 成功/失败各一组；输出延迟、成功率、fallback 率、关键失败码分布），以证明「验收级输出」可复现。  
- **验收口径**：在补齐上述最小结果摘要后，3.6 可按「压测口径固定 + 基线结果可复现」满足；或若选择先封板，则在 seal result 中写明 3.6 按「**压测口径与基线文档已固定**」满足，并注明「首轮基线结果摘要见 P7_4 文档 §5」或等价表述。

---

## 5. 建议下一步

1. **补 P7-3 观测边界**：在 P7_3_OBSERVABILITY_SCOPE 或等价文档中写明「当前统一观测面范围」与「不含 subgraph/local loop 全量 ledger 聚合」。  
2. **补 P7-4 最小压测结果摘要**：在 P7_4 文档中增加「压测结果摘要模板」与至少一份最小基线运行结果（延迟、成功率、fallback 率、关键失败码分布）。  
3. **再写 P7_SEAL_RESULT**：8 条逐条结论中 3.4、3.6 采用上述收窄口径；其余 6 条按「满足」结论；整体结论为「P7 正式验收通过」并更新 P7_SCOPE / REFACTOR。
