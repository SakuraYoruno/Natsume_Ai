from __future__ import annotations

import json
from types import SimpleNamespace

from host_brain.app.observability_persist import (
    build_life_action_event_memory_text,
    build_background_event_memory_text,
    persist_life_action_event_memory,
    persist_background_event_memory,
    should_persist_background_event_memory,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator
from host_brain.memory.life_memory.store import LifeMemoryStore


class _FakeEpisodicStore:
    def __init__(self) -> None:
        self.writes: list[dict[str, object]] = []

    def write_memory(self, **kwargs):  # noqa: ANN003
        self.writes.append(dict(kwargs))
        return SimpleNamespace(success=True, reason_code=None, memory_id="mem_non_chat_001", detail="memory written")


class _FakeLifeMemoryStore:
    def __init__(self) -> None:
        self.background_writes: list[dict[str, object]] = []

    def write_background_episode(self, **kwargs):  # noqa: ANN003
        self.background_writes.append(dict(kwargs))
        return {"status": "OK", "episode_id": "ep_background_001"}


def test_scheduler_background_event_memory_is_gated_by_env(monkeypatch) -> None:
    monkeypatch.delenv("HOST_NON_CHAT_MEMORY_ENABLED", raising=False)

    assert not should_persist_background_event_memory(
        event_source="scheduler",
        task_id="daily_recommendation",
        status="ERROR",
        detail="failed",
    )


def test_codex_production_terminal_event_memory_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HOST_NON_CHAT_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("HOST_CODEX_PRODUCTION_MEMORY_ENABLED", raising=False)

    assert should_persist_background_event_memory(
        event_source="codex_production",
        task_id="agent_lightnovel_recommender",
        status="FAILED",
        detail="reason=LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS",
    )


def test_scheduler_background_event_memory_writes_salient_event(monkeypatch) -> None:
    monkeypatch.setenv("HOST_NON_CHAT_MEMORY_ENABLED", "true")
    store = _FakeEpisodicStore()
    life_store = _FakeLifeMemoryStore()

    result = persist_background_event_memory(
        event_source="scheduler",
        event_id="sched_evt_1",
        request_id="sched_evt_1",
        intent_id="intent_1",
        task_id="daily_recommendation",
        status="ERROR",
        reason_code="LLM_POOL_EXHAUSTED",
        detail="model pool unavailable",
        ts_ms=1_800_000_000_000,
        episodic_store=store,
        life_memory_store=life_store,
    )

    assert result["status"] == "OK"
    assert result["life_memory"]["episode_id"] == "ep_background_001"
    assert life_store.background_writes[0]["event_source"] == "scheduler"
    assert store.writes
    write = store.writes[0]
    assert "后台任务记忆：daily_recommendation" in str(write["text"])
    assert "scheduler://event/sched_evt_1" in write["evidence_refs"]
    assert "scheduler://task/daily_recommendation" in write["evidence_refs"]


def test_inner_world_memory_text_uses_private_note(monkeypatch) -> None:
    monkeypatch.setenv("HOST_NON_CHAT_MEMORY_ENABLED", "true")
    store = _FakeEpisodicStore()
    life_store = _FakeLifeMemoryStore()

    result = persist_background_event_memory(
        event_source="inner_world",
        event_id="u1:inner_world_monologue_tick:1",
        request_id="inner_world::1",
        intent_id="inner_world_monologue_tick",
        person_id="u1",
        task_id="inner_world_monologue_tick",
        status="OK",
        detail="我先把没说完的事放在心里。",
        ts_ms=1_800_000_000_001,
        episodic_store=store,
        life_memory_store=life_store,
        metadata={"focus": "topic", "source": "llm"},
    )

    assert result["status"] == "OK"
    assert result["life_memory"]["episode_id"] == "ep_background_001"
    assert life_store.background_writes[0]["event_source"] == "inner_world"
    write = store.writes[0]
    assert "内心流记忆：u1" in str(write["text"])
    assert "我先把没说完的事放在心里" in str(write["text"])
    assert "chat://person/u1" in write["evidence_refs"]


def test_background_event_memory_text_skips_empty_inner_note() -> None:
    assert build_background_event_memory_text(
        event_source="inner_world",
        status="OK",
        detail="",
    ) == ""


def test_life_action_event_memory_writes_general_action(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIFE_ACTION_EVENT_MEMORY_ENABLED", raising=False)
    episodic_store = _FakeEpisodicStore()
    life_store = _FakeLifeMemoryStore()

    result = persist_life_action_event_memory(
        event_source="gateway",
        action_kind="qq_reply_sent",
        action_id="reply-1",
        actor_id="natsume",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我通过 QQ 给用户发送了今日推荐结果。",
        evidence_refs=["gateway://message/reply-1"],
        episodic_store=episodic_store,
        life_memory_store=life_store,
        metadata={"selected_recommendations": [{"title": "第一本"}]},
    )

    assert result["status"] == "OK"
    assert life_store.background_writes
    life_write = life_store.background_writes[0]
    assert life_write["event_source"] == "gateway"
    assert life_write["task_id"] == "qq_reply_sent"
    assert "行动记忆：natsume 执行了 qq_reply_sent" in life_write["detail"]
    assert "gateway://message/reply-1" in life_write["metadata"]["evidence_refs"]
    assert episodic_store.writes
    assert "行动记忆" in str(episodic_store.writes[0]["text"])


def test_life_action_event_memory_explicit_none_skips_legacy_episodic(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIFE_ACTION_EVENT_MEMORY_ENABLED", raising=False)
    life_store = _FakeLifeMemoryStore()

    def _unexpected_episodic_store():
        raise AssertionError("explicit episodic_store=None should not initialize the legacy store")

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_episodic_store",
        _unexpected_episodic_store,
    )

    result = persist_life_action_event_memory(
        event_source="gateway",
        action_kind="qq_reply_sent",
        action_id="reply-life-only",
        actor_id="natsume",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我通过 QQ 发送了一条回复。",
        evidence_refs=["gateway://message/reply-life-only"],
        episodic_store=None,
        life_memory_store=life_store,
    )

    assert result["status"] == "OK"
    assert result["reason_code"] == "EPISODIC_STORE_DISABLED"
    assert result["life_memory"]["status"] == "OK"
    assert life_store.background_writes


def test_life_action_event_memory_resolves_gateway_person_id(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIFE_ACTION_EVENT_MEMORY_ENABLED", raising=False)
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "owner")
    episodic_store = _FakeEpisodicStore()
    life_store = _FakeLifeMemoryStore()

    result = persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id="job-1:lightnovel_gateway_delivery",
        actor_id="natsume",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我刚才通过 qq 给 qq_dm_owner 推荐了 3 本轻小说。",
        evidence_refs=["gateway://message/job-1/lightnovel/overview"],
        episodic_store=episodic_store,
        life_memory_store=life_store,
        metadata={"platform": "qq", "conversation_id": "qq_dm_owner"},
    )

    assert result["status"] == "OK"
    life_write = life_store.background_writes[0]
    assert life_write["person_id"] == "owner"
    assert life_write["session_id"] == "qq_dm_owner"
    assert life_write["metadata"]["resolved_person_id_source"] == "gateway_context"
    assert "chat://person/owner" in life_write["metadata"]["evidence_refs"]
    assert "chat://person/owner" in episodic_store.writes[0]["evidence_refs"]


def test_life_action_event_memory_marks_capability_change_as_self_change(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIFE_ACTION_EVENT_MEMORY_ENABLED", raising=False)
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        life_store = LifeMemoryStore(ledger=ledger)
        episodic_store = _FakeEpisodicStore()

        result = persist_life_action_event_memory(
            event_source="action",
            action_kind="capability_change",
            action_id="remove-resource-manager",
            actor_id="natsume",
            person_id="owner",
            session_id="qq_dm_owner",
            status="COMPLETED",
            action_summary="删除资源管理器能力，因为用户明确说它没有作用。",
            evidence_refs=["action://event/remove-resource-manager"],
            episodic_store=episodic_store,
            life_memory_store=life_store,
        )
        episode = ledger.life_episode[0]
        metadata = episode["metadata"]
        if not isinstance(metadata, dict):
            metadata = json.loads(metadata)

        distilled = LifeMemoryConsolidator(store=life_store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories()

        assert result["status"] == "OK"
        assert metadata["memory_class"] == "self_change"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert distilled["written_count"] == 1
        assert total == 1
        assert "自我改变" in items[0]["summary"]
        assert "删除资源管理器能力" in items[0]["summary"]
    finally:
        ledger.close()


def test_life_action_event_memory_text_requires_summary() -> None:
    assert build_life_action_event_memory_text(action_kind="qq_reply_sent", action_summary="").strip() == ""
