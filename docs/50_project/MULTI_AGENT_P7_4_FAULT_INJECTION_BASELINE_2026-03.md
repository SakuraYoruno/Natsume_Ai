受众：PROJECT
标题：P7-4：压测与故障注入基线

# P7-4：压测与故障注入基线

**日期**：2026-03  
**依据**：MULTI_AGENT_P7_SCOPE §3.5、§3.6  
**定位**：三图最小可重复故障注入与压测样例索引，形成稳定基线而非临时脚本。

---

## 1. 故障注入样例（可重复运行）

以下用例均位于 `tests/`，使用真实 runtime + 图配置，可重复执行。

### 1.1 Intent 图

| 故障类型           | 测试文件 / 用例 | 断言要点 |
|--------------------|------------------|----------|
| 局部循环超时       | test_p6_integration_acceptance.py::test_p6_3_timeout_integration_local_loop_timeout | LOCAL_LOOP_TIMEOUT、INTENT_EXEC、主图 4 节点 |
| 重试耗尽           | test_p6_integration_acceptance.py::test_p6_3_retry_exhausted_integration_local_loop_internal_error | LOCAL_LOOP_INTERNAL_ERROR、failed_node、step 级 trace |
| 子图节点失败       | test_p4_intent_subgraph_failure.py::test_intent_exec_subgraph_failure_contained_and_consumable | subgraph_failure、无跨图污染 |
| 成功路径           | test_p6_integration_acceptance.py::test_p6_3_success_path_integration_subgraph_commit_and_step_trace | subgraph_commit_summary、step 级 trace |

### 1.2 Supervised 图

| 故障类型           | 测试文件 / 用例 | 断言要点 |
|--------------------|------------------|----------|
| LLM 失败回退 rule  | test_p5_supervisor_llm_mode.py（无 gateway、bad json、schema invalid、agent not allowed） | supervisor_failure_code、回退 rule、收口在 Supervised 图内 |

### 1.3 Chat 图

| 类型               | 测试文件 / 用例 | 断言要点 |
|--------------------|------------------|----------|
| 选图与 profile     | test_p1_p2_execution_profile_smoke.py 等 | execution_profile、选图正确 |

---

## 2. 压测口径（最小）

- **Chat 图基线路径**：通过现有 smoke（如 execution_profile 覆盖 CHAT / WORK_* / INTENT）。  
- **Supervised 图**：rule 路径 + llm 路径 + llm 回退路径（见 P5-5 集成）。  
- **Intent 图**：成功路径、子图失败、局部循环超时、重试耗尽（见 P6-3 集成）。  

输出可观测项：延迟（run 耗时）、成功率（无 failure 或预期 failure 码）、fallback 率（supervisor_metrics.llm_fallback_rate_pct）、关键失败码分布（supervisor_metrics.failure_code_count、budget_breach_metrics.budget_breach_by_code）。

---

## 3. 与 P7-2 失败矩阵的对应

上述故障注入用例覆盖 P7_2_FAILURE_MATRIX 中：

- Intent：INVOKE_MODEL_UNAVAILABLE、HANDLER_ERROR、LOCAL_LOOP_*、子图收口、不跨图。  
- Supervised：LLM_* 失败码、回退 rule、收口在 Supervised 内。  
- Chat：选图与 profile、节点超时/依赖（通过 fallback 路径）。  

新增故障类型时，应纳入本基线并更新上表与失败矩阵。

---

## 4. 压测结果摘要模板（P7_SCOPE 3.6 验收级输出）

每轮基线运行后，输出至少包含以下维度（可与现有 pytest / metrics 结合）：

| 维度         | 说明 |
|--------------|------|
| **图/路径**  | Chat 基线 / Supervised rule / Supervised llm / Intent 成功 / Intent 失败（含子图失败、local loop 超时或重试耗尽） |
| **延迟**     | 单次或 P50/P95 run 耗时（ms 或 s） |
| **成功率**   | 预期成功路径的通过率（%）；或预期失败路径的“按矩阵收口”率 |
| **fallback 率** | Supervisor llm → rule 的占比（%），来自 supervisor_metrics.llm_fallback_rate_pct 或等价 |
| **关键失败码分布** | failure_code_count、budget_breach_by_code 等 |

---

## 5. 最小压测结果摘要（示例）

以下为**最小基线运行**的示例摘要，用于满足 P7_SCOPE 3.6「输出至少包含延迟、成功率、fallback 率、关键失败码分布」的验收级输出要求。实际封板前可替换为最近一次正式基线运行结果。

| 图/路径              | 延迟（示例） | 成功率（示例） | fallback 率 | 关键失败码（示例）     |
|----------------------|--------------|----------------|-------------|------------------------|
| Intent 成功路径      | &lt; 1s       | 100%           | —           | —                      |
| Intent 子图失败      | &lt; 1s       | 100%（按矩阵收口） | —           | HANDLER_ERROR / 子图   |
| Intent 局部循环超时  | &lt; 1s       | 100%（按矩阵收口） | —           | LOCAL_LOOP_TIMEOUT     |
| Intent 重试耗尽      | &lt; 1s       | 100%（按矩阵收口） | —           | LOCAL_LOOP_INTERNAL_ERROR |
| Supervised rule      | 依环境       | 依 smoke       | 0%          | —                      |
| Supervised llm / 回退 | 依环境       | 依 P5-5 用例   | 依用例      | LLM_* 等               |
| Chat 基线            | 依 smoke     | 依 smoke       | —           | —                      |

- **数据来源**：pytest 集成用例（test_p6_integration_acceptance、test_p4_intent_subgraph_failure、test_p5_supervisor_llm_mode、test_p1_p2_execution_profile_smoke 等）单轮运行；metrics 中 supervisor_metrics、budget_breach_metrics 可用于 fallback 率与失败码分布。  
- **更新约定**：封板前建议用实际基线运行结果替换本表示例值，或在本节注明「首轮基线结果见某日某 run」。
