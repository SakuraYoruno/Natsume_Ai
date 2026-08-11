from __future__ import annotations

from host_brain.skills.impl.body_sense_skill import (
    execute_body_sense_conversation_end,
    execute_body_sense_conversation_start,
    execute_body_sense_tick,
    execute_body_sense_update_loneliness,
)


class _FakeLimbicAgent:
    def __init__(self) -> None:
        self.tick_calls: list[dict[str, object]] = []
        self.conversation_calls: list[dict[str, object]] = []
        self.loneliness_calls: list[dict[str, object]] = []

    def tick_body_sense(self, *, current_hour=None):
        self.tick_calls.append({"current_hour": current_hour})
        return {
            "status": "OK",
            "body_sense": {
                "fatigue": 0.2,
                "drowsiness": 0.1,
                "loneliness": 0.0,
                "willingness_penalty": 0.0,
            },
        }

    def capture_conversation_body_sense(self, **kwargs):
        self.conversation_calls.append(dict(kwargs))
        return {
            "status": "OK",
            "body_sense": {
                "fatigue": 0.2,
                "drowsiness": 0.1,
                "loneliness": 0.0,
                "loneliness_level": "FINE",
            },
        }

    def update_body_sense_loneliness(self, *, loneliness_value: float, person_id=None):
        self.loneliness_calls.append(
            {"loneliness_value": loneliness_value, "person_id": person_id}
        )
        return {
            "status": "OK",
            "body_sense": {
                "fatigue": 0.2,
                "drowsiness": 0.1,
                "loneliness": float(loneliness_value),
                "loneliness_level": "LONELY" if float(loneliness_value) >= 0.3 else "FINE",
            },
        }


class _CanonicalFakeLimbicAgent:
    def uses_canonical_affective_life(self) -> bool:
        return True

    def __getattr__(self, name: str):  # noqa: ANN204
        raise AssertionError(f"canonical body-sense skill must not call legacy Limbic method: {name}")


def test_body_sense_tick_emits_updated_signal(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_background_tick_guard_state",
        lambda **_kwargs: {
            "active": False,
            "cooldown_remaining_ms": 0,
        },
    )

    result = execute_body_sense_tick(args={"current_hour": 2})

    assert result["status"] == "OK"
    assert result["body_sense"]["fatigue"] == 0.2
    assert fake_limbic.tick_calls[0]["current_hour"] == 2


def test_body_sense_tick_skips_during_recent_foreground_chat(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_background_tick_guard_state",
        lambda **_kwargs: {
            "active": True,
            "cooldown_remaining_ms": 19000,
        },
    )

    result = execute_body_sense_tick(args={"current_hour": 2})

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN"
    assert result["cooldown_remaining_ms"] == 19000
    assert fake_limbic.tick_calls == []


def test_body_sense_conversation_start_emits_conversation_started(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_body_sense_conversation_start(
        args={},
        context={
            "request_id": "req_1",
            "session_id": "sess_1",
            "user_id": "user_1",
            "emotion_context": {"emotion_tag": "CALM", "mood_state": "steady"},
        },
    )

    assert result["status"] == "OK"
    assert result["body_sense"]["fatigue"] == 0.2
    assert fake_limbic.conversation_calls[0]["request_id"] == "req_1"
    assert fake_limbic.conversation_calls[0]["person_id"] == "user_1"


def test_body_sense_update_loneliness_emits_updated_signal(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_body_sense_update_loneliness(args={"loneliness_value": 0.5})

    assert result["status"] == "OK"
    assert result["body_sense"]["loneliness"] == 0.5
    assert fake_limbic.loneliness_calls[0]["loneliness_value"] == 0.5
    assert result["body_sense"]["loneliness_level"] == "LONELY"


def test_body_sense_update_loneliness_rejects_invalid_value() -> None:
    result = execute_body_sense_update_loneliness(args={"loneliness_value": "bad"})

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "BODY_SENSE_LONELINESS_INVALID"


def test_body_sense_tick_does_not_publish_retired_canonical_operation(monkeypatch) -> None:
    class _RetiredLimbicAgent:
        def tick_body_sense(self, *, current_hour=None):
            assert current_hour == 2
            return {
                "status": "RETIRED_CANONICAL_LIFE",
                "reason_code": "LEGACY_BODY_SENSE_MUTATION_RETIRED",
            }

    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: _RetiredLimbicAgent(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_background_tick_guard_state",
        lambda **_kwargs: {"active": False, "cooldown_remaining_ms": 0},
    )
    monkeypatch.setattr(
        "host_brain.tribe.ans_agent.get_ans_agent",
        lambda: (_ for _ in ()).throw(
            AssertionError("retired body sense must not publish an update")
        ),
    )

    result = execute_body_sense_tick(args={"current_hour": 2})

    assert result["status"] == "RETIRED_CANONICAL_LIFE"


def test_body_sense_skills_retire_before_mutable_legacy_calls_in_canonical_host(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: _CanonicalFakeLimbicAgent(),
    )

    tick = execute_body_sense_tick(args={"current_hour": 2})
    started = execute_body_sense_conversation_start(
        args={"offline_loneliness": 1.0},
        context={
            "request_id": "req-1",
            "session_id": "session-1",
            "user_id": "person-a",
            "emotion_context": {"emotion_tag": "SAD"},
        },
    )
    ended = execute_body_sense_conversation_end(
        args={"duration_minutes": 45},
        context={"user_id": "person-a"},
    )
    loneliness = execute_body_sense_update_loneliness(
        args={"loneliness_value": 0.9},
        context={"user_id": "person-a"},
    )

    for result, operation in (
        (tick, "body_sense_tick"),
        (started, "conversation_body_sense_start"),
        (ended, "conversation_body_sense_end"),
        (loneliness, "body_sense_loneliness_update"),
    ):
        assert result["status"] == "RETIRED_CANONICAL_LIFE"
        assert result["authority"] == "CANONICAL_LIFE_JOURNAL"
        assert result["reason_code"] == "LEGACY_BODY_SENSE_MUTATION_RETIRED"
        assert result["operation"] == operation
        assert result["body_sense"] == {}
    assert started["relationship_subject_id"] == "person-a"
