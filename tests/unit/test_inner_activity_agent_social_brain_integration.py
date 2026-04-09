from __future__ import annotations

from host_brain.persona.inner_activity_agent import _collect_bond_context


def test_collect_bond_context_uses_social_brain_snapshot(monkeypatch) -> None:
    state_sentinel = object()

    class _SocialBrain:
        def resolve_bond_snapshot(self, *, person_id: str):
            assert person_id == "user_42"
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
