from __future__ import annotations

from types import SimpleNamespace

from host_brain.retrieval.recall_fusion_stores import search_sqlite_ledger


def test_search_sqlite_ledger_applies_person_boost_from_evidence_refs() -> None:
    ledger = SimpleNamespace(
        intent_log=[],
        memory_ops_log=[
            {
                "memory_id": "mem-001",
                "request_id": "req-001",
                "intent_id": "intent-001",
                "op_kind": "WRITE",
                "status": "SUCCESS",
                "detail": "memory written: User said she loves games and asked whether I remember it.",
                "evidence_refs": [
                    "chat://request/req-001",
                    "chat://person/user-001",
                    "chat://session/qq_session_01",
                ],
                "ts_ms": 1000,
            }
        ],
    )
    service = SimpleNamespace(ledger=ledger)

    rows = search_sqlite_ledger(
        service,
        query="remember favorite games",
        top_k=3,
        person_id="user-001",
        session_id="wechat_session_02",
        now_ts_ms=1500,
    )

    assert len(rows) == 1
    metadata = rows[0]["metadata"]
    assert metadata["person_id"] == "user-001"
    assert metadata["session_id"] == "qq_session_01"
    assert metadata["score_boost"]["person_boost"] > 0.0
    assert rows[0]["score"] >= metadata["base_score"]
