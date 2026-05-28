from __future__ import annotations

import pytest

from host_brain.app import chat_retrieval_helpers as helpers
from host_brain.conversation.schema import Turn


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
    assert summary["query_plan"]["recall_policy"] == "MAY_RECALL"
    assert summary["query_plan"]["reason"] == "technical_user_memory_context"
    assert summary["target_stores"] == ["life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"]
    assert captured["target_store_ids"] == ["life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"]
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
    assert summary["query_plan"]["recall_policy"] == "NO_RECALL"
    assert summary["surface_memory_recall_packet"]["trace"]["plan"]["reason"] == "low_signal_surface_turn"


@pytest.mark.asyncio
async def test_run_retrieval_skips_generic_plain_answer_without_memory_hint(monkeypatch) -> None:
    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("generic daily turns should not build embeddings")

    def _unexpected_service():
        raise AssertionError("generic daily turns should not build recall services")

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)

    summary, evidence_items = await helpers._run_retrieval(
        request_id="req-skip-plain-answer",
        message="今天先随便聊两句吧。",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=654,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "plain_answer"},
    )

    assert evidence_items == []
    assert summary["status"] == "SKIPPED"
    assert summary["query_plan"]["recall_policy"] == "NO_RECALL"
    assert summary["surface_memory_recall_packet"]["trace"]["plan"]["reason"] == "no_memory_recall_needed"


@pytest.mark.asyncio
async def test_run_retrieval_prefers_recent_explicit_memory_write(monkeypatch) -> None:
    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("recent explicit memory should not need vector embedding")

    def _unexpected_service():
        raise AssertionError("recent explicit memory should bypass vector recall")

    class _History:
        def fetch_recent_turns(self, *, session_id, max_turns, exclude_request_id=None):  # noqa: ANN001
            assert session_id == "sess-1"
            assert max_turns == 8
            assert exclude_request_id == "req-current"
            return [
                Turn(
                    turn_id="turn-1",
                    session_id="sess-1",
                    person_id="person-1",
                    request_id="req-old",
                    role="user",
                    content="记一下，我昨晚没睡好。",
                    ts_ms=1,
                    turn_index=1,
                ),
                Turn(
                    turn_id="turn-2",
                    session_id="sess-1",
                    person_id="person-1",
                    request_id="req-old",
                    role="assistant",
                    content="记住了。",
                    ts_ms=2,
                    turn_index=2,
                ),
                Turn(
                    turn_id="turn-3",
                    session_id="sess-1",
                    person_id="person-1",
                    request_id="req-write",
                    role="user",
                    content="记一下，我喜欢芒果，不喜欢太甜的奶茶。",
                    ts_ms=3,
                    turn_index=3,
                ),
                Turn(
                    turn_id="turn-4",
                    session_id="sess-1",
                    person_id="person-1",
                    request_id="req-write",
                    role="assistant",
                    content="芒果记住了。",
                    ts_ms=4,
                    turn_index=4,
                ),
            ]

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)
    monkeypatch.setattr("host_brain.app.dependencies.get_conversation_history", lambda: _History())

    summary, evidence_items = await helpers._run_retrieval(
        request_id="req-current",
        message="我刚才让你记了什么？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=789,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "recall_memory"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert evidence_items == []
    assert summary["status"] == "OK"
    assert summary["routes"] == ["recent_turn_explicit_memory"]
    assert packet["facts"] == ["记忆线索：用户刚才让你记住：我喜欢芒果，不喜欢太甜的奶茶"]
    assert packet["confidence"] == 1.0
    assert packet["trace"]["short_term_explicit_memory"] is True
    assert "昨晚" not in str(packet["facts"])


@pytest.mark.asyncio
async def test_run_retrieval_applies_lightnovel_deep_review_before_surface(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "life_episode", "store_id": "life_episode"}],
                    "candidates": [
                        {
                            "text": "我刚才通过 qq 推荐了 2 本轻小说：1. A；2. 与神在河岸。",
                            "store_id": "life_episode",
                            "score": 0.92,
                            "metadata": {
                                "store_id": "life_episode",
                                "episode_kind": "action_episode",
                                "episode_metadata": {
                                    "selected_recommendations": [
                                        {
                                            "title": "A",
                                            "detail_url": "https://www.lightnovel.fun/cn/detail/1",
                                            "selection_reason": "恋爱日常",
                                        },
                                        {
                                            "title": "与神在河岸",
                                            "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                                            "selection_reason": "奇幻身份钩子",
                                        },
                                    ]
                                },
                            },
                        }
                    ],
                }
            }

    def fake_review(**_kwargs):  # noqa: ANN003
        return {
            "status": "OK",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_OK",
            "fact": "网页深读结果：我回去打开详情页比较后，更推荐第2本《与神在河岸》。原因：简介有奇幻身份钩子；有章节线索。",
            "selected": {"index": 2, "title": "与神在河岸"},
            "items": [{"index": 1}, {"index": 2}],
            "evidence_refs": ["https://www.lightnovel.fun/cn/detail/2"],
            "cached": True,
            "source": "cache",
            "recommendation_signature": "sig-test",
            "source_episode_id": "ep-cache",
            "persist_status": "OK",
            "persisted_episode_id": "ep-review",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.codex_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        request_id="req-lightnovel-deep-review",
        message="这两本书哪本好看？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert packet["facts"][0].startswith("网页深读结果")
    assert "详情：" in packet["facts"][1]
    assert "基于深读证据" in packet["must_not_do"][0]
    trace = packet["trace"]["lightnovel_deep_review"]
    assert trace["status"] == "OK"
    assert trace["cached"] is True
    assert trace["source"] == "cache"
    assert trace["recommendation_signature"] == "sig-test"
    assert trace["source_episode_id"] == "ep-cache"
    assert trace["persist_status"] == "OK"
    assert trace["persisted_episode_id"] == "ep-review"
    assert trace["evidence_ref_count"] == 1
    assert isinstance(trace["latency_ms"], int)


def test_embed_surface_memory_query_uses_configured_collection_dim(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Config:
        collection_profiles = {"episodic": 1024}
        default_collection = "episodic"

    class _Result:
        success = True
        config = _Config()

    def _fake_embed(text: str, *, dim=None):  # noqa: ANN001
        captured["text"] = text
        captured["dim"] = dim
        return [0.0] * int(dim), "hash-embed-v1"

    monkeypatch.setattr("host_brain.memory.chroma_client.load_chroma_runtime_config", lambda: _Result())
    monkeypatch.setattr("host_brain.memory.chroma_client.embed_text", _fake_embed)

    values, model_id = helpers._embed_surface_memory_query("记得上次的事吗")

    assert len(values) == 1024
    assert captured == {"text": "记得上次的事吗", "dim": 1024}
    assert model_id == "hash-embed-v1"
