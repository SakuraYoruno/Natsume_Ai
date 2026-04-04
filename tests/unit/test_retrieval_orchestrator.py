from __future__ import annotations

import asyncio
import time

from host_brain.retrieval.orchestrator import (
    RETRIEVAL_PATH_ALL_FAILURE,
    RETRIEVAL_PATH_PARTIAL_FAILURE,
    RETRIEVAL_PATH_SUCCESS,
    RetrievalOrchestrator,
    RetrievalOrchestratorRequest,
)
from host_brain.retrieval.pipeline import RetrievalPipelineResult


class _FakePipeline:
    def __init__(self, result: RetrievalPipelineResult) -> None:
        self._result = result

    def run(self, **kwargs):  # noqa: ANN003
        return self._result


class _CountingPipeline:
    def __init__(self, result: RetrievalPipelineResult) -> None:
        self._result = result
        self.calls = 0

    def run(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return self._result


class _SlowPipeline:
    def run(self, **kwargs):  # noqa: ANN003
        time.sleep(0.2)
        return RetrievalPipelineResult(
            success=False,
            status="DEGRADED",
            reason_code="TIMEOUT",
            stage="S_RET_PIPELINE",
            detail="slow",
            output={
                "recall": {},
                "rerank": {},
                "evidence_pack": {},
                "reason_chain": [],
                "degraded": True,
            },
        )


def _run(orchestrator: RetrievalOrchestrator, request: RetrievalOrchestratorRequest, timeout_ms: int | None = None):
    async def _arun():
        return await orchestrator.arun(request=request, timeout_ms=timeout_ms)

    return asyncio.run(_arun())


def test_retrieval_orchestrator_success_path() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "recall": {
                "routes": ["episodic", "style"],
                "route_rows": [{"route": "episodic"}],
                "route_errors": [],
                "candidate_count": 3,
            },
            "rerank": {
                "topk": [
                    {
                        "evidence_ref": "e1",
                        "source_ref": "doc://1",
                        "snippet": "hello world",
                        "score": 0.9,
                        "confidence": 0.8,
                    }
                ]
            },
            "evidence_pack": {"evidence_refs": ["e1"]},
            "reason_chain": [],
            "degraded": False,
        },
    )
    orchestrator = RetrievalOrchestrator(pipeline=_FakePipeline(result=result))  # type: ignore[arg-type]
    req = RetrievalOrchestratorRequest(
        trace_id="trace::1",
        query="Natsume retrieval pipeline",
        session_state={"session_id": "s1"},
        request_id="req::1",
        session_id="s1",
        top_k=5,
    )
    out = _run(orchestrator, req)

    assert out.success is True
    assert out.path == RETRIEVAL_PATH_SUCCESS
    assert out.retrieval_summary["cache_hit"] is False
    assert out.retrieval_bundle["trace_id"] == "trace::1"
    assert out.retrieval_bundle["query"] == "Natsume retrieval pipeline"
    assert out.retrieval_bundle["session_state"]["session_id"] == "s1"
    assert out.retrieval_summary["route_count"] == 2
    assert len(out.retrieval_summary["topk"]) == 1
    assert out.retrieval_summary["evidence_topk"] == []
    assert len(out.retrieval_evidence_items) == 1


def test_retrieval_orchestrator_partial_failure_path() -> None:
    result = RetrievalPipelineResult(
        success=False,
        status="DEGRADED",
        reason_code="RERANK_STAGE_TIMEOUT",
        stage="S_RET_PIPELINE",
        detail="degraded",
        output={
            "recall": {
                "routes": ["episodic"],
                "route_rows": [{"route": "episodic"}],
                "route_errors": [{"route": "style", "reason": "timeout"}],
                "candidate_count": 2,
            },
            "rerank": {"topk": [{"evidence_ref": "e2", "snippet": "snippet"}]},
            "evidence_pack": {"evidence_refs": ["e2"]},
            "reason_chain": ["RERANK_STAGE_TIMEOUT"],
            "degraded": True,
        },
    )
    orchestrator = RetrievalOrchestrator(pipeline=_FakePipeline(result=result))  # type: ignore[arg-type]
    out = _run(
        orchestrator,
            RetrievalOrchestratorRequest(
                trace_id="trace::2",
                query="Natsume retrieval pipeline",
                session_state={"session_id": "s2"},
                request_id="req::2",
            ),
    )

    assert out.success is False
    assert out.path == RETRIEVAL_PATH_PARTIAL_FAILURE
    assert out.retrieval_summary["cache_hit"] is False
    assert out.retrieval_summary["status"] == "DEGRADED"
    assert out.retrieval_summary["candidate_count"] == 2


def test_retrieval_orchestrator_all_failure_path() -> None:
    result = RetrievalPipelineResult(
        success=False,
        status="DEGRADED",
        reason_code="RECALL_ALL_FAILED",
        stage="S_RET_PIPELINE",
        detail="failed",
        output={
            "recall": {
                "routes": [],
                "route_rows": [],
                "route_errors": [{"route": "episodic", "reason": "down"}],
                "candidate_count": 0,
            },
            "rerank": {"topk": []},
            "evidence_pack": {"evidence_refs": []},
            "reason_chain": ["RECALL_ALL_FAILED"],
            "degraded": True,
        },
    )
    orchestrator = RetrievalOrchestrator(pipeline=_FakePipeline(result=result))  # type: ignore[arg-type]
    out = _run(
        orchestrator,
            RetrievalOrchestratorRequest(
                trace_id="trace::3",
                query="Natsume retrieval pipeline",
                session_state={"session_id": "s3"},
                request_id="req::3",
            ),
    )

    assert out.success is False
    assert out.path == RETRIEVAL_PATH_ALL_FAILURE
    assert out.retrieval_summary["cache_hit"] is False
    assert out.retrieval_summary["route_count"] == 0
    assert out.retrieval_summary["rerank_topk"] == 0


def test_retrieval_orchestrator_timeout_maps_to_degraded() -> None:
    orchestrator = RetrievalOrchestrator(pipeline=_SlowPipeline())  # type: ignore[arg-type]
    started = time.perf_counter()
    out = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::4",
            query="Natsume retrieval pipeline",
            session_state={"session_id": "s4"},
            request_id="req::4",
        ),
        timeout_ms=50,
    )
    elapsed = time.perf_counter() - started

    assert out.success is False
    assert out.path == RETRIEVAL_PATH_ALL_FAILURE
    assert out.retrieval_summary["cache_hit"] is False
    assert out.retrieval_summary["reason_code"] == "RETRIEVAL_TIMEOUT"
    assert elapsed < 0.15


def test_retrieval_orchestrator_cache_hit_reuses_pipeline() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "recall": {
                "routes": ["episodic"],
                "route_rows": [],
                "route_errors": [],
                "candidate_count": 1,
            },
            "rerank": {"topk": [{"evidence_ref": "e1", "snippet": "hello"}]},
            "evidence_pack": {"evidence_refs": ["e1"]},
            "reason_chain": [],
            "degraded": False,
        },
    )
    pipeline = _CountingPipeline(result=result)
    orchestrator = RetrievalOrchestrator(
        pipeline=pipeline,  # type: ignore[arg-type]
        cache_enabled=True,
        cache_ttl_ms=5000,
        cache_max_entries=128,
    )

    req1 = RetrievalOrchestratorRequest(
        trace_id="trace::cache-1",
        query="重复问题",
        session_state={"session_id": "s-cache"},
        request_id="req::cache-1",
        session_id="s-cache",
    )
    req2 = RetrievalOrchestratorRequest(
        trace_id="trace::cache-2",
        query="重复问题",
        session_state={"session_id": "s-cache"},
        request_id="req::cache-2",
        session_id="s-cache",
    )

    out1 = _run(orchestrator, req1)
    out2 = _run(orchestrator, req2)

    assert pipeline.calls == 1
    assert out1.retrieval_summary["cache_hit"] is False
    assert out2.retrieval_summary["cache_hit"] is True
    assert dict(out2.retrieval_bundle.get("cache") or {}).get("hit") is True


def test_retrieval_orchestrator_cache_expire_triggers_new_run() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "recall": {"routes": ["episodic"], "route_rows": [], "route_errors": [], "candidate_count": 1},
            "rerank": {"topk": [{"evidence_ref": "e1", "snippet": "hello"}]},
            "evidence_pack": {"evidence_refs": ["e1"]},
            "reason_chain": [],
            "degraded": False,
        },
    )
    pipeline = _CountingPipeline(result=result)
    orchestrator = RetrievalOrchestrator(
        pipeline=pipeline,  # type: ignore[arg-type]
        cache_enabled=True,
        cache_ttl_ms=10,
        cache_max_entries=128,
    )

    req = RetrievalOrchestratorRequest(
        trace_id="trace::cache-expire",
        query="重复问题",
        session_state={"session_id": "s-cache-expire"},
        request_id="req::cache-expire-1",
        session_id="s-cache-expire",
    )
    out1 = _run(orchestrator, req)
    time.sleep(0.03)
    out2 = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::cache-expire-2",
            query="重复问题",
            session_state={"session_id": "s-cache-expire"},
            request_id="req::cache-expire-2",
            session_id="s-cache-expire",
        ),
    )

    assert pipeline.calls == 2
    assert out1.retrieval_summary["cache_hit"] is False
    assert out2.retrieval_summary["cache_hit"] is False


def test_retrieval_orchestrator_cache_key_separates_render_target_lang() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "recall": {"routes": ["persona"], "route_rows": [], "route_errors": [], "candidate_count": 1},
            "rerank": {"topk": [{"evidence_ref": "e1", "snippet": "hello"}]},
            "evidence_pack": {"evidence_refs": ["e1"]},
            "reason_chain": [],
            "degraded": False,
        },
    )
    pipeline = _CountingPipeline(result=result)
    orchestrator = RetrievalOrchestrator(
        pipeline=pipeline,  # type: ignore[arg-type]
        cache_enabled=True,
        cache_ttl_ms=5000,
        cache_max_entries=128,
    )

    zh_out = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::lang-zh",
            query="same-query",
            session_state={"session_id": "lang-cache"},
            request_id="req::lang-zh",
            session_id="lang-cache",
            render_target_lang="zh",
        ),
    )
    ja_out = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::lang-ja",
            query="same-query",
            session_state={"session_id": "lang-cache"},
            request_id="req::lang-ja",
            session_id="lang-cache",
            render_target_lang="ja",
        ),
    )

    assert pipeline.calls == 2
    assert zh_out.retrieval_summary["cache_hit"] is False
    assert ja_out.retrieval_summary["cache_hit"] is False



def test_retrieval_orchestrator_cache_key_separates_persona_context() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "recall": {"routes": ["episodic_vector"], "route_rows": [], "route_errors": [], "candidate_count": 1},
            "rerank": {"topk": [{"evidence_ref": "e1", "snippet": "hello"}]},
            "evidence_pack": {"evidence_refs": ["e1"]},
            "reason_chain": [],
            "degraded": False,
        },
    )
    pipeline = _CountingPipeline(result=result)
    orchestrator = RetrievalOrchestrator(
        pipeline=pipeline,  # type: ignore[arg-type]
        cache_enabled=True,
        cache_ttl_ms=5000,
        cache_max_entries=128,
    )

    out1 = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::persona-1",
            query="same-query",
            session_state={"session_id": "persona-cache"},
            request_id="req::persona-1",
            session_id="persona-cache",
            mood_state="critical",
            bond_summary={"stage": "FRIEND"},
            motive_state={"dominant_motive": "self_protect", "drives": {"self_protect_drive": 0.88}},
        ),
    )
    out2 = _run(
        orchestrator,
        RetrievalOrchestratorRequest(
            trace_id="trace::persona-2",
            query="same-query",
            session_state={"session_id": "persona-cache"},
            request_id="req::persona-2",
            session_id="persona-cache",
            mood_state="engaged",
            bond_summary={"stage": "INTIMATE"},
            motive_state={"dominant_motive": "continuity", "drives": {"continuity_drive": 0.91}},
        ),
    )

    assert pipeline.calls == 2
    assert out1.retrieval_summary["cache_hit"] is False
    assert out2.retrieval_summary["cache_hit"] is False
    assert "mood_state" in out1.retrieval_summary["cache_key_scope"]
    assert "bond_summary" in out2.retrieval_summary["cache_key_scope"]
    assert "motive_state" in out2.retrieval_summary["cache_key_scope"]
