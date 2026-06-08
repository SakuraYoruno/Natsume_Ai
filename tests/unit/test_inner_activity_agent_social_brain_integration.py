from __future__ import annotations

from host_brain.persona.inner_activity_agent import _collect_bond_context, generate_inner_activity_note
from host_brain.bond.user_identity import get_canonical_bond_user_id


def test_collect_bond_context_uses_social_brain_snapshot(monkeypatch) -> None:
    state_sentinel = object()

    class _SocialBrain:
        def resolve_bond_snapshot(self, *, person_id: str):
            assert person_id == get_canonical_bond_user_id()
            return {
                "bond_state_obj": state_sentinel,
                "bond_summary": {"stage": "FRIEND", "trend": "RISING"},
            }

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _SocialBrain(),
    )

    summary, state = _collect_bond_context(persona_user_id="user_42")

    assert summary["stage"] == "FRIEND"
    assert summary["trend"] == "RISING"
    assert state is state_sentinel


def test_collect_bond_context_degrades_when_snapshot_fails(monkeypatch) -> None:
    class _SocialBrain:
        def resolve_bond_snapshot(self, *, person_id: str):
            raise RuntimeError("snapshot_failed")

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _SocialBrain(),
    )

    summary, state = _collect_bond_context(persona_user_id="user_42")

    assert summary == {}
    assert state is None


def test_generate_inner_activity_note_ignores_heavy_background_disable(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")
    monkeypatch.delenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent._resolve_inner_activity_llm_runtime",
        lambda *, now_ms: {"available": False, "route_reason_code": "NO_MODEL"},
    )

    note, focus, reason, meta = generate_inner_activity_note(
        persona_user_id="user_42",
        absence_band="recent",
        offline_hours=0.0,
        inner_state={},
        now_ms=123456,
        topic_phrase="",
    )

    assert note == ""
    assert focus == ""
    assert reason == "NO_MODEL"
    assert meta == {}


def test_generate_inner_activity_note_respects_continuous_tick_disable(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent._resolve_inner_activity_llm_runtime",
        lambda *, now_ms: (_ for _ in ()).throw(AssertionError("runtime should not resolve")),
    )

    note, focus, reason, meta = generate_inner_activity_note(
        persona_user_id="user_42",
        absence_band="recent",
        offline_hours=0.0,
        inner_state={},
        now_ms=123456,
        topic_phrase="",
    )

    assert note == ""
    assert focus == ""
    assert reason == "INNER_ACTIVITY_CONTINUOUS_ORGAN_TICKS_DISABLED"
    assert meta == {}
