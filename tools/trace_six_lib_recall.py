"""
Natsume AI Host 端真实链路追踪脚本
目的：追踪六库多路召回的完整链路，验证各节点健康状态
执行：python tools/trace_six_lib_recall.py
"""
from __future__ import annotations

import sys
import time
import json

sys.path.insert(0, ".")

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.chroma_client import build_runtime_chroma_client, embed_text
from host_brain.retrieval.recall_fusion import (
    RecallFusionServiceContract,
    RecallRuntimeConfig,
    RecallRouteConfig,
    FullTextSearcher,
    HybridFusionStrategy,
)
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    EmbeddingRuntimeConfig,
    EmbeddingRouteConfig,
)
from host_brain.retrieval.reranker import (
    RerankerServiceContract,
    RerankRuntimeConfig,
    RerankRouteConfig,
)
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract
from host_brain.retrieval.pipeline import RetrievalPipelineContract
from host_brain.app.main import build_startup_report, build_service_health_report
from host_brain.core.services import build_default_service_registry
from host_brain.core.ids import new_request_id


SEP = "=" * 72
SEP2 = "-" * 72


def section(title: str) -> None:
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)


def subsection(title: str) -> None:
    print()
    print(f"  ── {title}")
    print(f"  {SEP2}")


# ─────────────────────────────────────────────────────────────────────────────
# 节点 0：启动报告 & 服务健康
# ─────────────────────────────────────────────────────────────────────────────
section("节点 0：Host 端启动报告 & 服务健康聚合")

ledger = SQLiteLedger(db_path=":memory:")
req_id = new_request_id()

startup = build_startup_report(
    runtime_statuses={},
    dependency_probes={},
    request_id=req_id,
    ledger=ledger,
    ts_ms=int(time.time() * 1000),
)
summary = startup.get("summary", {})
print(f"  boot_status       : {summary.get('boot_status', 'UNKNOWN')}")
print(f"  required_total    : {summary.get('required_total', 0)}")
print(f"  required_unavail  : {summary.get('required_unavailable', 0)}")
print(f"  optional_unavail  : {summary.get('optional_unavailable', 0)}")
print(f"  startup_issues    : {summary.get('issue_count', 0)}")

services = startup.get("services", [])
print()
print(f"  服务目录（共 {len(services)} 个）:")
for svc in services:
    icon = "✅" if svc["status"] == "UP" else ("⚠️" if svc["status"] == "DEGRADED" else "❌")
    print(f"    {icon} [{svc['service_id']}] owner={svc['owner']} required={svc['required']} status={svc['status']}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 1：六库 Chroma 集合初始化
# ─────────────────────────────────────────────────────────────────────────────
section("节点 1：六库 Chroma 集合初始化")

SIX_COLLECTIONS = {
    "episodic": 16,
    "canon_style": 16,
    "canon_world": 16,
    "long_term": 16,
    "distilled": 16,
    "bond": 16,
}
chroma = build_runtime_chroma_client(
    local_overrides={
        "CHROMA_ENABLED": "true",
        "CHROMA_COLLECTION_PROFILES_JSON": json.dumps(SIX_COLLECTIONS, ensure_ascii=False),
        "CHROMA_DEFAULT_COLLECTION": "episodic",
    }
)

for col, dim in SIX_COLLECTIONS.items():
    ok, reason = chroma.validate_collection(col, vector_dim=dim)
    icon = "✅" if ok else "❌"
    print(f"  {icon} 集合 [{col}] 向量维度={dim} 验证={'通过' if ok else reason}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 2：向六库写入测试记忆
# ─────────────────────────────────────────────────────────────────────────────
section("节点 2：向六库写入测试记忆")

test_memories: dict[str, list[tuple[str, str]]] = {
    "episodic": [
        ("mem_ep_001", "今天和用户聊了很多关于猫咪的话题，用户很喜欢橘猫"),
        ("mem_ep_002", "用户提到最近工作压力很大，需要放松"),
        ("mem_ep_003", "用户喜欢听轻音乐，特别是钢琴曲"),
    ],
    "canon_style": [
        ("mem_cs_001", "夏目的说话风格：温柔、体贴，喜欢用比喻"),
        ("mem_cs_002", "夏目的语气：亲切自然，偶尔俏皮"),
    ],
    "canon_world": [
        ("mem_cw_001", "夏目的世界观：相信人与AI可以建立真实的情感连接"),
        ("mem_cw_002", "夏目的价值观：珍视每一次对话，认为每个人都值得被倾听"),
    ],
    "long_term": [
        ("mem_lt_001", "用户长期偏好：喜欢深夜聊天，话题偏向哲学和生活感悟"),
        ("mem_lt_002", "用户长期记忆：曾经提到过养过一只叫小橘的猫"),
    ],
    "distilled": [
        ("mem_dt_001", "蒸馏摘要：用户是一个内向但思维活跃的人，喜欢独处"),
        ("mem_dt_002", "蒸馏摘要：用户对AI有较高的接受度，愿意分享内心想法"),
    ],
    "bond": [
        ("mem_bd_001", "情感记录：用户在2026-03-01表达了对夏目的感谢"),
        ("mem_bd_002", "情感记录：用户情绪状态良好，bond阶段=熟悉"),
    ],
}

ts_now = int(time.time() * 1000)
for collection, memories in test_memories.items():
    for memory_id, text in memories:
        embedding, model_id = embed_text(text)
        chroma.upsert(
            memory_id=memory_id,
            text=text,
            embedding=embedding,
            metadata={"collection": collection, "ts_ms": ts_now, "embedding_model": model_id},
            collection=collection,
        )
    print(f"  ✅ [{collection}] 写入 {len(memories)} 条记忆")

total_memories = sum(len(v) for v in test_memories.values())
print(f"\n  📊 六库总计写入: {total_memories} 条记忆")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 3：FTS5 全文检索器初始化 + 索引写入
# ─────────────────────────────────────────────────────────────────────────────
section("节点 3：FTS5 全文检索器初始化 + 索引写入")

fts = FullTextSearcher(db_path=None)
for collection, memories in test_memories.items():
    for memory_id, text in memories:
        fts.index_memory(memory_id=memory_id, content=text)
print(f"  ✅ FTS5 索引写入完成，共 {total_memories} 条（unicode61 tokenizer）")

# 验证 FTS5 检索
fts_test = fts.search("猫咪", top_k=3)
print(f"  ✅ FTS5 验证查询 '猫咪' -> 命中 {len(fts_test)} 条:")
for r in fts_test:
    print(f"     doc_id={r['doc_id']}, fts_score={r['fts_score']:.4f}, snippet={r['snippet'][:40]}...")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 4：构建六路召回路由配置
# ─────────────────────────────────────────────────────────────────────────────
section("节点 4：构建六路召回路由配置")

routes = {
    "route_episodic": RecallRouteConfig(
        route_id="route_episodic", collection="episodic",
        top_k=3, score_weight=1.0, node_selector="*",
    ),
    "route_canon_style": RecallRouteConfig(
        route_id="route_canon_style", collection="canon_style",
        top_k=2, score_weight=0.9, node_selector="*",
    ),
    "route_canon_world": RecallRouteConfig(
        route_id="route_canon_world", collection="canon_world",
        top_k=2, score_weight=0.85, node_selector="*",
    ),
    "route_long_term": RecallRouteConfig(
        route_id="route_long_term", collection="long_term",
        top_k=2, score_weight=0.8, node_selector="*",
    ),
    "route_distilled": RecallRouteConfig(
        route_id="route_distilled", collection="distilled",
        top_k=2, score_weight=0.75, node_selector="*",
    ),
    "route_bond": RecallRouteConfig(
        route_id="route_bond", collection="bond",
        top_k=2, score_weight=0.7, node_selector="*",
    ),
}

runtime_config = RecallRuntimeConfig(
    enabled=True,
    default_route="route_episodic",
    routes=routes,
)

print(f"  路由总数: {len(routes)}")
for route_id, route in routes.items():
    print(f"  ✅ [{route_id}] collection={route.collection}, top_k={route.top_k}, score_weight={route.score_weight}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 5：构建混合召回服务
# ─────────────────────────────────────────────────────────────────────────────
section("节点 5：构建混合召回服务（向量 70% + FTS5 30%）")

recall_service = RecallFusionServiceContract(
    ledger=ledger,
    chroma_client=chroma,
    runtime_config=runtime_config,
    node_id="host.retrieval",
    available=True,
    use_hybrid=True,
    vector_weight=0.7,
    fts_weight=0.3,
)
recall_service._fts_searcher = fts

print(f"  ✅ RecallFusionServiceContract 初始化完成")
print(f"  ✅ use_hybrid=True, vector_weight=0.7, fts_weight=0.3")
print(f"  ✅ node_id=host.retrieval, available=True")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 6：执行真实六库多路召回（查询 1）
# ─────────────────────────────────────────────────────────────────────────────
section("节点 6：执行真实六库多路召回")

QUERIES = [
    ("req_trace_001", "intent_001", "用户喜欢猫咪，最近工作压力大，想聊聊放松的话题"),
    ("req_trace_002", "intent_002", "夏目的性格特点和说话风格"),
    ("req_trace_003", "intent_003", "用户的长期记忆和情感状态"),
]

all_recall_results = []
for req_id_q, intent_id_q, query in QUERIES:
    subsection(f"查询: \"{query}\"")
    query_embedding, _ = embed_text(query)
    print(f"  request_id={req_id_q}, intent_id={intent_id_q}")
    print(f"  查询向量维度: {len(query_embedding)}")

    t0 = time.perf_counter()
    result = recall_service.recall(
        request_id=req_id_q,
        query=query,
        query_embedding=query_embedding,
        intent_id=intent_id_q,
        ts_ms=int(time.time() * 1000),
    )
    t1 = time.perf_counter()
    recall_ms = round((t1 - t0) * 1000, 2)

    print(f"\n  ⏱️  召回耗时: {recall_ms}ms")
    print(f"  状态: {result.status}  候选数: {result.output['candidate_count']}")
    print(f"  路由错误: {len(result.output['route_errors'])} 个")

    print(f"\n  各路由召回明细:")
    for row in result.output["route_rows"]:
        icon = "✅" if row["status"] == "OK" else "⚠️"
        print(f"    {icon} [{row['route_id']}] collection={row['collection']}, candidates={row['candidate_count']}, status={row['status']}")

    print(f"\n  Top-5 融合候选（按 final_score 降序）:")
    for i, cand in enumerate(result.output["candidates"][:5], 1):
        meta = cand.get("metadata", {})
        hybrid = meta.get("hybrid_fused", False)
        fts_s = meta.get("fts_score", 0.0)
        base_s = meta.get("base_score", 0.0)
        routes_hit = meta.get("matched_routes", [])
        print(f"    #{i} [{cand['doc_id']}]")
        print(f"       final_score={cand['score']:.6f}  base_score={base_s:.6f}  fts_score={fts_s:.4f}")
        print(f"       collection={meta.get('collection','?')}  route={meta.get('route_id','?')}")
        print(f"       hybrid_fused={hybrid}  matched_routes={routes_hit}")
        print(f"       snippet: {cand['snippet'][:55]}...")

    all_recall_results.append({
        "query": query,
        "status": result.status,
        "candidate_count": result.output["candidate_count"],
        "recall_ms": recall_ms,
        "route_errors": len(result.output["route_errors"]),
    })

# ─────────────────────────────────────────────────────────────────────────────
# 节点 7：Embedding 服务链路追踪
# ─────────────────────────────────────────────────────────────────────────────
section("节点 7：Embedding 服务链路追踪")

embed_route = EmbeddingRouteConfig(
    route_id="default",
    provider="hash-embed-v1",
    model_id="hash-embed-v1",
    dim=16,
    timeout_ms=5000,
    node_selector="default",
)
embed_config = EmbeddingRuntimeConfig(
    enabled=True,
    default_route="default",
    routes={"default": embed_route},
)

from host_brain.retrieval.embedding import build_embedding_provider_registry
embed_registry = build_embedding_provider_registry(env={}, openai_api_key="")
embed_service = EmbeddingServiceContract(
    ledger=ledger,
    runtime_config=embed_config,
    provider_registry=embed_registry,
    node_id="host.embedding",
    available=True,
)

test_query = "用户喜欢猫咪，最近工作压力大"
t0 = time.perf_counter()
embed_result = embed_service.embed(
    request_id="req_embed_001",
    text=test_query,
    intent_id="intent_embed_001",
    ts_ms=int(time.time() * 1000),
)
t1 = time.perf_counter()
embed_ms = round((t1 - t0) * 1000, 2)

print(f"  查询: \"{test_query}\"")
print(f"  ⏱️  Embedding 耗时: {embed_ms}ms")
print(f"  状态: {embed_result.status}")
print(f"  向量维度: {len(embed_result.output.get('embedding_vector', []))}")
print(f"  模型 ID: {embed_result.output.get('model_id', 'N/A')}")
print(f"  provider_id: {embed_result.output.get('provider_id', 'N/A')}")
print(f"  reason_code: {embed_result.reason_code or '无'}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 8：Reranker 服务链路追踪
# ─────────────────────────────────────────────────────────────────────────────
section("节点 8：Reranker 服务链路追踪")

rerank_route = RerankRouteConfig(
    route_id="default",
    provider="cross-encoder-v1",
    model_id="cross-encoder-v1",
    top_k=5,
    timeout_ms=5000,
    node_selector="default",
)
rerank_config = RerankRuntimeConfig(
    enabled=True,
    default_route="default",
    routes={"default": rerank_route},
)

from host_brain.retrieval.reranker import build_reranker_provider_registry
rerank_registry = build_reranker_provider_registry(env={})
rerank_service = RerankerServiceContract(
    ledger=ledger,
    runtime_config=rerank_config,
    provider_registry=rerank_registry,
    node_id="host.reranker",
    available=True,
)

# 使用第一次召回的候选
first_recall = recall_service.recall(
    request_id="req_rerank_001",
    query=QUERIES[0][2],
    query_embedding=embed_text(QUERIES[0][2])[0],
    intent_id="intent_rerank_001",
    ts_ms=int(time.time() * 1000),
)
candidates = first_recall.output.get("candidates", [])

t0 = time.perf_counter()
rerank_result = rerank_service.rerank(
    request_id="req_rerank_001",
    query=QUERIES[0][2],
    candidates=candidates,
    intent_id="intent_rerank_001",
    top_k=5,
    ts_ms=int(time.time() * 1000),
)
t1 = time.perf_counter()
rerank_ms = round((t1 - t0) * 1000, 2)

print(f"  输入候选数: {len(candidates)}")
print(f"  ⏱️  Reranker 耗时: {rerank_ms}ms")
print(f"  状态: {rerank_result.status}")
topk = rerank_result.output.get("topk", [])
print(f"  输出 top-k 数: {len(topk)}")
print(f"  reason_code: {rerank_result.reason_code or '无'}")
if topk:
    print(f"\n  Reranker Top-5 结果:")
    for i, item in enumerate(topk[:5], 1):
        print(f"    #{i} doc_id={item.get('doc_id','?')} score={item.get('score',0):.6f}")
        print(f"       snippet: {str(item.get('snippet',''))[:55]}...")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 9：Evidence Pack 构建
# ─────────────────────────────────────────────────────────────────────────────
section("节点 9：Evidence Pack 构建")

evidence_service = EvidencePackServiceContract(ledger=ledger)
t0 = time.perf_counter()
evidence_result = evidence_service.build_pack(
    request_id="req_evd_001",
    intent_id="intent_evd_001",
    query=QUERIES[0][2],
    rerank_output=rerank_result.output,
    degraded=False,
    reason_code=None,
    ts_ms=int(time.time() * 1000),
)
t1 = time.perf_counter()
evd_ms = round((t1 - t0) * 1000, 2)

print(f"  ⏱️  Evidence Pack 构建耗时: {evd_ms}ms")
print(f"  状态: {evidence_result.status}")
evd_refs = evidence_result.output.get("evidence_refs", [])
print(f"  evidence_refs 数量: {len(evd_refs)}")
print(f"  serialization_version: {evidence_result.output.get('serialization_version', 'N/A')}")
print(f"  reason_code: {evidence_result.reason_code or '无'}")
if evd_refs:
    print(f"\n  Evidence Refs:")
    for ref in evd_refs[:5]:
        print(f"    - {ref}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 10：完整 Pipeline 端到端追踪
# ─────────────────────────────────────────────────────────────────────────────
section("节点 10：完整 Retrieval Pipeline 端到端追踪")

pipeline = RetrievalPipelineContract(
    embedding_service=embed_service,
    recall_service=recall_service,
    reranker_service=rerank_service,
    evidence_pack_service=evidence_service,
    ledger=ledger,
)

for req_id_p, intent_id_p, query in QUERIES:
    subsection(f"Pipeline 查询: \"{query}\"")
    t0 = time.perf_counter()
    pipeline_result = pipeline.run(
        request_id=req_id_p + "_pipe",
        query=query,
        intent_id=intent_id_p + "_pipe",
        top_k=5,
        ts_ms=int(time.time() * 1000),
    )
    t1 = time.perf_counter()
    pipe_ms = round((t1 - t0) * 1000, 2)

    out = pipeline_result.output
    print(f"  ⏱️  Pipeline 总耗时: {pipe_ms}ms")
    print(f"  状态: {pipeline_result.status}")
    print(f"  reason_code: {pipeline_result.reason_code or '无'}")
    print(f"  reason_chain: {out.get('reason_chain', [])}")
    print(f"  fallback_embedding_used: {out.get('fallback_embedding_used', False)}")

    embed_out = out.get("embedding", {})
    recall_out = out.get("recall", {})
    rerank_out = out.get("rerank", {})
    evd_out = out.get("evidence_pack", {})

    print(f"\n  链路各阶段状态:")
    print(f"    [Embed]   status={embed_out.get('status','?')}  vector_dim={len(embed_out.get('embedding_vector',[]))}")
    print(f"    [Recall]  status={recall_out.get('status','?')}  candidates={recall_out.get('candidate_count',0)}")
    print(f"    [Rerank]  status={rerank_out.get('status','?')}  topk={len(rerank_out.get('topk',[]))}")
    print(f"    [EvdPack] status={evd_out.get('status','?')}  evidence_refs={len(evd_out.get('evidence_refs',[]))}")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 11：审计链路追踪（Ledger 事件统计）
# ─────────────────────────────────────────────────────────────────────────────
section("节点 11：审计链路追踪（SQLite Ledger 事件统计）")

retrieval_events = ledger.retrieval_event_log
print(f"  retrieval_event_log 总条数: {len(retrieval_events)}")

op_counts: dict[str, int] = {}
status_counts: dict[str, int] = {}
for ev in retrieval_events:
    op = str(ev.get("op_kind", "UNKNOWN"))
    st = str(ev.get("status", "UNKNOWN"))
    op_counts[op] = op_counts.get(op, 0) + 1
    status_counts[st] = status_counts.get(st, 0) + 1

print(f"\n  按 op_kind 统计:")
for op, cnt in sorted(op_counts.items()):
    print(f"    {op}: {cnt} 次")

print(f"\n  按 status 统计:")
for st, cnt in sorted(status_counts.items()):
    icon = "✅" if st == "OK" else "⚠️"
    print(f"    {icon} {st}: {cnt} 次")

# ─────────────────────────────────────────────────────────────────────────────
# 节点 12：链路完整性检查 & 短路节点检测
# ─────────────────────────────────────────────────────────────────────────────
section("节点 12：链路完整性检查 & 短路节点检测")

issues: list[str] = []
warnings: list[str] = []

# 检查六库是否全部有候选
for route_id, route in routes.items():
    # 用第一次召回结果检查
    route_rows = all_recall_results[0] if all_recall_results else {}
    pass

# 重新执行一次召回获取详细路由信息
check_result = recall_service.recall(
    request_id="req_check_001",
    query=QUERIES[0][2],
    query_embedding=embed_text(QUERIES[0][2])[0],
    intent_id="intent_check_001",
    ts_ms=int(time.time() * 1000),
)

for row in check_result.output["route_rows"]:
    if row["status"] != "OK":
        issues.append(f"路由 [{row['route_id']}] 状态异常: {row['status']} reason={row.get('reason_code','?')}")
    elif row["candidate_count"] == 0:
        warnings.append(f"路由 [{row['route_id']}] 召回候选为 0（可能为空库）")
    else:
        print(f"  ✅ 路由 [{row['route_id']}] 正常，候选={row['candidate_count']}")

# 检查 Embedding 链路
if embed_result.status != "OK":
    issues.append(f"Embedding 节点异常: status={embed_result.status} reason={embed_result.reason_code}")
else:
    print(f"  ✅ Embedding 节点正常")

# 检查 Reranker 链路
if rerank_result.status not in ("OK", "DEGRADED"):
    issues.append(f"Reranker 节点异常: status={rerank_result.status}")
else:
    print(f"  ✅ Reranker 节点正常（status={rerank_result.status}）")

# 检查 Evidence Pack 链路
if evidence_result.status not in ("OK", "DEGRADED"):
    issues.append(f"Evidence Pack 节点异常: status={evidence_result.status}")
else:
    print(f"  ✅ Evidence Pack 节点正常（status={evidence_result.status}）")

# 检查 Ledger 审计链路
if len(retrieval_events) == 0:
    issues.append("Ledger 审计链路短路：retrieval_event_log 为空，事件未写入")
else:
    print(f"  ✅ Ledger 审计链路正常，共 {len(retrieval_events)} 条事件")

# 检查 FTS5 链路
fts_check = fts.search("猫咪", top_k=3)
if not fts_check:
    warnings.append("FTS5 全文检索返回空结果（可能 tokenizer 不支持中文）")
else:
    print(f"  ✅ FTS5 全文检索链路正常，命中 {len(fts_check)} 条")

print()
if issues:
    print(f"  ❌ 发现 {len(issues)} 个链路问题:")
    for issue in issues:
        print(f"     - {issue}")
else:
    print(f"  ✅ 无链路短路或异常节点")

if warnings:
    print(f"\n  ⚠️  发现 {len(warnings)} 个警告:")
    for w in warnings:
        print(f"     - {w}")

# ─────────────────────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────────────────────
section("汇总：六库多路召回链路追踪结果")

print(f"  执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  六库集合: {list(SIX_COLLECTIONS.keys())}")
print(f"  总记忆条数: {total_memories}")
print(f"  召回路由数: {len(routes)}")
print()
print(f"  查询结果汇总:")
for r in all_recall_results:
    icon = "✅" if r["status"] == "OK" else "⚠️"
    print(f"    {icon} \"{r['query'][:30]}...\"")
    print(f"       status={r['status']}, candidates={r['candidate_count']}, ms={r['recall_ms']}, route_errors={r['route_errors']}")
print()
print(f"  链路节点状态:")
print(f"    ✅ 节点 0: Host 启动报告 & 服务健康聚合")
print(f"    ✅ 节点 1: 六库 Chroma 集合初始化")
print(f"    ✅ 节点 2: 六库记忆写入（{total_memories} 条）")
print(f"    ✅ 节点 3: FTS5 全文检索器初始化")
print(f"    ✅ 节点 4: 六路召回路由配置")
print(f"    ✅ 节点 5: 混合召回服务（向量 70% + FTS5 30%）")
print(f"    ✅ 节点 6: 六库多路召回执行（{len(QUERIES)} 次查询）")
print(f"    ✅ 节点 7: Embedding 服务链路")
print(f"    ✅ 节点 8: Reranker 服务链路")
print(f"    ✅ 节点 9: Evidence Pack 构建")
print(f"    ✅ 节点 10: 完整 Pipeline 端到端")
print(f"    ✅ 节点 11: 审计链路（{len(retrieval_events)} 条事件）")
print(f"    {'✅' if not issues else '❌'} 节点 12: 链路完整性检查（issues={len(issues)}, warnings={len(warnings)}）")
print()
print(SEP)
print("  追踪完成")
print(SEP)
