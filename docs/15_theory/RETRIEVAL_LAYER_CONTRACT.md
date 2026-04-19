受众：MASTER
标题：host_brain/retrieval/ 层接口契约（当前代码实况）

版本：v2.0
日期：2026-04-19
归属：docs/15_theory/

---

定位：本文描述当前代码里的 `host_brain/retrieval/` 层契约，而不是早期“`Embed -> RecallFusion -> Rerank -> EvidencePack` 四段直连”简化口径。
若与旧文档冲突，以本文、`docs/50_project/arch/AGENTIC_RAG_ARCHITECTURE.md` 和实际代码为准。

## 0. 代码证据入口

- `host_brain/retrieval/query_planner.py`
- `host_brain/retrieval/agentic_retrieval_loop.py`
- `host_brain/retrieval/evidence_evaluator.py`
- `host_brain/retrieval/pipeline.py`
- `host_brain/retrieval/orchestrator.py`
- `host_brain/retrieval/recall_fusion.py`
- `host_brain/retrieval/recall_fusion_config.py`
- `host_brain/retrieval/reranker.py`
- `host_brain/retrieval/evidence_pack.py`
- `host_brain/app/chat_retrieval_helpers.py`
- `host_brain/tribe/hippocampus_agent.py`

---

## 一、当前层定位

`host_brain/retrieval/` 现在不是一个“被动四段式工具箱”，而是当前 Agentic RAG 主体：

```text
/v1/chat 或 /v1/host/input
  -> agent_runtime_shared / chat_retrieval_helpers
  -> HippocampusAgent.recall
  -> RetrievalOrchestrator.arun
  -> RetrievalPipelineContract.run
```

这意味着：

- 当前检索入口已经不在 `main_chain.py`
- `retrieval/` 层负责 QueryPlanner、可多轮 recall、证据评估、去重、重排、EvidencePack 和缓存包装
- prompt 注入前还会由主链继续做 `prompt_filter` 与 `RecallPackBuilder`

---

## 二、模块清单（当前主模块）

| 模块文件 | 当前职责 |
|---|---|
| `query_planner.py` | 纯规则规划：意图识别、store 路由、改写 query、决定 `skip_retrieval / max_rounds / time_lookback_days` |
| `agentic_retrieval_loop.py` | 多轮检索循环：执行 recall、读取评估结果、必要时改写 query 或扩 store |
| `evidence_evaluator.py` | 检查当前证据是否足够，给出 `proceed / retry_with_rewrite / expand_stores` |
| `pipeline.py` | 检索总管线：Embedding → Agentic loop → SemanticDedup → Rerank → EvidencePack |
| `orchestrator.py` | 统一请求/结果契约、线程池包装、路径分类、缓存、`retrieval_bundle` 汇总 |
| `recall_fusion.py` | 多 store recall 执行 |
| `recall_fusion_config.py` | recall 路由配置加载与 node/language 过滤 |
| `reranker.py` | 候选重排 |
| `evidence_pack.py` | 证据包构造 |
| `embedding.py` | embedding provider 路由与 fallback |

---

## 三、QueryPlanner 当前契约

### 3.1 当前 store map

`query_planner.py` 当前 `ALL_STORE_IDS` 为 5 路：

- `episodic_vector`
- `sqlite_ledger`
- `neo4j_bond`
- `world_canon`
- `artifact_vault`

文件内已明确：

- `style_canon` 不再走这里的检索路由，而是常驻注入
- `persona_utterance` 不在当前主 store map 内，而走独立通道

### 3.2 意图到 store 的当前路由

| intent | target_stores |
|---|---|
| `greeting` | 空，跳过检索 |
| `phatic` | `episodic_vector` |
| `chat` | `episodic_vector` |
| `recall_memory` | `episodic_vector`, `sqlite_ledger` |
| `emotion` | `neo4j_bond`, `episodic_vector` |
| `affection` | `neo4j_bond`, `episodic_vector` |
| `knowledge` | `world_canon` |
| `tool` | `artifact_vault` |
| `complex` | 全量 5 路 |

### 3.3 最大轮次

| intent | max_rounds |
|---|---|
| `greeting` | `0` |
| `phatic` | `1` |
| `chat` | `1` |
| `recall_memory` | `2` |
| `emotion` | `2` |
| `affection` | `1` |
| `knowledge` | `1` |
| `tool` | `1` |
| `complex` | `3` |

### 3.4 规划结果数据类

当前 `QueryPlan` 的关键字段：

```python
QueryPlan(
    original_query: str,
    rewritten_queries: list[str],
    intent: str,
    target_stores: list[str],
    skip_retrieval: bool,
    max_rounds: int,
    time_lookback_days: int | None,
)
```

---

## 四、RetrievalPipelineContract 当前契约

### 4.1 真实执行顺序

当前 `RetrievalPipelineContract.run()` 不再是旧文档里的四步直连，而是：

```text
QueryPlan 归一化
  -> EmbeddingService.embed
  -> AgenticRetrievalLoop.retrieve
       -> RecallFusionService.recall
       -> EvidenceEvaluator.evaluate
  -> SemanticDedup.dedup
  -> RerankerService.rerank
  -> EvidencePackService.build_pack
  -> output 汇总
```

其中：

- `AgenticRetrievalLoop` 会根据评估结果决定是否追加一轮
- `phatic / affection` 这类低风险路径可能走 fast-path rerank
- `SemanticDedup` 已经是当前主管线的一环

### 4.2 `run()` 关键输入

当前 `run()` 会接收的不只是 query，还包括：

- `request_id`
- `query`
- `intent_id`
- `person_id`
- `session_id`
- `top_k`
- `emotion_context`
- `mood_state`
- `bond_summary`
- `motive_state`
- `render_target_lang`
- `turn_index`
- `query_plan`
- `route_target`

因此当前 retrieval 层已显式感知：

- 对话身份与会话
- 情感/Bond 相关上下文
- 目标语言
- 上游已生成的 query_plan

### 4.3 `RetrievalPipelineResult`

```python
RetrievalPipelineResult(
    success: bool,
    status: str,
    reason_code: str | None,
    stage: str,
    detail: str,
    output: dict[str, Any],
)
```

当前 `output` 中的核心块包括：

- `embedding`
- `recall`
- `rerank`
- `evidence_pack`
- `query_plan`
- `agentic_meta`
- `adaptive_budget`
- `reason_chain`

---

## 五、EvidenceEvaluator 与 Agentic loop

### 5.1 当前评估阈值

`evidence_evaluator.py` 当前默认阈值：

| 维度 | 默认值 |
|---|---|
| 最少证据数 | `2` |
| top_score 下限 | `0.25` |
| coverage 下限 | `0.4` |

### 5.2 当前建议类型

- `proceed`
- `retry_with_rewrite`
- `expand_stores`

### 5.3 AgenticRetrievalLoop 当前输出

`agentic_retrieval_loop.py` 会产出：

- `queries_used`
- `rounds`
- `final_evaluation`
- `recall_summary`
- `routes / route_rows / route_errors`

这说明当前 retrieval 层已经真实具备“先检索、再评估、必要时二次检索”的 agentic 行为。

---

## 六、RetrievalOrchestrator 当前契约

### 6.1 请求对象

当前 `RetrievalOrchestratorRequest` 关键字段：

```python
RetrievalOrchestratorRequest(
    trace_id: str,
    query: str,
    session_state: dict[str, Any],
    request_id: str,
    person_id: str | None = None,
    session_id: str | None = None,
    intent_id: str | None = None,
    top_k: int = 8,
    ts_ms: int | None = None,
    emotion_context: str | None = None,
    mood_state: str | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
    turn_index: int = 0,
    query_plan: QueryPlan | Mapping[str, Any] | None = None,
    route_target: str = "",
)
```

### 6.2 结果对象

```python
RetrievalOrchestratorResult(
    success: bool,
    status: str,
    reason_code: str | None,
    path: str,
    detail: str,
    trace_id: str,
    query: str,
    session_state: dict[str, Any],
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_bundle: dict[str, Any],
)
```

### 6.3 路径分类

当前路径分类仍是：

- `success`
- `partial_failure`
- `all_failure`

但其依据已经是当前 summary 字段：

- `status == "OK"` 且 `degraded == False` -> `success`
- 有 route/candidate/rerank/evidence 任一残留 -> `partial_failure`
- 否则 -> `all_failure`

### 6.4 summary 统一字段

`build_summary_from_pipeline_result()` 当前会产出并汇总：

- `route_count / routes / route_rows / route_errors`
- `candidate_count`
- `rerank_topk`
- `evidence_refs / evidence_topk`
- `query_plan`
- `agentic_meta`
- `adaptive_budget`
- `embedding_model_id / embedding_provider / fallback_embedding_*`
- `rerank_model_id / rerank_provider / fallback_reranker_*`

---

## 七、跳过、降级与缓存

### 7.1 跳过/降级摘要

当前 `orchestrator.py` 提供：

- `build_skipped_retrieval_summary(reason_code)`
- `build_degraded_retrieval_summary(reason_code)`

其中：

- 跳过态 `status = "SKIPPED"`
- 降级态 `status = "DEGRADED"`

### 7.2 缓存

`RetrievalOrchestrator` 当前内置 LRU 风格缓存，关键配置：

- `cache_enabled`
- `cache_ttl_ms`
- `cache_max_entries`

缓存 key scope 当前显式覆盖：

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

缓存元数据会回写到：

- `retrieval_summary`
- `retrieval_bundle.cache`

---

## 八、环境构建入口

`build_retrieval_pipeline_from_env()` 仍是当前标准工厂函数。它当前会装配：

- embedding runtime config
- recall runtime config
- reranker runtime config
- Chroma runtime config
- `ChromaHttpSearchClient`
- Neo4j bond client（启用时）
- `StyleCanonStore`
- `WorldCanonStore`
- `ArtifactVault`
- `QueryPlanner`
- `AdaptivePromptBudget`
- `SemanticDedup`

需要注意：

- recall 适配层支持的 store id 以 `recall_fusion_config.py` 为准
- 但当前主 `QueryPlanner` 默认只规划上文列出的 5 路 store

---

## 九、与主链的当前关系

当前 retrieval 层不会直接把所有证据原样塞进 prompt。

在 `chat_retrieval_helpers.py` / `agent_runtime_shared.py` 里，retrieval 结果还会经过：

1. `filter_retrieval_evidence_items_for_prompt()`
2. `RecallPackBuilder.build(...)`

所以当前正确理解是：

- `retrieval/` 负责“检索与证据组织”
- prompt 注入仍由 app 主链继续收口

---

## 十、当前不再适用的旧口径

以下说法不再代表当前代码事实：

- retrieval 仍是固定 `Embed -> RecallFusion -> Rerank -> EvidencePack` 四步直连
- 当前主入口仍是 `main_chain.py`
- 当前仍是固定“七路全量召回”
- `style_canon` / `persona_utterance` 仍在当前主 store map 内
- retrieval 结果会不经筛选直接进入最终 prompt

当前正确口径应是：

1. 当前主入口是 `chat_retrieval_helpers -> HippocampusAgent -> RetrievalOrchestrator`。
2. `QueryPlanner + AgenticRetrievalLoop + EvidenceEvaluator` 已是当前主检索结构。
3. 当前主 store map 是 5 路。
4. retrieval 之后主链还会继续做 `prompt_filter` 和 `RecallPackBuilder`。
