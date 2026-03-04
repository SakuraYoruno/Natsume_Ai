"""T-RERANK-01~04: RerankerServiceContract + EvidencePackServiceContract 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.retrieval.reranker import (
    RerankerServiceContract,
    STATUS_OK,
    STATUS_DEGRADED,
    S_RET_RERANK,
)
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_candidates(n: int = 3) -> list[dict]:
    return [
        {
            "doc_id": f"doc_{i:03d}",
            "source_ref": f"source://doc/{i}",
            "snippet": f"snippet text for document {i} about query topic",
        }
        for i in range(1, n + 1)
    ]


def _make_reranker(*, available: bool = True) -> RerankerServiceContract:
    return RerankerServiceContract(available=available)


# ── T-RERANK-01: 正常重排返回 TopK，字段稳定 ─────────────────────────────────

class TestRerankNormal:
    """T-RERANK-01: 正常重排返回 TopK，字段结构稳定。"""

    def test_rerank_returns_topk(self):
        svc = _make_reranker()
        result = svc.rerank(
            request_id="req-001",
            query="query topic",
            candidates=_make_candidates(5),
            top_k=3,
        )
        assert result.success is True
        assert result.status == STATUS_OK
        assert result.stage == S_RET_RERANK
        topk = result.output["topk"]
        assert len(topk) == 3

    def test_topk_fields_stable(self):
        svc = _make_reranker()
        result = svc.rerank(
            request_id="req-002",
            query="query topic",
            candidates=_make_candidates(3),
        )
        for item in result.output["topk"]:
            assert "doc_id" in item
            assert "source_ref" in item
            assert "snippet" in item
            assert "score" in item
            assert "confidence" in item

    def test_rerank_deterministic(self):
        svc = _make_reranker()
        candidates = _make_candidates(4)
        r1 = svc.rerank(request_id="req-det1", query="topic", candidates=candidates)
        r2 = svc.rerank(request_id="req-det2", query="topic", candidates=candidates)
        ids1 = [item["doc_id"] for item in r1.output["topk"]]
        ids2 = [item["doc_id"] for item in r2.output["topk"]]
        assert ids1 == ids2


# ── T-RERANK-02: Reranker 不可用 → 降级，stage 标注 ──────────────────────────

class TestRerankDegrade:
    """T-RERANK-02: Reranker 不可用时降级为 recall_order，stage 标注。"""

    def test_unavailable_degrades(self):
        svc = _make_reranker(available=False)
        result = svc.rerank(
            request_id="req-deg",
            query="query topic",
            candidates=_make_candidates(3),
        )
        assert result.success is False
        assert result.status == STATUS_DEGRADED
        assert result.stage == S_RET_RERANK
        assert result.reason_code is not None

    def test_degraded_still_returns_topk(self):
        svc = _make_reranker(available=False)
        result = svc.rerank(
            request_id="req-deg2",
            query="query topic",
            candidates=_make_candidates(3),
        )
        assert len(result.output["topk"]) > 0

    def test_degraded_flag_in_output(self):
        svc = _make_reranker(available=False)
        result = svc.rerank(
            request_id="req-deg3",
            query="query topic",
            candidates=_make_candidates(2),
        )
        assert result.output["degraded"] is True


# ── T-RERANK-03: health() 返回结构稳定 ───────────────────────────────────────

class TestRerankHealth:
    """T-RERANK-03: build_reranker_dependency_status 返回结构稳定。"""

    def test_healthy_status(self):
        from host_brain.retrieval.reranker import build_reranker_dependency_status
        svc = _make_reranker()
        result = svc.rerank(
            request_id="req-health",
            query="query topic",
            candidates=_make_candidates(2),
        )
        status = build_reranker_dependency_status(result)
        assert "available" in status
        assert status["available"] is True

    def test_degraded_status(self):
        from host_brain.retrieval.reranker import build_reranker_dependency_status
        svc = _make_reranker(available=False)
        result = svc.rerank(
            request_id="req-health-deg",
            query="query topic",
            candidates=_make_candidates(2),
        )
        status = build_reranker_dependency_status(result)
        assert status["available"] is False
        assert "detail" in status


# ── T-RERANK-04: evidence_pack 含 rerank_stage 字段 ──────────────────────────

class TestEvidencePackRerankStage:
    """T-RERANK-04: EvidencePackServiceContract 构建的 pack 含 stage 字段。"""

    def test_evidence_pack_has_stage(self):
        svc = EvidencePackServiceContract()
        reranker = _make_reranker()
        rerank_result = reranker.rerank(
            request_id="req-ep",
            query="query topic",
            candidates=_make_candidates(3),
        )
        pack_result = svc.build_pack(
            request_id="req-ep",
            query="query topic",
            rerank_output=rerank_result.output,
        )
        assert "stage" in pack_result.output
        assert pack_result.output["stage"] == "S_RET_EVIDENCE_PACK"

    def test_evidence_pack_fields_stable(self):
        svc = EvidencePackServiceContract()
        reranker = _make_reranker()
        rerank_result = reranker.rerank(
            request_id="req-ep2",
            query="query topic",
            candidates=_make_candidates(2),
        )
        pack_result = svc.build_pack(
            request_id="req-ep2",
            query="query topic",
            rerank_output=rerank_result.output,
        )
        output = pack_result.output
        for field in ("request_id", "query", "topk", "pack_id", "stage", "status", "ts_ms"):
            assert field in output, f"missing field: {field}"

    def test_evidence_pack_degraded_upstream(self):
        svc = EvidencePackServiceContract()
        reranker = _make_reranker(available=False)
        rerank_result = reranker.rerank(
            request_id="req-ep3",
            query="query topic",
            candidates=_make_candidates(2),
        )
        pack_result = svc.build_pack(
            request_id="req-ep3",
            query="query topic",
            rerank_output=rerank_result.output,
        )
        assert pack_result.output["degraded"] is True
