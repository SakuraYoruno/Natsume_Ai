from __future__ import annotations

from host_brain.digital_life.companion_proactive import decide_companion_proactive


NOW = 1_783_814_400_000


def _state(*, permission: str = "ALLOWED") -> dict:
    return {
        "proactive": {
            "last_target": {"platform": "qq", "conversation_id": "conversation-a"},
            "last_attempt_ms": 0,
            "last_sent_ms": 0,
        },
        "companion_proactive_target": {
            "schema_version": "companion_proactive_target.v1",
            "target_id": "companion_target_a",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-a",
            "relationship_subject_id": "person-a",
            "session_id": "session-a",
            "platform": "qq",
            "conversation_id": "conversation-a",
            "source_receipt_ref": "life-event://foreground-a",
            "registered_at_ms": NOW - 1,
        },
        "relationship_v2": {
            "schema_version": "relationship_state.v2",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-a",
            "relationship_subject_id": "person-a",
            "revision": 7,
            "trust": 0.74,
            "safety": 0.82,
            "attunement": 0.76,
            "rupture_load": 0.0,
            "repair_required": False,
            "proactive_permission": permission,
            "romantic_consent": "NOT_GRANTED",
            "exclusive_consent": "NOT_GRANTED",
            "dependency_consent": "NOT_GRANTED",
            "active_episodes": [],
            "last_aftereffect": None,
        },
        "affective": {
            "schema_version": "self_affect_state.v2",
            "self_id": "natsume_ai",
            "relationship_subject_id": "person-a",
            "revision": 11,
            "components": [],
            "active_episodes": [],
            "mood": {
                "label": "calm",
                "valence": 0.2,
                "arousal": 0.2,
                "energy": 0.72,
                "updated_at_ms": NOW,
            },
            "needs": {
                "connection": 0.58,
                "rest": 0.12,
                "autonomy": 0.2,
                "play": 0.2,
                "repair": 0.0,
                "safety": 0.1,
                "updated_at_ms": NOW,
            },
            "last_regulation": {},
        },
        "clock": {"last_user_seen_ms": NOW - 5 * 60 * 60 * 1_000},
        # A deliberately hostile compatibility value proves the decision does
        # not read legacy self-affect as an authority.
        "affect": {"tension": 1.0, "fatigue": 1.0, "loneliness": 1.0},
    }


def test_canonical_proactive_requires_explicit_permission_not_bond_or_legacy_affect() -> None:
    decision = decide_companion_proactive(
        _state(permission="ALLOWED"),
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert decision.send_allowed is True
    assert decision.reason_code == "PROACTIVE_CANONICAL_IMPULSE_READY"
    assert decision.relationship_revision == 7
    assert decision.self_affect_revision == 11
    assert decision.target_id == "companion_target_a"
    assert "不用回" in decision.content
    assert decision.as_trace()["content_digest"].startswith("sha256:")


def test_unknown_or_denied_permission_holds_without_sending() -> None:
    for permission in ("UNKNOWN", "DENIED"):
        decision = decide_companion_proactive(
            _state(permission=permission),
            now_ms=NOW,
            timezone_name="Asia/Shanghai",
        )
        assert decision.send_allowed is False
        assert decision.reason_code == "PROACTIVE_PERMISSION_NOT_ALLOWED"
        assert decision.content == ""


def test_legacy_last_target_cannot_authorize_a_send_without_canonical_registry() -> None:
    state = _state()
    state.pop("companion_proactive_target")

    decision = decide_companion_proactive(
        state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert decision.send_allowed is False
    assert decision.reason_code == "PROACTIVE_TARGET_UNAVAILABLE"


def test_unresolved_relationship_or_self_regulation_holds_the_impulse() -> None:
    repair_state = _state()
    repair_state["relationship_v2"]["repair_required"] = True
    repair = decide_companion_proactive(
        repair_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )
    assert repair.send_allowed is False
    assert repair.reason_code == "PROACTIVE_REPAIR_REQUIRED"

    regulated_state = _state()
    regulated_state["affective"]["last_regulation"] = {"mode": "GIVE_SPACE"}
    regulated = decide_companion_proactive(
        regulated_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )
    assert regulated.send_allowed is False
    assert regulated.reason_code == "PROACTIVE_SELF_REGULATION_HOLD"


def test_same_frozen_state_and_timezone_produces_the_same_decision() -> None:
    first = decide_companion_proactive(
        _state(), now_ms=NOW, timezone_name="Asia/Shanghai"
    )
    second = decide_companion_proactive(
        _state(), now_ms=NOW, timezone_name="Asia/Shanghai"
    )

    assert first == second
