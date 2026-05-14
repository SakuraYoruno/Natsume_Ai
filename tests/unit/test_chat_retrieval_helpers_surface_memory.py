from __future__ import annotations

import pytest

from host_brain.app import chat_retrieval_helpers as helpers


@pytest.mark.asyncio
async def test_run_retrieval_returns_compressed_surface_memory_packet(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 2,
                    "routes": [
                        {"route_id": "episodic_vector", "store_id": "episodic_vector"},
                        {"route_id": "sqlite_ledger", "store_id": "sqlite_ledger"},
                    ],
                    "candidates": [
                        {
                            "text": "用户上次处理文本质量时先看输入输出边界。",
                            "store_id": "episodic_vector",
                            "score": 0.82,
                            "source_ref": "memory://raw/should-not-pass",
                            "evidence_ref": "evd://memory/1",
                        },
                        {
                            "text": "熟人私聊，不升级亲密。",
                            "store_id": "neo4j_bond",
                            "score": 0.73,
                            "source_ref": "memory://bond/should-not-pass",
                            "evidence_ref": "evd://bond/1",
                        },
                    ],
                }
            }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))

    summary, evidence_items = await helpers._run_retrieval(
        request_id="req-surface-memory",
        message="下一步怎么处理这个文本质量问题",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        bond_summary={"relationship_distance": "familiar"},
        intent_draft={"route": "technical_help", "intent_id": "intent-1"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert evidence_items == []
    assert summary["status"] == "OK"
    assert summary["query_plan"]["intent"] == "surface_memory_recall"
    assert summary["query_plan"]["reason"] == "technical_user_memory_context"
    assert summary["target_stores"] == ["episodic_vector", "sqlite_ledger"]
    assert captured["target_store_ids"] == ["episodic_vector", "sqlite_ledger"]
    assert captured["intent_id"] == "intent-1"
    assert "输入输出边界" in packet["facts"][0]
    assert "memory://raw/should-not-pass" not in str(packet["facts"])


@pytest.mark.asyncio
async def test_run_retrieval_skips_low_signal_short_turn_without_embedding(monkeypatch) -> None:
    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("low-signal short turns should not build embeddings")

    def _unexpected_service():
        raise AssertionError("low-signal short turns should not build recall services")

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)

    summary, evidence_items = await helpers._run_retrieval(
        request_id="req-skip-memory",
        message="继续",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=456,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "daily_chat"},
    )

    assert evidence_items == []
    assert summary["status"] == "SKIPPED"
    assert summary["reason_code"] == "SURFACE_MEMORY_RECALL_SKIPPED"
    assert summary["surface_memory_recall_packet"]["trace"]["plan"]["reason"] == "low_signal_surface_turn"
