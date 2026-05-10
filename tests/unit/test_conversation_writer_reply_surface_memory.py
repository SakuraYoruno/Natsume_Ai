from __future__ import annotations

from types import SimpleNamespace

from host_brain.conversation import writer


class _FakeTurnStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def append_generated(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return SimpleNamespace(turn_index=3)


class _FakeTopicLedgerStore:
    def __init__(self, topics=None, should_raise: bool = False) -> None:
        self._topics = list(topics or [])
        self._should_raise = should_raise

    def record_reply_plan(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        if self._should_raise:
            raise RuntimeError("boom")
        return list(self._topics)


class _FakeTacticLedgerStore:
    def record_reply_plan(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return None


class _CaptureMemoryStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def remember(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))


class _BrokenMemoryStore:
    def remember(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        raise RuntimeError("boom")


def test_record_assistant_turn_remembers_real_generated_reply(monkeypatch) -> None:
    capture = _CaptureMemoryStore()
    turn_store = _FakeTurnStore()
    topic_store = _FakeTopicLedgerStore(topics=["emoji_social_followup"])

    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: turn_store)
    monkeypatch.setattr(writer, "get_session_topic_ledger_store", lambda: topic_store)
    monkeypatch.setattr(writer, "get_session_tactic_ledger_store", lambda: _FakeTacticLedgerStore())
    monkeypatch.setattr(writer, "get_reply_surface_memory_store", lambda: capture)
    monkeypatch.setattr(
        writer,
        "get_compiled_soul",
        lambda: SimpleNamespace(soul_id="test-soul"),
    )

    writer.record_assistant_turn(
        request_id="req-1",
        session_id="sess-1",
        person_id="user-1",
        content="哥哥，猫耳那张我接住了。",
        ts_ms=1,
        lang="zh",
        metadata={"provider_name": "gemini", "model_id": "gemini-2.5-pro", "fallback": False},
        reply_plan={
            "primary_topic": "emoji_social_followup",
            "tactic_family": "soft_emotion_ack",
            "tactic_variant": "backoff_hold",
            "response_form": "text",
            "response_form_reason": "image_social_followup",
        },
    )

    assert len(capture.calls) == 1
    assert "猫耳" in str(capture.calls[0]["text"])
    assert turn_store.calls[0]["metadata"]["soul_id"] == "test-soul"
    assert turn_store.calls[0]["metadata"]["primary_topic"] == "emoji_social_followup"
    assert turn_store.calls[0]["metadata"]["tactic_family"] == "soft_emotion_ack"
    assert turn_store.calls[0]["metadata"]["response_form_reason"] == "image_social_followup"


def test_record_assistant_turn_remembers_text_plus_sticker_reply(monkeypatch) -> None:
    capture = _CaptureMemoryStore()

    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: _FakeTurnStore())
    monkeypatch.setattr(writer, "get_session_topic_ledger_store", lambda: _FakeTopicLedgerStore())
    monkeypatch.setattr(writer, "get_session_tactic_ledger_store", lambda: _FakeTacticLedgerStore())
    monkeypatch.setattr(writer, "get_reply_surface_memory_store", lambda: capture)
    monkeypatch.setattr(
        writer,
        "get_compiled_soul",
        lambda: SimpleNamespace(soul_id="test-soul"),
    )

    writer.record_assistant_turn(
        request_id="req-phatic",
        session_id="sess-phatic",
        person_id="user-5",
        content="哦！你好呀。",
        ts_ms=5,
        lang="zh",
        metadata={"provider_name": "gemini", "model_id": "gemini-2.5-pro", "fallback": False},
        reply_plan={
            "primary_topic": "greeting",
            "tactic_family": "phatic_wave",
            "tactic_variant": "greeting",
            "response_form": "text_plus_sticker",
            "response_form_reason": "phatic_greeting",
        },
    )

    assert len(capture.calls) == 1
    assert capture.calls[0]["text"] == "哦！你好呀。"


def test_record_assistant_turn_skips_deterministic_or_fallback_reply(monkeypatch) -> None:
    capture = _CaptureMemoryStore()

    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: _FakeTurnStore())
    monkeypatch.setattr(writer, "get_session_topic_ledger_store", lambda: _FakeTopicLedgerStore())
    monkeypatch.setattr(writer, "get_session_tactic_ledger_store", lambda: _FakeTacticLedgerStore())
    monkeypatch.setattr(writer, "get_reply_surface_memory_store", lambda: capture)
    monkeypatch.setattr(
        writer,
        "get_compiled_soul",
        lambda: SimpleNamespace(soul_id="test-soul"),
    )

    writer.record_assistant_turn(
        request_id="req-2",
        session_id="sess-2",
        person_id="user-2",
        content="哥哥，怎么啦，突然发这个给我看呀？",
        ts_ms=1,
        lang="zh",
        metadata={"provider_name": "host", "model_id": "deterministic-plan-renderer", "fallback": False},
        reply_plan={
            "primary_topic": "emoji_social_reply",
            "tactic_family": "soft_emotion_ack",
            "tactic_variant": "image_social",
            "response_form": "text",
            "response_form_reason": "image_social_reply",
        },
    )
    writer.record_assistant_turn(
        request_id="req-3",
        session_id="sess-2",
        person_id="user-2",
        content="哥哥，好嘛，我收一点。",
        ts_ms=2,
        lang="zh",
        metadata={"provider_name": "gemini", "model_id": "gemini-2.5-pro", "fallback": True},
        reply_plan={
            "primary_topic": "rejection",
            "tactic_family": "hold_repair_space",
            "tactic_variant": "plain",
            "response_form": "text",
            "response_form_reason": "default_text",
        },
    )

    assert capture.calls == []


def test_record_user_turn_appends_soul_metadata(monkeypatch) -> None:
    turn_store = _FakeTurnStore()

    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: turn_store)
    monkeypatch.setattr(
        writer,
        "get_compiled_soul",
        lambda: SimpleNamespace(soul_id="test-soul"),
    )

    writer.record_user_turn(
        request_id="req-user",
        session_id="sess-user",
        person_id="user-3",
        content="hello",
        ts_ms=3,
        lang="zh",
        metadata={"channel": "qq"},
    )

    assert len(turn_store.calls) == 1
    assert turn_store.calls[0]["metadata"]["soul_id"] == "test-soul"
    assert turn_store.calls[0]["metadata"]["channel"] == "qq"


def test_record_turns_return_early_for_empty_session_or_content(monkeypatch) -> None:
    turn_store = _FakeTurnStore()
    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: turn_store)

    writer.record_user_turn(
        request_id="req-empty",
        session_id="",
        person_id="user",
        content="hello",
        ts_ms=1,
        lang="zh",
    )
    writer.record_assistant_turn(
        request_id="req-empty-2",
        session_id="sess",
        person_id="user",
        content="",
        ts_ms=1,
        lang="zh",
    )

    assert turn_store.calls == []


def test_record_assistant_turn_soft_fails_topic_ledger_and_memory(monkeypatch) -> None:
    turn_store = _FakeTurnStore()

    monkeypatch.setattr(writer, "get_conversation_turn_store", lambda: turn_store)
    monkeypatch.setattr(writer, "get_session_topic_ledger_store", lambda: _FakeTopicLedgerStore(should_raise=True))
    monkeypatch.setattr(writer, "get_session_tactic_ledger_store", lambda: _FakeTacticLedgerStore())
    monkeypatch.setattr(writer, "get_reply_surface_memory_store", lambda: _BrokenMemoryStore())
    monkeypatch.setattr(
        writer,
        "get_compiled_soul",
        lambda: SimpleNamespace(soul_id="test-soul"),
    )

    writer.record_assistant_turn(
        request_id="req-soft-fail",
        session_id="sess-soft-fail",
        person_id="user-4",
        content="哥哥，好嘛，那我就当自己刚才多想了一点。",
        ts_ms=4,
        lang="zh",
        metadata={"provider_name": "gemini", "model_id": "gemini-2.5-pro", "fallback": False},
        reply_plan={
            "primary_topic": "emoji_social_followup",
            "tactic_family": "soft_emotion_ack",
            "tactic_variant": "backoff_hold",
            "response_form": "text",
            "response_form_reason": "image_social_followup",
        },
    )

    assert len(turn_store.calls) == 1
