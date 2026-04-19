受众：MASTER
标题：Agentic RAG 智能检索架构（当前代码实况）

版本：v2.0
日期：2026-04-19

---

定位：本文描述当前代码里的检索主链，而不是早期 `main_chain.py / RecallFusionNode` 方案草图。
若与旧文档中的“七路全量召回 + Graph 注入点”口径冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/chat_retrieval_helpers.py`
- `host_brain/tribe/hippocampus_agent.py`
- `host_brain/retrieval/orchestrator.py`
- `host_brain/retrieval/pipeline.py`
- `host_brain/retrieval/query_planner.py`
- `host_brain/retrieval/evidence_evaluator.py`
- `host_brain/retrieval/agentic_retrieval_loop.py`

---

## 一、当前检索入口

当前聊天主链中的检索入口不是 `main_chain.py`，而是：

```text
/v1/chat
  -> chat_handler
  -> cortex_orchestrator
  -> agent_runtime_shared._cortex_orchestrator_sse_generator_impl
  -> chat_retrieval_helpers._run_retrieval
  -> HippocampusAgent.recall
  -> RetrievalOrchestrator.arun
  -> RetrievalPipelineContract.run
```

其中职责分工如下：

| 层 | 当前模块 | 职责 |
|---|---|---|
| 主链触发 | `agent_runtime_shared.py` | 决定是否启动 retrieval task，并把结果回灌到 prompt / trace |
| 检索桥接 | `chat_retrieval_helpers.py` | 调用 `HippocampusAgent.recall()`，做 prompt_filter、RecallPackBuilder 补充 |
| 记忆/检索族入口 | `hippocampus_agent.py` | 构造 `RetrievalOrchestratorRequest` |
| 编排入口 | `retrieval/orchestrator.py` | 统一输出 `retrieval_summary / retrieval_evidence_items / retrieval_bundle` |
| 管线核心 | `retrieval/pipeline.py` | QueryPlanner → AgenticRetrievalLoop → Dedup → Rerank → EvidencePack |

---

## 二、当前检索管线

当前 `RetrievalPipelineContract.run()` 的真实步骤是：

```text
QueryPlanner
  -> EmbeddingService.embed
  -> AgenticRetrievalLoop.retrieve
       -> RecallFusionService.recall (可多轮)
       -> EvidenceEvaluator.evaluate
  -> SemanticDedup.dedup
  -> RerankerService.rerank
  -> EvidencePackService.build_pack
  -> Orchestrator 汇总 summary / bundle / evidence_items
  -> agent_runtime_shared 做 prompt_filter 与 RecallPackBuilder
```

当前代码里，检索结果不仅返回候选证据，还会额外产出：

- `retrieval_summary`
- `retrieval_evidence_items`
- `query_plan`
- `agentic_meta`
- `adaptive_budget`
- `cache` 元数据

---

## 三、QueryPlanner 当前口径

`host_brain/retrieval/query_planner.py` 当前的 `ALL_STORE_IDS` 只有 5 路：

- `episodic_vector`
- `sqlite_ledger`
- `neo4j_bond`
- `world_canon`
- `artifact_vault`

文件内已经明确说明：

- `style_canon` 不再走这里的检索路由，而是常驻注入
- `persona_utterance` 不再属于这里的 store map，而是独立通道

### 3.1 当前意图到 Store 路由

| intent | target_stores |
|---|---|
| `greeting` | 空，直接跳过检索 |
| `phatic` | `episodic_vector` |
| `chat` | `episodic_vector` |
| `recall_memory` | `episodic_vector`, `sqlite_ledger` |
| `emotion` | `neo4j_bond`, `episodic_vector` |
| `affection` | `neo4j_bond`, `episodic_vector` |
| `knowledge` | `world_canon` |
| `tool` | `artifact_vault` |
| `complex` | 全量 5 路 |

### 3.2 当前最大轮次

| intent | max_rounds |
|---|---|
| `greeting` | 0 |
| `phatic` | 1 |
| `chat` | 1 |
| `recall_memory` | 2 |
| `emotion` | 2 |
| `affection` | 1 |
| `knowledge` | 1 |
| `tool` | 1 |
| `complex` | 3 |

---

## 四、EvidenceEvaluator 当前阈值

`host_brain/retrieval/evidence_evaluator.py` 当前默认阈值是：

| 维度 | 默认值 |
|---|---|
| 最少证据数 | `2` |
| top_score 下限 | `0.25` |
| coverage 下限 | `0.4` |

当前建议类型有三种：

- `proceed`
- `retry_with_rewrite`
- `expand_stores`

因此，当前 Agentic RAG 不是“单次规划后只跑一次召回”，而是确实会根据评估结果触发：

- 改写 query 重试
- 扩展 store 范围
- 或直接收口

---

## 五、AgenticRetrievalLoop 当前行为

`host_brain/retrieval/agentic_retrieval_loop.py` 当前实现了：

1. 读取已有 `QueryPlan`
2. 若 `skip_retrieval=True`，直接返回 skipped 结果
3. 否则按 `max_rounds` 循环
4. 每轮调用 `RecallFusionService.recall(...)`
5. 汇总 `routes / route_rows / route_errors`
6. 用 `EvidenceEvaluator` 判断是否足够
7. 不足时按建议追加 retry query 或扩展 stores

当前 `AgenticRetrievalResult` 还会输出：

- `queries_used`
- `rounds`
- `final_evaluation`
- `recall_summary`

---

## 六、Orchestrator 与缓存

`host_brain/retrieval/orchestrator.py` 当前除了包装 pipeline，还提供了：

- 统一 request/result 数据类
- `success / partial_failure / all_failure` 路径分类
- LRU 风格缓存
- `retrieval_bundle` 统一产物

当前缓存元数据会进入 `retrieval_summary` / `retrieval_bundle.cache`，包括：

- `cache_enabled`
- `cache_hit`
- `cache_ttl_ms`
- `cache_age_ms`
- `cache_key_scope`

当前缓存 key 的 scope 还显式覆盖：

- `person_id`
- `session_id`
- `query`
- `top_k`
- `intent_id`
- `emotion_context`
- `mood_state`
- `bond_summary`
- `motive_state`
- `render_target_lang`
- `turn_index`
- `query_plan`

---

## 七、主链回灌与 Prompt 过滤

`agent_runtime_shared.py` 当前不会直接把所有 retrieval 结果原样塞进 prompt，而是还会做两步后处理：

### 7.1 Prompt Filter

`chat_retrieval_helpers.filter_retrieval_evidence_items_for_prompt()` 会针对：

- contextual follow-up
- low-signal short chat

做 prompt 证据裁剪，并把结果写回：

- `retrieval_evidence_items`
- `retrieval_summary["prompt_filter"]`

### 7.2 RecallPackBuilder

在 retrieval 完成后，主链还会：

- 把 evidence items 重组为 `recall_results`
- 调 `RecallPackBuilder.build(...)`
- 生成更适合 prompt 槽位注入的 `recall_slot_text`

所以当前正确理解是：

- Agentic RAG 负责“检索决策与证据产出”
- Prompt 注入还要经过主链的二次整理

---

## 八、当前与旧文档的关键差异

以下说法已经不再代表当前代码事实：

- `host_brain/app/graph/main_chain.py` 是 Agentic RAG 的当前注入点
- 当前仍是固定“七路全量召回”
- `style_canon` 与 `persona_utterance` 仍在当前主 store map 内
- 检索结果从 Graph 节点直接进入最终 prompt，没有主链二次过滤

当前实际口径应是：

1. 检索从 `cortex_orchestrator -> hippocampus_agent -> retrieval.orchestrator` 进入。
2. 当前主 store map 是 5 路，不是旧文档里的 7 路。
3. Agentic RAG 已经真实落在 `QueryPlanner + AgenticRetrievalLoop + EvidenceEvaluator + Orchestrator` 上。
4. 主链还会继续做 prompt_filter 和 RecallPackBuilder 整理。
