from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import time

import pytest

from host_brain.app import chat_retrieval_helpers as helpers
from host_brain.conversation.schema import Turn


_LIFE_SNAPSHOT_READER = SimpleNamespace(
    get_snapshot=lambda person_id: {
        "person_id": str(person_id or "default"),
        "version": 0,
        "state": {"person_id": str(person_id or "default"), "version": 0},
    }
)
_LIFE_MEMORY_STORE = SimpleNamespace()


@pytest.mark.asyncio
async def test_semantic_embedding_load_does_not_block_conversation_event_loop(
    monkeypatch,
) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 0,
                    "routes": [],
                    "candidates": [],
                }
            }

    event_loop_thread_id = threading.get_ident()
    embedding_thread_ids: list[int] = []

    def _slow_embed(_query):  # noqa: ANN001
        embedding_thread_ids.append(threading.get_ident())
        return [0.2] * 16, "slow-test-embedding"

    monkeypatch.setattr(
        helpers,
        "_build_surface_memory_recall_service",
        lambda: _RecallService(),
    )
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _slow_embed)

    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-nonblocking-embedding",
        message="下一步怎么处理这个文本质量问题",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        bond_summary={"relationship_distance": "familiar"},
        intent_draft={"route": "technical_help", "intent_id": "intent-1"},
    )

    assert evidence_items == []
    assert summary["embedding_model_id"] == "slow-test-embedding"
    assert embedding_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in embedding_thread_ids)


@pytest.mark.asyncio
async def test_optional_semantic_embedding_cold_load_does_not_delay_turn(
    monkeypatch,
) -> None:
    def _slow_embed(_query):  # noqa: ANN001
        time.sleep(0.2)
        return [0.2] * 16, "slow-test-embedding"

    monkeypatch.setenv("HOST_SURFACE_OPTIONAL_SEMANTIC_BUDGET_MS", "25")
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _slow_embed)

    started = time.perf_counter()
    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-optional-cold-embedding",
        message="今天工作有点累，想和你说说话。",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="TIRED",
        render_target_lang="zh",
        bond_summary={"relationship_distance": "familiar"},
        intent_draft={"route": "tired", "intent_id": "intent-1"},
    )

    assert time.perf_counter() - started < 0.12
    assert evidence_items == []
    assert summary["status"] == "DEGRADED"
    assert summary["reason_code"] == "SURFACE_OPTIONAL_SEMANTIC_WARMING"
    assert summary["query_plan"]["recall_policy"] == "MAY_RECALL"


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
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
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
async def test_run_retrieval_uses_collection_fastpath_without_embedding(monkeypatch) -> None:
    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("date collection recall should not build embeddings on the fast path")

    def _unexpected_service():
        raise AssertionError("date collection recall should not build the full recall fusion service")

    class _LifeMemoryStore:
        def recall_collection_packet(self, **kwargs):  # noqa: ANN003
            assert str(kwargs["query"]).startswith("昨天三本呢？")
            assert kwargs["person_id"] == "person-1"
            assert kwargs["session_id"] == "sess-1"
            return SimpleNamespace(
                candidates=[
                    {
                        "memory_id": "col-yesterday",
                        "text": (
                            "2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据："
                            "1. 昨天推荐 A：培养失败的前美少女童星｜看点：恋爱日常；"
                            "2. 昨天推荐 B：与神在河岸｜看点：奇幻身份；"
                            "3. 昨天推荐 C：异世界皇女皇帝线｜看点：皇女皇帝线"
                        ),
                        "score": 0.99,
                        "source_ref": "life_memory_collection://col-yesterday",
                        "evidence_ref": "gateway://message/yesterday/card/1",
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "memory_layer": "life_memory_collection",
                            "scope_key": "daily:2026-05-28",
                            "domain": "lightnovel_recommendation",
                            "item_count": 3,
                            "items": [
                                {"title": "昨天推荐 A：培养失败的前美少女童星", "selection_reason": "恋爱日常"},
                                {"title": "昨天推荐 B：与神在河岸", "selection_reason": "奇幻身份"},
                                {"title": "昨天推荐 C：异世界皇女皇帝线", "selection_reason": "皇女皇帝线"},
                            ],
                        },
                    }
                ]
            )

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)
    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LifeMemoryStore(),
        request_id="req-collection-fastpath",
        message="昨天三本呢？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=1_780_034_148_000,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    trace = packet["trace"]
    assert evidence_items == []
    assert summary["status"] == "OK"
    assert summary["embedding_model_id"] == ""
    assert summary["target_stores"][0] == "life_memory_collection"
    assert trace["collection_fastpath"]["status"] == "HIT"
    assert trace["recall_answer_readiness"]["status"] == "READY"
    assert trace["recall_answer_readiness"]["response_mode"] == "answer_from_collection_memory"
    assert packet["route_ids"] == ["surface_life_collection_fastpath"]
    assert "培养失败的前美少女童星" in packet["facts"][0]


@pytest.mark.asyncio
async def test_run_retrieval_dated_quality_followup_bypasses_collection_fastpath(monkeypatch) -> None:
    calls: dict[str, object] = {"embedded": False, "service": False}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            calls["service"] = True
            assert kwargs["recall_contract_id"] == "anaphoric_selection"
            assert kwargs["query_kind"] == "anaphoric_selection"
            assert kwargs["temporal_scope"] == "yesterday"
            assert kwargs["target_store_ids"][0] == "life_memory_collection"
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidates": [
                        {
                            "text": (
                                "2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据："
                                "1. 昨天推荐 A｜看点：恋爱日常；2. 昨天推荐 B｜看点：奇幻身份；3. 昨天推荐 C｜看点：新鲜设定"
                            ),
                            "store_id": "life_memory_collection",
                            "score": 0.99,
                            "source_ref": "life_memory_collection://col-yesterday",
                            "evidence_ref": "gateway://message/yesterday/card/1",
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "domain": "lightnovel_recommendation",
                                "item_count": 3,
                                "items": [
                                    {"title": "昨天推荐 A", "selection_reason": "恋爱日常"},
                                    {"title": "昨天推荐 B", "selection_reason": "奇幻身份"},
                                    {"title": "昨天推荐 C", "selection_reason": "新鲜设定"},
                                ],
                            },
                        }
                    ],
                }
            }

    async def _identity_review(**kwargs):  # noqa: ANN003
        return kwargs["packet"]

    def _embed(query):  # noqa: ANN001
        calls["embedded"] = True
        assert "昨天那几本好看吗" in query
        return [0.2] * 16, "test-embedding"

    class _UnexpectedLifeMemoryStore:
        def recall_collection_packet(self, **_kwargs):  # noqa: ANN003
            raise AssertionError("quality followups must not use collection fastpath")

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_maybe_apply_lightnovel_deep_review", _identity_review)
    monkeypatch.setattr(helpers, "_maybe_apply_generic_evidence_review", _identity_review)
    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_UnexpectedLifeMemoryStore(),
        request_id="req-dated-quality-selection",
        message="昨天那几本好看吗",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=1_780_035_340_000,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "daily_chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert evidence_items == []
    assert calls["embedded"] is True
    assert calls["service"] is True
    assert summary["embedding_model_id"] == "test-embedding"
    assert summary["query_plan"]["reason"] == "anaphoric_selection_recall"
    assert summary["query_plan"]["query_kind"] == "anaphoric_selection"
    assert packet["trace"]["plan"]["recall_contract_id"] == "anaphoric_selection"
    assert "collection_fastpath" not in packet["trace"]


@pytest.mark.asyncio
async def test_run_retrieval_retries_recent_collection_when_first_recall_is_undercovered(monkeypatch) -> None:
    service_calls: list[dict[str, object]] = []
    items = [
        {
            "title": f"最近推荐 {index}",
            "selection_reason": "恋爱日常" if index % 2 else "奇幻身份钩子",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/recent-{index}",
        }
        for index in range(1, 13)
    ]

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            service_calls.append(dict(kwargs))
            if len(service_calls) == 1:
                return {
                    "output": {
                        "status": "OK",
                        "stage": "recall_fusion",
                        "candidate_count": 1,
                        "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                        "candidates": [
                            {
                                "text": "用户之前聊过轻小说推荐格式，但这里没有推荐集合条目。",
                                "store_id": "life_episode",
                                "score": 0.90,
                                "source_ref": "chat://request/noisy",
                                "evidence_ref": "chat://request/noisy#assistant",
                                "metadata": {"store_id": "life_episode"},
                            }
                        ],
                    }
                }
            assert kwargs["time_lookback_days"] >= 14
            assert kwargs["target_store_ids"][0] == "life_memory_collection"
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidates": [
                        {
                            "text": "最近轻小说推荐集合，记录 12 个条目；可比较证据：" + "；".join(
                                f"{index}. 最近推荐 {index}｜看点：{item['selection_reason']}｜详情：{item['detail_url']}"
                                for index, item in enumerate(items, start=1)
                            ),
                            "store_id": "life_memory_collection",
                            "score": 0.99,
                            "source_ref": "life_memory_collection://col-recent-12",
                            "evidence_ref": "gateway://message/recent/card/1",
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "rolling:7d",
                                "domain": "lightnovel_recommendation",
                                "item_count": len(items),
                                "items": items,
                            },
                        }
                    ],
                }
            }

    async def _identity_review(**kwargs):  # noqa: ANN003
        return kwargs["packet"]

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_maybe_apply_lightnovel_deep_review", _identity_review)
    monkeypatch.setattr(helpers, "_maybe_apply_generic_evidence_review", _identity_review)

    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-retry-recent-undercovered",
        message="最近十几本里哪本好看？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=1_780_035_340_000,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "daily_chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    retry = packet["trace"]["recall_retry"]
    readiness = packet["trace"]["recall_answer_readiness"]
    assert evidence_items == []
    assert len(service_calls) == 2
    assert retry["status"] == "USED"
    assert retry["from_temporal_scope"] == "recent_days"
    assert retry["to_time_lookback_days"] >= 14
    assert packet["reason_code"] == "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
    assert readiness["status"] == "NOT_READY"
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert readiness["selected_item_count"] == 12
    selected_titles = [item["title"] for item in packet["trace"]["selected_collection_items"]]
    assert selected_titles[-1] == "最近推荐 12"


@pytest.mark.asyncio
async def test_run_retrieval_does_not_expand_explicit_yesterday_scope(monkeypatch) -> None:
    service_calls: list[dict[str, object]] = []

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            service_calls.append(dict(kwargs))
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                    "candidates": [
                        {
                            "text": "用户之前聊过轻小说推荐格式，但这里没有昨天推荐集合条目。",
                            "store_id": "life_episode",
                            "score": 0.90,
                            "source_ref": "chat://request/no-yesterday-collection",
                            "evidence_ref": "chat://request/no-yesterday-collection#assistant",
                            "metadata": {"store_id": "life_episode"},
                        }
                    ],
                }
            }

    async def _identity_review(**kwargs):  # noqa: ANN003
        return kwargs["packet"]

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_maybe_apply_lightnovel_deep_review", _identity_review)
    monkeypatch.setattr(helpers, "_maybe_apply_generic_evidence_review", _identity_review)

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-no-expand-yesterday",
        message="昨天那几本好看吗？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=1_780_035_340_000,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "daily_chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert len(service_calls) == 1
    assert "recall_retry" not in packet["trace"]
    assert packet["reason_code"] == "SURFACE_MEMORY_RECALL_VALIDATION_FAILED"
    assert packet["trace"]["plan"]["temporal_scope"] == "yesterday"


@pytest.mark.asyncio
async def test_run_retrieval_skips_low_signal_short_turn_without_embedding(monkeypatch) -> None:
    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("low-signal short turns should not build embeddings")

    def _unexpected_service():
        raise AssertionError("low-signal short turns should not build recall services")

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)

    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
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
    history_reads: list[str] = []

    class _History:
        def fetch_recent_turns(self, *, session_id, max_turns, exclude_request_id=None):  # noqa: ANN001
            history_reads.append(str(session_id))
            return []

    def _unexpected_embed(_query):  # noqa: ANN001
        raise AssertionError("generic daily turns should not build embeddings")

    def _unexpected_service():
        raise AssertionError("generic daily turns should not build recall services")

    monkeypatch.setattr(helpers, "_embed_surface_memory_query", _unexpected_embed)
    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", _unexpected_service)

    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-skip-plain-answer",
        message="今天先随便聊两句吧。",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=654,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "plain_answer"},
        conversation_history=_History(),
    )

    assert evidence_items == []
    assert history_reads == []
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

    summary, evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        conversation_history=_History(),
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


def test_chat_retrieval_helpers_do_not_import_process_service_locator() -> None:
    source = Path(helpers.__file__).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "host_brain.composition.process_services"
        if isinstance(node, ast.Import):
            assert all(
                alias.name != "host_brain.composition.process_services"
                for alias in node.names
            )


@pytest.mark.asyncio
async def test_lightnovel_review_skips_preference_ledger_for_unrelated_companion_turn() -> None:
    calls: list[dict[str, object]] = []
    packet = SimpleNamespace(as_dict=lambda: {"facts": [], "trace": {}})

    result = await helpers._maybe_apply_lightnovel_deep_review(
        packet=packet,
        message="今天有点累，安静陪我一句。",
        request_id="req-companion-no-lightnovel",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        life_memory_store=_LIFE_MEMORY_STORE,
        build_user_preference_pack=lambda **kwargs: calls.append(dict(kwargs)),
    )

    assert result is packet
    assert calls == []


@pytest.mark.asyncio
async def test_lightnovel_review_reads_recent_recommendation_once_for_anaphoric_query(monkeypatch) -> None:
    from host_brain.persona.surface_memory_recall import SurfaceMemoryRecallPacket

    calls: list[dict[str, object]] = []

    def build_pack(**kwargs):  # noqa: ANN003, ANN202
        calls.append(dict(kwargs))
        return {
            "recent_recommendation_context": {
                "referenced_item": {
                    "rank": 2,
                    "title": "与神在河岸",
                    "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                    "selection_reason": "奇幻身份钩子",
                },
                "items": [],
            }
        }

    monkeypatch.setattr(
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        lambda **_kwargs: {
            "status": "OK",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_OK",
            "fact": "网页深读结果：第二本《与神在河岸》有可读详情证据。",
            "selected": {"index": 2, "title": "与神在河岸"},
            "items": [{"index": 2}],
            "evidence_refs": ["https://www.lightnovel.fun/cn/detail/2"],
            "cached": True,
            "source": "cache",
        },
    )
    packet = SurfaceMemoryRecallPacket(
        query="刚才第二本的详情",
        reason_code="SURFACE_MEMORY_RECALL_SKIPPED",
        trace={"skipped": True},
    )

    result = await helpers._maybe_apply_lightnovel_deep_review(
        packet=packet,
        message="刚才推荐的第二本详情",
        request_id="req-lightnovel-anaphoric",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        life_memory_store=_LIFE_MEMORY_STORE,
        build_user_preference_pack=build_pack,
    )

    assert len(calls) == 1
    assert result.trace["lightnovel_deep_review"]["status"] == "OK"
    assert result.facts[0].startswith("网页深读结果")


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
                            "source_ref": "action://event/lightnovel-delivery-test",
                            "evidence_ref": "gateway://message/lightnovel-delivery-test",
                            "metadata": {
                                "store_id": "life_episode",
                                "episode_kind": "action_episode",
                                "source_ref": "action://event/lightnovel-delivery-test",
                                "evidence_refs_primary": "gateway://message/lightnovel-delivery-test",
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
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
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
    assert len(packet["facts"]) == 2
    assert "可比较证据" in packet["facts"][1]
    assert "详情：" in packet["facts"][1]
    assert "https://www.lightnovel.fun/cn/detail/2" in packet["evidence_refs"]
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
    readiness = packet["trace"]["recall_answer_readiness"]
    assert packet["reason_code"] is None
    assert readiness["response_mode"] == "answer_from_evidence"
    assert readiness["evidence_depth"] == "detail_or_body_evidence"
    assert readiness["detail_evidence"]["basis"] == "lightnovel_deep_review"
    strategy = packet["trace"]["recall_answer_strategy"]
    assert strategy["action"] == "direct_answer"
    assert strategy["claim_scope"] == "detail_judgment"
    assert strategy["evidence_level"] == "detail_or_body"
    assert strategy["detail_evidence"]["basis"] == "lightnovel_deep_review"


@pytest.mark.asyncio
async def test_run_retrieval_marks_partial_lightnovel_deep_review_scope(monkeypatch) -> None:
    recommendations = [
        {
            "title": f"推荐 {index}",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{index}",
            "selection_reason": "恋爱日常",
        }
        for index in range(1, 13)
    ]

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
                            "text": "这几天通过 qq 给用户推荐了 12 本轻小说。",
                            "store_id": "life_episode",
                            "score": 0.92,
                            "source_ref": "action://event/lightnovel-delivery-recent",
                            "evidence_ref": "gateway://message/lightnovel-delivery-recent",
                            "metadata": {
                                "store_id": "life_episode",
                                "episode_kind": "action_episode",
                                "source_ref": "action://event/lightnovel-delivery-recent",
                                "evidence_refs_primary": "gateway://message/lightnovel-delivery-recent",
                                "episode_metadata": {"selected_recommendations": recommendations},
                            },
                        }
                    ],
                }
            }

    def fake_review(**_kwargs):  # noqa: ANN003
        return {
            "status": "OK",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_OK",
            "fact": "网页深读结果（含正文）：当前更推荐第3本《推荐 3》。原因：开篇比较抓人；设定线很直给。",
            "selected": {"index": 3, "title": "推荐 3"},
            "items": [{"index": index} for index in range(1, 7)],
            "evidence_refs": [f"https://www.lightnovel.fun/cn/detail/{index}" for index in range(1, 7)],
            "source_card_count": 12,
            "reviewed_item_count": 6,
            "partial_review": True,
            "cached": False,
            "source": "fetch",
            "recommendation_signature": "sig-partial",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-lightnovel-partial-deep-review",
        message="这几天十几本里哪本最好看？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    deep = packet["trace"]["lightnovel_deep_review"]
    strategy = packet["trace"]["recall_answer_strategy"]
    readiness = packet["trace"]["recall_answer_readiness"]
    assert deep["source_card_count"] == 12
    assert deep["reviewed_item_count"] == 6
    assert deep["partial_review"] is True
    assert readiness["review_scope"] == {
        "partial": True,
        "source_card_count": 12,
        "reviewed_item_count": 6,
    }
    assert strategy["claim_scope"] == "partial_detail_judgment"
    assert strategy["evidence_level"] == "partial_detail_or_body"
    assert strategy["confidence_band"] == "medium"
    assert strategy["detail_evidence"]["partial_review"] is True
    assert "partial_detail_review" in strategy["reasons"]


@pytest.mark.asyncio
async def test_run_retrieval_uses_readiness_to_recover_missing_detail_with_deep_review(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidates": [
                        {
                            "doc_id": "col-current-no-detail",
                            "source_ref": "life_memory_collection://current/no-detail",
                            "evidence_refs": ["gateway://message/current/card/1"],
                            "text": "本次轻小说推荐集合，记录 3 个条目；可比较证据：1. A｜2. B｜3. C",
                            "score": 0.95,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "delivery:ep-current",
                                "domain": "lightnovel_recommendation",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/current/card/1",
                                "items": [
                                    {"item_title": "A", "item_summary": "看点：恋爱日常", "position": 1},
                                    {"item_title": "B", "item_summary": "看点：奇幻身份", "position": 2},
                                    {"item_title": "C", "item_summary": "看点：日常喜剧", "position": 3},
                                ],
                            },
                        }
                    ],
                }
            }

    def fake_review(**kwargs):  # noqa: ANN003
        packet = kwargs["packet"]
        assert packet["facts"] == []
        assert packet["trace"]["recall_answer_readiness"]["response_mode"] == "fetch_or_request_detail_evidence"
        assert packet["trace"]["selected_collection_items"][0]["title"] == "A"
        return {
            "status": "OK",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_OK",
            "fact": "网页深读结果（含正文）：当前更推荐第2本《B》。原因：开篇冲突更直接，角色关系也更有钩子。",
            "selected": {"index": 2, "title": "B"},
            "items": [{"index": 1}, {"index": 2}, {"index": 3}],
            "evidence_refs": ["https://www.lightnovel.fun/cn/detail/b"],
            "cached": False,
            "source": "fetch",
            "recommendation_signature": "sig-recovered",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-lightnovel-readiness-recovery",
        message="这三本书哪本好看？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    readiness = packet["trace"]["recall_answer_readiness"]
    assert summary["status"] == "OK"
    assert summary["reason_code"] == "SURFACE_MEMORY_RECALL_OK"
    assert packet["degraded"] is False
    assert packet["reason_code"] is None
    assert packet["confidence"] == 0.85
    assert packet["facts"][0].startswith("网页深读结果")
    assert packet["trace"]["coverage_failed_resolved_by_deep_review"] is True
    assert packet["trace"]["answer_ready"] is True
    assert packet["trace"]["recall_answer_readiness_before_deep_review"]["response_mode"] == "fetch_or_request_detail_evidence"
    assert readiness["status"] == "READY"
    assert readiness["ready_to_answer"] is True
    assert readiness["response_mode"] == "answer_from_evidence"
    assert readiness["detail_evidence"]["basis"] == "lightnovel_deep_review"


@pytest.mark.asyncio
async def test_run_retrieval_marks_failed_lightnovel_deep_review_not_ready(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidates": [
                        {
                            "doc_id": "col-yesterday",
                            "source_ref": "life_memory_collection://yesterday",
                            "evidence_refs": ["gateway://message/yesterday/card/1"],
                            "text": "昨天轻小说推荐集合，记录 3 个条目；可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/a；2. 与神在河岸｜详情：https://www.lightnovel.fun/cn/detail/b；3. C｜详情：https://www.lightnovel.fun/cn/detail/c",
                            "score": 0.95,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "daily:2026-05-29",
                                "domain": "lightnovel_recommendation",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/yesterday/card/1",
                                "items": [
                                    {"item_title": "A", "item_summary": "详情：https://www.lightnovel.fun/cn/detail/a", "position": 1},
                                    {"item_title": "与神在河岸", "item_summary": "详情：https://www.lightnovel.fun/cn/detail/b", "position": 2},
                                    {"item_title": "C", "item_summary": "详情：https://www.lightnovel.fun/cn/detail/c", "position": 3},
                                ],
                            },
                        }
                    ],
                }
            }

    def fake_review(**_kwargs):  # noqa: ANN003
        return {
            "status": "DEGRADED",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
            "fact": "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
            "selected": {},
            "items": [{"index": 1}, {"index": 2}, {"index": 3}],
            "evidence_refs": [
                "https://www.lightnovel.fun/cn/detail/a",
                "https://www.lightnovel.fun/cn/detail/b",
                "https://www.lightnovel.fun/cn/detail/c",
            ],
            "cached": False,
            "source": "fetch",
            "recommendation_signature": "sig-fetch-failed",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-lightnovel-fetch-failed-not-ready",
        message="昨天推荐 B：与神在河岸 这本好看吗？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    readiness = packet["trace"]["recall_answer_readiness"]
    strategy = packet["trace"]["recall_answer_strategy"]
    assert packet["degraded"] is True
    assert packet["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED"
    assert packet["facts"][0].startswith("我回去查了详情页")
    assert any("与神在河岸" in fact for fact in packet["facts"])
    assert any("可比较证据" in fact for fact in packet["facts"])
    assert readiness["status"] == "NOT_READY"
    assert readiness["ready_to_answer"] is False
    assert readiness["response_mode"] == "fetch_or_request_detail_evidence"
    assert readiness["detail_evidence"]["basis"] == "missing_detail_evidence"
    assert "deep_review_failed" in readiness["reasons"]
    assert strategy["action"] == "fetch_more_evidence"
    assert strategy["ready_to_answer"] is False


@pytest.mark.asyncio
async def test_run_retrieval_applies_lightnovel_deep_review_from_entity_trace(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_entity", "store_id": "life_memory_entity"}],
                    "candidates": [
                        {
                            "memory_id": "ent-1",
                            "text": (
                                "实体记忆：与神在河岸；出现 1 次；最近证据："
                                "2026-05-28｜delivery:ep-1｜与神在河岸｜看点：奇幻身份钩子｜来源：https://www.lightnovel.fun/cn/detail/2"
                            ),
                            "score": 0.96,
                            "metadata": {
                                "store_id": "life_memory_entity",
                                "entity_id": "ent-1",
                                "canonical_name": "与神在河岸",
                                "domain": "lightnovel_recommendation",
                                "mention_count": 1,
                                "source_ref": "life_memory_entity://ent-1",
                                "evidence_refs_primary": "https://www.lightnovel.fun/cn/detail/2",
                                "mentions": [
                                    {
                                        "collection_id": "col-delivery",
                                        "collection_item_id": "coli-2",
                                        "scope_key": "delivery:ep-1",
                                        "position": 2,
                                        "item_title": "与神在河岸",
                                        "item_summary": "看点：奇幻身份钩子｜简介：神明与河岸的奇幻闲聊｜详情：https://www.lightnovel.fun/cn/detail/2",
                                        "source_ref": "https://www.lightnovel.fun/cn/detail/2",
                                        "evidence_ref": "gateway://message/card/2",
                                        "metadata": {
                                            "selection_reason": "奇幻身份钩子",
                                            "intro_snippet": "神明与河岸的奇幻闲聊。",
                                            "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            }

    def fake_review(**kwargs):  # noqa: ANN003
        packet = kwargs["packet"]
        assert packet["trace"]["selected_entity_items"][0]["title"] == "与神在河岸"
        assert packet["trace"]["selected_entity_items"][0]["detail_url"] == "https://www.lightnovel.fun/cn/detail/2"
        return {
            "status": "OK",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_OK",
            "fact": "网页深读结果：这本《与神在河岸》能看，原因是奇幻身份钩子清楚，正文开篇也能读出故事。",
            "selected": {"index": 1, "title": "与神在河岸"},
            "items": [{"index": 1}],
            "evidence_refs": ["https://www.lightnovel.fun/cn/detail/2"],
            "cached": False,
            "source": "fetch",
            "recommendation_signature": "sig-entity",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.agent_runtime.lightnovel_deep_reviewer.review_lightnovel_selection_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-lightnovel-entity-deep-review",
        message="与神在河岸这本好看吗？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    assert packet["facts"][0].startswith("网页深读结果")
    assert packet["trace"]["lightnovel_deep_review"]["status"] == "OK"
    assert packet["trace"]["lightnovel_deep_review"]["source"] == "fetch"
    assert packet["trace"]["selected_entity_items"][0]["detail_url"] == "https://www.lightnovel.fun/cn/detail/2"


@pytest.mark.asyncio
async def test_run_retrieval_uses_generic_evidence_review_for_missing_web_body(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "query": "昨天那个网页内容靠谱吗？依据是什么？",
                    "status": "OK",
                    "stage": "recall_fusion",
                    "candidate_count": 1,
                    "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                    "candidates": [
                        {
                            "doc_id": "ep-web-summary-only",
                            "source_ref": "https://example.com/report",
                            "text": "昨天查过一个网页，结论是看起来还行。",
                            "score": 0.94,
                            "metadata": {
                                "store_id": "life_episode",
                                "source_ref": "https://example.com/report",
                                "domain": "web",
                            },
                        }
                    ],
                }
            }

    def fake_review(**kwargs):  # noqa: ANN003
        packet = kwargs["packet"]
        assert packet["facts"] == []
        assert packet["rows"][0]["source_ref"] == "https://example.com/report"
        assert packet["trace"]["recall_answer_readiness"]["response_mode"] == "fetch_or_request_detail_evidence"
        return {
            "status": "OK",
            "reason_code": "GENERIC_EVIDENCE_REVIEW_OK",
            "fact": "证据补读结果：重新读取了 1 个来源（样本报告）。可用依据：报告正文列出了样本来源、更新时间和限制条件。",
            "items": [{"kind": "url", "ref": "https://example.com/report", "review_status": "OK"}],
            "evidence_refs": ["https://example.com/report"],
            "cached": False,
            "source": "fetch",
            "persist_status": "OK",
            "persisted_episode_id": "ep-generic-review",
        }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
    monkeypatch.setattr(
        "host_brain.agent_runtime.generic_evidence_reviewer.review_generic_evidence_from_memory_packet",
        fake_review,
    )

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-generic-evidence-review",
        message="昨天那个网页内容靠谱吗？依据是什么？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=123,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    readiness = packet["trace"]["recall_answer_readiness"]
    assert summary["status"] == "OK"
    assert packet["facts"][0].startswith("证据补读结果")
    assert packet["trace"]["generic_evidence_review"]["status"] == "OK"
    assert packet["trace"]["generic_evidence_review"]["persisted_episode_id"] == "ep-generic-review"
    assert packet["trace"]["coverage_failed_resolved_by_generic_evidence_review"] is True
    assert packet["trace"]["answer_ready"] is True
    assert readiness["status"] == "READY"
    assert readiness["response_mode"] == "answer_from_evidence"
    assert readiness["detail_evidence"]["basis"] == "generic_evidence_review"
    assert packet["degraded"] is False
    assert packet["reason_code"] is None


@pytest.mark.asyncio
async def test_run_retrieval_preserves_surface_memory_validation_contract(monkeypatch) -> None:
    class _RecallService:
        def recall(self, **_kwargs):  # noqa: ANN003
            return {
                "output": {
                    "query": "昨天三本呢？",
                    "status": "OK",
                    "stage": "recall_fusion",
                    "degraded": False,
                    "candidate_count": 1,
                    "ts_ms": 1_800_000_000_000,
                    "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                    "candidates": [
                        {
                            "doc_id": "col-today",
                            "source_ref": "life_memory_collection://col-today",
                            "evidence_refs": ["gateway://message/today/card/1"],
                            "text": "2026-05-28 当天轻小说推荐集合，记录 3 个条目；可比较证据：1. Today A｜2. Today B｜3. Today C",
                            "score": 0.96,
                            "metadata": {
                                "store_id": "life_memory_collection",
                                "scope_key": "daily:2026-05-28",
                                "domain": "lightnovel_recommendation",
                                "item_count": 3,
                                "evidence_refs_primary": "gateway://message/today/card/1",
                            },
                        }
                    ],
                }
            }

    monkeypatch.setattr(helpers, "_build_surface_memory_recall_service", lambda: _RecallService())
    monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))

    summary, _evidence_items = await helpers._run_retrieval(
        life_snapshot_reader=_LIFE_SNAPSHOT_READER,
        life_memory_store=_LIFE_MEMORY_STORE,
        request_id="req-validation-contract",
        message="昨天三本呢？",
        person_id="person-1",
        session_id="sess-1",
        ts_ms=1_800_000_000_000,
        emotion_tag="NEUTRAL",
        render_target_lang="zh",
        intent_draft={"route": "chat"},
    )

    packet = summary["surface_memory_recall_packet"]
    validation = packet["trace"]["validation"]
    assert summary["status"] == "DEGRADED"
    assert summary["reason_code"] == "SURFACE_MEMORY_RECALL_VALIDATION_FAILED"
    assert summary["query_plan"]["validation_requirements"] == [
        "scope_match",
        "collection_items_present",
        "evidence_refs_present",
        "collection_item_min_count",
        "collection_item_exact_count",
    ]
    assert packet["facts"] == []
    assert validation["passed"] is False
    assert "scope_match" in validation["missing"]


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
