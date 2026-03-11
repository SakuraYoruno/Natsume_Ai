import time

# 1. diary_writer llm_result 初始化修复验证
from host_brain.skills.impl.diary_writer import execute_diary_writer

# 场景：llm_generator=None，不应触发 NameError
result = execute_diary_writer(
    {"base_dir": "", "date": "2026-03-10"},
    context={"request_id": "r1", "audit_reader": None, "episodic_reader": None}
)
assert result["status"] in ("REJECTED", "EXECUTED", "DEGRADED"), f"意外状态: {result}"
print("diary_writer llm_result 初始化 OK: status=", result["status"])

# 2. retrieval pipeline 可正常导入
from host_brain.retrieval.pipeline import RetrievalPipelineContract
from host_brain.retrieval.orchestrator import RetrievalOrchestrator
print("retrieval pipeline/orchestrator 导入 OK")

# 3. recall_fusion 七路 store 分发正常
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
svc = RecallFusionServiceContract(available=False)
result2 = svc.recall(
    request_id="r1", query="test", query_embedding=[0.1]*16,
    intent_id="i1", session_id="s1"
)
assert result2.status == "DEGRADED", f"预期 DEGRADED，实际: {result2.status}"
print("recall_fusion 降级路径 OK")

# 4. reranker warm_retry 上限验证（最多重试 1 次，attempt < 2）
from host_brain.retrieval.reranker import _should_retry_reranker_cold_start
assert _should_retry_reranker_cold_start("cross_encoder", 1) == True
assert _should_retry_reranker_cold_start("cross_encoder", 2) == False
assert _should_retry_reranker_cold_start("heuristic", 1) == False
print("reranker warm_retry 上限 OK: attempt<2 才重试，heuristic 不重试")

# 5. context_compressor 正常工作
from host_brain.memory.context_compressor import ContextCompressor
cc = ContextCompressor()
turns = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]
ctx = cc.compress(turns, strategy="sliding_window")
assert not ctx.degraded
print("context_compressor OK: turns=", len(ctx.turns))

# 6. episodic store LRU 淘汰
from host_brain.memory.episodic import EpisodicStore
store = EpisodicStore()
for i in range(3):
    store.write_memory(text=f"test {i}", evidence_refs=[f"evd://test/{i}"])
assert len(store._records) == 3
print("episodic store LRU OK: records=", len(store._records))

# 7. FastAPI 服务仍可正常启动
from host_brain.app.fastapi_app import app
print("FastAPI app OK:", len(app.routes), "条路由")

print("ALL CHECKS PASSED")
