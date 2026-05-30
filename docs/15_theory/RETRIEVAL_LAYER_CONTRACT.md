受众：MASTER
标题：host_brain/retrieval/ 层接口契约（当前代码实况）

版本：v3.0
日期：2026-05-30
归属：docs/15_theory/

---

定位：本文描述当前 `host_brain/retrieval/` 层的真实契约。**v2.0 描述的 Agentic RAG pipeline / orchestrator stack（QueryPlanner / RetrievalPipelineContract / EvidenceEvaluator / AgenticRetrievalLoop / RetrievalOrchestrator）已从代码中整体移除**，请以本文与代码为准。

## 0. 代码证据入口

- `host_brain/retrieval/__init__.py`
- `host_brain/retrieval/recall_fusion.py`（`RecallFusionServiceContract`）
- `host_brain/retrieval/recall_fusion_stores.py`（多路 store 搜索实现）
- `host_brain/retrieval/recall_fusion_config.py`（`RecallRuntimeConfig` / `RecallRouteConfig` 装载）
- `host_brain/retrieval/recall_fusion_utils.py`（候选排序、过滤、规整工具）
- `host_brain/retrieval/full_text_searcher.py`（FTS5 全文检索）
- `host_brain/retrieval/hybrid_fusion.py`（`HybridFusionStrategy` 向量 + FTS 融合）
- `host_brain/retrieval/recall_schemas.py`（数据 schema）
- `host_brain/retrieval/audit_preview.py`（审计预览）
- `host_brain/persona/surface_memory_recall.py`（消费者：surface 主链记忆召回）

---

## 一、当前层定位

retrieval 层只剩一件事：**多路召回 + 混合融合 + 候选过滤**，结果上交给 `surface_memory_recall` 决定是否注入主链。

```
RecallFusionServiceContract.recall(query, embedding, intent_id, ...)
  ├── 多路 store 搜索（vector / FTS / canon / artifact / life_episode / semantic / bond）
  ├── 混合融合（HybridFusionStrategy：vector_weight + fts_weight）
  ├── 候选过滤（anaphoric selection / specific item reference / broad collection / deep review）
  └── 输出 RecallFusionResult（候选列表 + meta）
↓
host_brain/persona/surface_memory_recall.py
  ├── 决定 RECALL_POLICY_MUST / MAY / NO
  ├── 应用 surface_memory_recall_decision_matrix
  └── 输出 MemoryRecallPacket（必要事实 + 时间锚 + 引语，已压缩）
↓
SurfaceRuntime（接收 fact list，不接收原始 candidate）
```

retrieval 层**不**再做：

- ❌ 多步 agent loop 检索（旧 `AgenticRetrievalLoop`）
- ❌ 复杂的 evidence evaluator + 建议机制
- ❌ orchestrator 路径分类
- ❌ rerank-as-a-service
- ❌ 给 main chain 直接喂候选

---

## 二、模块清单（当前主模块）

| 文件 | 当前职责 |
|---|---|
| `__init__.py` | 仅导出 `RecallFusionServiceContract` 及配置类型，移除旧 Agentic stack 导出 |
| `recall_fusion.py` | `RecallFusionServiceContract` 主类（recall 入口、路由分发、混合融合、anaphoric 过滤） |
| `recall_fusion_stores.py` | 各路 store 搜索的具体实现（sqlite_ledger / life_episode / semantic_memory / neo4j_bond / world_canon / artifact_vault） |
| `recall_fusion_config.py` | `load_recall_runtime_config(...)` + `resolve_recall_routes(...)`：从配置文件加载多路召回路由 |
| `recall_fusion_utils.py` | 候选排序、规整、文本相似度等纯函数 |
| `full_text_searcher.py` | `FullTextSearcher`（FTS5 全文检索器） |
| `hybrid_fusion.py` | `HybridFusionStrategy`（vector + FTS 加权融合） |
| `recall_schemas.py` | `RecallFusionResult` / `RecallRouteConfig` / `RecallRuntimeConfig` 等 schema |
| `audit_preview.py` | 召回结果的审计预览 |

---

## 三、`RecallFusionServiceContract` 当前契约

### 3.1 构造参数

```python
RecallFusionServiceContract(
    *,
    ledger: SQLiteLedger | None = None,          # 审计 ledger（用于 sqlite_ledger / life_episode 路由）
    chroma_client: ChromaClientProtocol | None = None,  # Chroma 向量库
    neo4j_client: Any | None = None,              # Bond 图（可选）
    world_store: Any | None = None,                # World canon 存储
    artifact_vault: Any | None = None,             # Artifact 库
    runtime_config: RecallRuntimeConfig | None = None,  # 多路路由配置
    node_id: str | None = None,                    # 节点选择器（按节点过滤路由）
    available: bool = True,
    use_hybrid: bool = True,                       # 是否启用 vector + FTS 混合
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    fts_weight: float = DEFAULT_FTS_WEIGHT,
    fts_db_path: str | None = None,
)
```

### 3.2 当前 store 路由

由 `RecallRouteConfig` 配置启用，store 名空间：

| Store name | 实现 | 来源 |
|---|---|---|
| `sqlite_ledger` | `_search_sqlite_ledger` | `audit/sqlite_ledger`（Recall ops 审计落盘） |
| `life_episode` | `_search_life_episode` | `memory/life_memory` 的 SQLite life episode 表 |
| `semantic_memory` | `_search_semantic_memory` | 长期蒸馏出的语义记忆 |
| `neo4j_bond` | `_search_neo4j_bond` | Bond 关系图（可选） |
| `world_canon` | `_search_world_canon` | World canon vector 存储 |
| `artifact_vault` | `_search_artifact_vault` | Artifact 库（生成产物） |

每条路由由 `RecallRouteConfig` 描述：

- `name`（store 名）
- `top_k`（每路返回数）
- `node_selector`（按 `node_id` 过滤生效节点）
- `render_target_lang_selector`（按渲染目标语言过滤）
- 其它 store 专属参数（如 `collection` for world_canon）

### 3.3 `recall(...)` 入口

```python
def recall(
    self,
    *,
    request_id: str,
    query: str,
    query_embedding: list[float] | tuple[float, ...] | Any,
    intent_id: str | None = None,
    intent_kind: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    render_target_lang: str | None = None,
    now_ts_ms: int | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> RecallFusionResult: ...
```

执行步骤：

1. 加载 runtime config → 解析当前节点 / 语言下生效的路由列表
2. 对每条路由调用对应 `_search_*`，得到该路 candidate
3. 若 `use_hybrid=True`，对 vector candidate 调用 `_apply_hybrid_fusion` 与 FTS 融合
4. 走过滤链：
   - `_filter_anaphoric_selection_candidates`：处理"那个 / 这个 / 之前那本"等指代性 query
   - `_select_recent_anaphoric_life_primary`：从 life episode 中选择最相关的一条
   - `_query_looks_like_specific_item_reference`：是否像在指特定条目
   - `_looks_like_broad_collection_selection_query`：是否在选某类集合
   - `_query_asks_about_deep_review`：是否在问深度评审 / 二次审稿
5. 输出 `RecallFusionResult`

### 3.4 `RecallFusionResult` 主要字段

- `candidates: list[dict[str, Any]]`：融合后的候选列表
- `meta: dict[str, Any]`：路由命中、过滤命中、性能 timing
- `query_features: dict[str, Any]`：query 解析特征（anaphoric / deep_review / specific_item / broad_collection）
- `policy_hint: str | None`：给 surface_memory_recall 的提示（不强制）

---

## 四、混合融合（HybridFusionStrategy）

`hybrid_fusion.HybridFusionStrategy.fuse(vector_candidates, fts_candidates)`：

- 默认权重：`vector_weight + fts_weight = 1.0`，由 `DEFAULT_VECTOR_WEIGHT` / `DEFAULT_FTS_WEIGHT` 给定
- 同 ID 候选取最大融合分（vector_score * vector_weight + fts_score * fts_weight）
- 不同 ID 候选保留两侧 union
- 输出按融合分降序

`use_hybrid=False` 或 `_fts_searcher is None` 时，直接返回 vector candidates，不做融合。

---

## 五、候选过滤（query feature 决策）

retrieval 层只在"必要时"按 query 特征做候选筛选；**不做语义重排**。

| Query 特征 | 检测函数 | 行为 |
|---|---|---|
| Anaphoric selection（"那个/这个 X"） | `_looks_like_anaphoric_selection_query` | 调 `_filter_anaphoric_selection_candidates` 限制候选范围 |
| Recent anaphoric life | `_select_recent_anaphoric_life_primary` | 优先返回最近一条 life episode |
| Specific item reference（特定标题） | `_query_looks_like_specific_item_reference` | `_candidate_matches_specific_item_reference` 强制命中 |
| Broad collection selection | `_looks_like_broad_collection_selection_query` | 保留集合类候选 |
| Deep review query | `_query_asks_about_deep_review` | 倾向 deep review artifact 路由 |
| Anaphoric scheduler noise | `_looks_like_anaphoric_scheduler_noise` | 抑制 scheduler 噪声候选 |

具体打分见 `_anaphoric_primary_sort_key` 与 `_candidate_anaphoric_action_priority`。

---

## 六、配置加载（recall_fusion_config）

```python
load_recall_runtime_config(
    config_path: str | Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> RecallConfigLoadResult
```

返回：

- `config: RecallRuntimeConfig`
- `issues: list[RecallConfigIssue]`：装载告警（不抛异常）
- `source: dict[str, Any]`：来源信息（用于审计）

```python
resolve_recall_routes(
    config: RecallRuntimeConfig,
    *,
    node_id: str | None,
    render_target_lang: str | None,
) -> list[RecallRouteConfig]
```

按 `node_selector` 与 `render_target_lang_selector` 过滤后返回当前节点 / 语言下生效的路由。

---

## 七、与 `surface_memory_recall` 的边界

| 职责 | 由谁完成 |
|---|---|
| 多路召回 + 混合融合 + 过滤 | `RecallFusionServiceContract`（retrieval 层） |
| 决定 RECALL_POLICY（MUST / MAY / NO） | `surface_memory_recall`（persona 层） |
| 应用 `surface_memory_recall_decision_matrix` | persona 层 |
| 候选 → MemoryRecallPacket（事实压缩） | persona 层 |
| 注入 ReplyControlPacket / SurfaceRuntime | persona 层 |
| 写最终回复 | `SurfaceMainChain`（persona 层） |

retrieval 层**不**判断"该不该召回"，**不**写最终回复，**不**做事实压缩；它只负责"召回有什么候选可以用"。

---

## 八、不变量

1. retrieval 层不直接写 chat 主链 reply；只产出 candidates。
2. 旧 Agentic RAG stack（QueryPlanner / RetrievalPipelineContract / EvidenceEvaluator / AgenticRetrievalLoop / RetrievalOrchestrator）已**全部移除**，相关 import 必须报错。
3. retrieval 层不知道 ReplyControlPacket / SurfaceRuntime 存在，只与 `surface_memory_recall` 通过 `RecallFusionResult` 通信。
4. 混合融合在 store 层之上、过滤之前；不允许跨层。
5. 路由配置由 `RecallRuntimeConfig` 集中管理，不允许在调用方临时拼路由。
6. `SQLiteLedger` 是 ledger 路由的唯一来源，retrieval 层不应直接去 SQL 拼表。

---

## 九、不再代表当前事实的旧口径

| 旧描述 | 当前事实 |
|---|---|
| `QueryPlanner` 解析意图到 store map | 已删除；当前由 `recall_fusion_config.resolve_recall_routes` + `intent_id` 上下文决定 |
| `RetrievalPipelineContract.run()` 四阶段 | 已删除；统一为 `RecallFusionServiceContract.recall(...)` 一次调用 |
| `EvidenceEvaluator` 阈值与建议 | 已删除；过滤改由 query feature 函数完成 |
| `AgenticRetrievalLoop` 多步 agent 检索 | 已删除；不再有 agent loop |
| `RetrievalOrchestrator` 路径分类 | 已删除；当前不存在 orchestrator |
| 旧 `style_canon` / `persona_utterance` 召回路由 | 已删除（commit 显式 retire） |
| retrieval 层会 rerank | 不会；rerank 在 surface chain 内部按 affect / continuity / quality 完成 |
