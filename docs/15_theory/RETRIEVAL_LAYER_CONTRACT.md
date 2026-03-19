受众：MASTER
标题：host_brain/retrieval/ 层接口契约

版本：v1.0
日期：2026-03-19
归属：docs/15_theory/

---

## 概述

`host_brain/retrieval/` 是 Natsume AI 的检索层，负责将用户查询转化为结构化的证据包（Evidence Pack），供 Prompt 构建层使用。整个检索链路分为四个串行阶段：

```
Embed → RecallFusion → Rerank → EvidencePack
```

所有阶段均通过 `RetrievalPipelineContract` 串联，对外暴露统一的 `RetrievalOrchestrator` 入口。

---

## 一、模块清单

| 模块文件 | 职责 |
|---------|------|
| `orchestrator.py` | 检索编排器：统一入口、缓存、路径分类 |
| `pipeline.py` | 检索管道契约：四阶段串联执行 |
| `embedding.py` | 嵌入服务：文本向量化 |
| `recall_fusion.py` | 多路召回融合服务 |
| `recall_fusion_config.py` | 召回融合配置加载 |
| `recall_fusion_stores.py` | 召回存储适配器（Chroma/Neo4j/Canon/Vault） |
| `recall_fusion_utils.py` | 召回融合工具函数 |
| `recall_schemas.py` | 召回结果 Schema 定义 |
| `recall_search_mixin.py` | 召回搜索 Mixin |
| `recall_config.py` | 召回运行时配置 |
| `reranker.py` | 重排序服务契约 |
| `reranker_config.py` | 重排序运行时配置 |
| `reranker_providers.py` | 重排序提供商注册表 |
| `reranker_schemas.py` | 重排序结果 Schema |
| `evidence_pack.py` | 证据包构建服务 |
| `hybrid_fusion.py` | 混合融合算法（RRF 等） |
| `full_text_searcher.py` | 全文检索适配器 |

---

## 二、RetrievalOrchestrator（编排器）

### 2.1 入口契约

```python
class RetrievalOrchestratorRequest:
    trace_id: str          # 追踪 ID
    query: str             # 用户查询文本
    session_state: dict    # 会话状态（含 session_id 等）
    request_id: str        # 请求 ID
    session_id: str | None
    intent_id: str | None  # 意图 ID（用于缓存键）
    top_k: int = 5         # 最终返回证据条数（范围 1–20）
    ts_ms: int | None      # 时间戳（毫秒）
    emotion_context: str | None  # 情感上下文（OPT-41 透传）
```

```python
class RetrievalOrchestratorResult:
    success: bool
    status: str                        # "OK" | "DEGRADED"
    reason_code: str | None
    path: str                          # 见路径分类
    detail: str
    trace_id: str
    query: str
    session_state: dict
    retrieval_summary: dict            # 检索摘要（含缓存元数据）
    retrieval_evidence_items: list     # 最终证据条目列表
    retrieval_bundle: dict             # 完整检索包（含 summary + evidence_items + cache）
```

### 2.2 路径分类

| 路径常量 | 含义 |
|---------|------|
| `success` | 全部阶段正常，有效证据 ≥ 1 |
| `partial_failure` | 部分阶段降级，但仍有候选结果 |
| `all_failure` | 全部阶段失败，无任何候选 |

分类逻辑（`_classify_retrieval_path`）：
- `status == "OK"` 且 `degraded == False` → `success`
- `route_count > 0` 或 `candidate_count > 0` 或 `rerank_topk > 0` 或 `evidence_items > 0` → `partial_failure`
- 其余 → `all_failure`

### 2.3 缓存机制

编排器内置 LRU 缓存（可选启用）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cache_enabled` | `False` | 是否启用缓存 |
| `cache_ttl_ms` | `3000` | 缓存 TTL（毫秒），范围 0–600000 |
| `cache_max_entries` | `256` | 最大缓存条目数，范围 1–20000 |

**缓存键范围**：`session_id + query + top_k + intent_id + emotion_context`（SHA-256）

**缓存约束**：
- 仅缓存 `status == "OK"` 的结果
- 降级结果（`DEGRADED`）不写入缓存
- 超时（`asyncio.TimeoutError`）不写入缓存

缓存元数据注入到 `retrieval_bundle.cache`：
```json
{
  "enabled": true,
  "hit": false,
  "ttl_ms": 3000,
  "age_ms": 0,
  "max_entries": 256,
  "key_scope": "session_id+query+top_k+intent_id+emotion_context"
}
```

### 2.4 降级摘要

| 函数 | 触发场景 |
|------|---------|
| `build_skipped_retrieval_summary("HOST_CHAT_RETRIEVAL_DISABLED")` | 检索功能被禁用 |
| `build_degraded_retrieval_summary("RETRIEVAL_TIMEOUT")` | 超时 |
| `build_degraded_retrieval_summary("RETRIEVAL_FAILED")` | 异常 |

降级摘要结构：
```json
{
  "status": "DEGRADED",
  "reason_code": "RETRIEVAL_TIMEOUT",
  "route_count": 0,
  "routes": [],
  "route_rows": [],
  "route_errors": [],
  "candidate_count": 0,
  "rerank_topk": 0,
  "evidence_refs": [],
  "reason_chain": [],
  "degraded": true
}
```

---

## 三、RetrievalPipelineContract（管道）

### 3.1 四阶段串联

```
query
  ↓ EmbeddingServiceContract.embed()
embedding_vector
  ↓ RecallFusionServiceContract.recall()
recall_candidates
  ↓ RerankerServiceContract.rerank()
rerank_topk
  ↓ EvidencePackServiceContract.build_pack()
evidence_pack
```

### 3.2 管道输出结构

```python
class RetrievalPipelineResult:
    success: bool
    status: str          # "OK" | "DEGRADED"
    reason_code: str | None
    stage: str           # "S_RET_PIPELINE"
    detail: str
    output: dict         # 含 embedding/recall/rerank/evidence_pack/reason_chain
```

`output` 完整字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | str | 请求 ID |
| `intent_id` | str\|None | 意图 ID |
| `query` | str | 归一化查询文本 |
| `status` | str | "OK"\|"DEGRADED" |
| `stage` | str | "S_RET_PIPELINE" |
| `reason_code` | str\|None | 首个降级原因码 |
| `reason_chain` | list[str] | 所有阶段降级原因码链 |
| `degraded` | bool | 是否有任意阶段降级 |
| `fallback_embedding_used` | bool | 是否使用了降级嵌入 |
| `embedding` | dict | 嵌入阶段输出 |
| `recall` | dict | 召回阶段输出 |
| `rerank` | dict | 重排序阶段输出 |
| `evidence_pack` | dict | 证据包阶段输出 |
| `ts_ms` | int | 执行时间戳 |

### 3.3 降级传播规则

- 任意阶段返回非空 `reason_code` → `degraded = True`
- `reason_chain` 收集所有阶段的 `reason_code`（去重）
- `root_reason` = `reason_chain[0]`（首个降级原因）
- 最终 `status = "OK"` 当且仅当 `degraded == False` 且 `evidence_pack_result.success == True`

### 3.4 情感上下文透传（OPT-41）

`emotion_context` 参数从 `RetrievalOrchestratorRequest` 透传至 `RerankerServiceContract.rerank()`，用于情感感知重排序。

---

## 四、EmbeddingServiceContract（嵌入服务）

### 4.1 接口

```python
embedding_service.embed(
    request_id: str,
    text: str,
    intent_id: str | None,
    ts_ms: int,
) -> EmbeddingResult
```

### 4.2 输出

```python
EmbeddingResult.output = {
    "embedding_vector": list[float],  # 嵌入向量
    ...
}
```

### 4.3 运行时配置（`load_embedding_runtime_config`）

从 `.env` 加载，失败时抛出 `RuntimeError`。

---

## 五、RecallFusionServiceContract（多路召回融合）

### 5.1 接口

```python
recall_service.recall(
    request_id: str,
    query: str,
    query_embedding: list[float],
    intent_id: str | None,
    session_id: str | None,
    ts_ms: int,
) -> RecallResult
```

### 5.2 召回路由

召回融合服务支持多路并行召回，路由来源包括：

| 路由 | 存储后端 | 说明 |
|------|---------|------|
| `chroma` | ChromaDB | 情节记忆向量检索 |
| `neo4j` | Neo4j | Bond 关系图检索 |
| `style_canon` | StyleCanonStore | 风格 Canon 检索 |
| `world_canon` | WorldCanonStore | 世界观 Canon 检索 |
| `artifact_vault` | ArtifactVault | 工件库检索 |

### 5.3 输出结构

```python
RecallResult.output = {
    "routes": list[str],          # 激活的路由名称
    "route_rows": list[dict],     # 各路由原始结果行
    "route_errors": list[dict],   # 各路由错误信息
    "candidates": list[dict],     # 融合后候选列表
    "candidate_count": int,
}
```

### 5.4 ChromaHttpSearchClient

管道内置 Chroma HTTP 客户端适配器：

```python
ChromaHttpSearchClient.search(
    query_embedding: list[float],
    top_k: int = 5,
    collection: str | None = None,
) -> list[dict]
```

距离转分数：`score = 1.0 / (1.0 + distance)`（`_distance_to_score`）

---

## 六、RerankerServiceContract（重排序服务）

### 6.1 接口

```python
reranker_service.rerank(
    request_id: str,
    query: str,
    candidates: list[dict],
    intent_id: str | None,
    top_k: int | None,
    ts_ms: int,
    emotion_context: str | None,  # OPT-41
) -> RerankerResult
```

### 6.2 输出结构

```python
RerankerResult.output = {
    "topk": list[dict],   # 重排序后 top-k 结果
    ...
}
```

每条 `topk` 条目：

| 字段 | 类型 | 说明 |
|------|------|------|
| `evidence_ref` | str | 证据引用 ID |
| `source_ref` | str | 来源引用 |
| `snippet` | str | 文本片段（截断至 240 字符） |
| `score` | float | 重排序分数 |
| `confidence` | float\|None | 置信度 |
| `metadata` | dict | 元数据 |
| `store_id` | str\|None | 存储 ID |

---

## 七、EvidencePackServiceContract（证据包服务）

### 7.1 接口

```python
evidence_pack_service.build_pack(
    request_id: str,
    intent_id: str | None,
    query: str,
    rerank_output: dict,
    degraded: bool,
    reason_code: str | None,
    ts_ms: int,
) -> EvidencePackResult
```

### 7.2 输出结构

```python
EvidencePackResult.output = {
    "evidence_refs": list[str],   # 证据引用 ID 列表
    ...
}
```

---

## 八、build_retrieval_pipeline_from_env（工厂函数）

从环境变量构建完整检索管道：

```python
pipeline = build_retrieval_pipeline_from_env(
    env=None,              # 环境变量字典，None 时使用 os.environ
    local_overrides=None,  # 本地覆盖配置
    ledger=None,           # SQLiteLedger 审计实例
    node_id=None,          # 节点 ID（默认 "host.default"）
    key_profiles=None,     # 密钥配置文件列表
    secret_store=None,     # 密钥存储
    current_epoch=None,    # 当前 epoch
) -> RetrievalPipelineContract
```

**构建顺序**：
1. 加载 `embedding_cfg`（失败则抛出 RuntimeError）
2. 加载 `recall_cfg`（失败则抛出 RuntimeError）
3. 加载 `rerank_cfg`（失败则抛出 RuntimeError）
4. 加载 `chroma_cfg`（失败则抛出 RuntimeError）
5. 加载 `host_cfg`（解析 OpenAI API Key）
6. 构建 `embedding_registry`、`reranker_registry`
7. 构建 `neo4j_client`（可选，失败时降级为 None）
8. 构建 `chroma_client`、`style_store`、`world_store`、`artifact_vault`
9. 组装 `RetrievalPipelineContract`

---

## 九、关键约束

1. **top_k 范围**：`_normalize_top_k` 强制限制在 `[1, 20]`，默认 5
2. **snippet 截断**：`_extract_evidence_items` 截断至 240 字符（`_truncate_for_prompt`）
3. **最大证据条数**：`_extract_evidence_items` 最多取 `rerank_topk[:5]`
4. **缓存仅对 OK 结果生效**：降级结果不写入缓存
5. **情感上下文透传**：`emotion_context` 从 Orchestrator → Pipeline → Reranker 全链路透传
6. **审计写入**：`RetrievalPipelineContract._log_event` 在每次 `run()` 后写入 Ledger

---

## 十、与其他层的接口关系

```
prompt_context.py (core/)
    ↓ 调用
RetrievalOrchestrator.arun()
    ↓ 返回
retrieval_bundle → prompt_builder.py (app/)
    ↓ 注入
GraphState.retrieval_bundle (integrations/)
```

- `retrieval_bundle` 是 `GraphState` 的标准字段，由 `graph_contract.py` 消费
- 检索层不直接写入 `GraphState`，由 `graph_contract` 中的节点函数负责注入
- 检索层的 `evidence_refs` 由 `EvidencePlane` 写入审计（见 `INTEGRATIONS_LAYER_CONTRACT.md`）
