from __future__ import annotations

from host_brain.digital_life.companion_proactive import decide_companion_proactive
from natsume_contracts.life import (
    CompanionProfilePreferencesV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    baseline_companion_profile,
)


NOW = 1_783_814_400_000


def _state(
    *,
    permission: str = "ALLOWED",
    preferences: CompanionProfilePreferencesV1 | None = None,
) -> dict:
    if preferences is None:
        profile = baseline_companion_profile(
            workspace_id="workspace-a",
            relationship_subject_id="person-a",
        )
    else:
        profile = CompanionProfileVersionV1.create(
            workspace_id="workspace-a",
            relationship_subject_id="person-a",
            version=0,
            preferences=preferences,
            source_aftereffect_refs=(),
            evidence_days_utc=(),
            confidence_lower_bound=0.0,
            boundary_violation_count=0,
            rollback_profile_id=None,
            created_at_ms=0,
            is_baseline=True,
        )
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
        "companion_profile": CompanionProfileStateV1(
            workspace_id="workspace-a",
            relationship_subject_id="person-a",
            revision=5,
            active_profile=profile,
            known_profiles=(profile,),
        ).as_dict(),
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
    assert "不方便回也没关系" in decision.content
    trace = decision.as_trace()
    assert trace["content_digest"].startswith("sha256:")
    assert trace["affective_turn_plan_id"].startswith("affective-turn::")
    assert trace["synchronization_id"].startswith("sync::")
    assert decision.affective_turn_plan is not None
    assert decision.affective_turn_plan["self_affect_revision"] == 11
    assert decision.affective_turn_plan["relationship_revision"] == 7
    assert decision.affective_turn_plan["companion_profile_revision"] == 5
    assert decision.affective_turn_plan["companion_profile_stage"] == "BASELINE"
    assert (
        decision.affective_turn_plan["expression_plan"]["synchronization_id"]
        == trace["synchronization_id"]
    )


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


def test_compact_profile_makes_proactive_text_one_sentence_and_replayable() -> None:
    preferences = CompanionProfilePreferencesV1(response_length="COMPACT")
    first = decide_companion_proactive(
        _state(preferences=preferences),
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )
    second = decide_companion_proactive(
        _state(preferences=preferences),
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert first.send_allowed is True
    assert first == second
    assert first.content.count("。") == 1
    assert "？" not in first.content
    assert "不方便回也没关系" in first.content


def test_quiet_direct_profile_removes_followup_and_advice_without_losing_presence() -> None:
    preferences = CompanionProfilePreferencesV1(
        addressing_style="GENTLE_DIRECT",
        humor_level="LOW",
        followup_rate="NONE",
        comfort_mode="QUIET_FIRST",
    )
    direct_state = _state(preferences=preferences)
    direct_state["affective"]["components"] = [{"emotion": "CARE", "intensity": 0.7}]
    baseline_state = _state()
    baseline_state["affective"]["components"] = [{"emotion": "CARE", "intensity": 0.7}]

    direct = decide_companion_proactive(
        direct_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )
    baseline = decide_companion_proactive(
        baseline_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert direct.send_allowed is True
    assert direct.affective_turn_plan["companion_act"]["kind"] == "COMFORT"
    assert direct.content != baseline.content
    assert "？" not in direct.content
    assert "喝口水" not in direct.content
    assert "松一下肩" not in direct.content
    assert "不方便回也没关系" in direct.content


def test_practical_gentle_comfort_adds_only_an_optional_bounded_micro_step() -> None:
    preferences = CompanionProfilePreferencesV1(comfort_mode="PRACTICAL_GENTLE")
    state = _state(preferences=preferences)
    state["affective"]["components"] = [{"emotion": "CARE", "intensity": 0.7}]

    decision = decide_companion_proactive(
        state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert decision.send_allowed is True
    assert decision.affective_turn_plan["companion_act"]["kind"] == "COMFORT"
    assert "如果手边方便" in decision.content or "如果方便" in decision.content
    assert "也行" in decision.content
    assert "不方便回也没关系" in decision.content


def test_medium_humor_is_limited_to_play_not_comfort() -> None:
    preferences = CompanionProfilePreferencesV1(humor_level="MEDIUM")
    play_state = _state(preferences=preferences)
    play_state["affective"]["components"] = [
        {"emotion": "SHARED_JOY", "intensity": 0.7}
    ]
    comfort_state = _state(preferences=preferences)
    comfort_state["affective"]["components"] = [{"emotion": "CARE", "intensity": 0.7}]

    play = decide_companion_proactive(
        play_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )
    comfort = decide_companion_proactive(
        comfort_state,
        now_ms=NOW,
        timezone_name="Asia/Shanghai",
    )

    assert play.send_allowed is True
    assert play.affective_turn_plan["companion_act"]["kind"] == "PLAY"
    assert "我悄悄眨一下眼" in play.content
    assert comfort.send_allowed is True
    assert comfort.affective_turn_plan["companion_act"]["kind"] == "COMFORT"
    assert "我悄悄眨一下眼" not in comfort.content
