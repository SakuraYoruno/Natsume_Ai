from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.digital_life.affective_expression_delivery import (
    build_expression_delivery_controls,
    build_sentence_expression_delivery,
    expression_delivery_trace,
    previous_durable_avatar_emotion_tag,
)
from host_brain.digital_life.affective_turn_planner import build_affective_turn_guidance
from host_brain.surface_runtime.reply_context import _surface_emotion_context
from host_brain.surface_runtime.delivery_audit import _bounded_expression_segments
from host_brain.input_runtime.reply_envelope import ReplyEnvelope


NOW = 1_900_000_000_000


def _plan(*, previous: str = "SOFT_COMFORT") -> dict[str, object]:
    guidance = build_affective_turn_guidance(
        emotion_context={
            "_affective_life_internal": True,
            "affective_life": {
                "schema_version": "affective_turn_context.v1",
                "self_id": "natsume_ai",
                "workspace_id": "workspace-dl05",
                "relationship_subject_id": "person-dl05",
                "session_id": "session-dl05",
                "self_affect": {
                    "self_id": "natsume_ai",
                    "relationship_subject_id": "person-dl05",
                    "revision": 31,
                    "components": [{"emotion": "HURT", "intensity": 0.61}],
                    "last_regulation": {"regulation_id": "reg-dl05", "mode": "EXPRESS"},
                },
                "user_observations": [],
                "relationship_life": {
                    "state": {
                        "relationship_subject_id": "person-dl05",
                        "revision": 7,
                        "repair_required": False,
                        "proactive_permission": "UNKNOWN",
                    },
                    "aftereffect": {"outcome": "UNKNOWN"},
                },
            },
        },
        request_id="request-dl05",
        workspace_id="workspace-dl05",
        relationship_subject_id="person-dl05",
        session_id="session-dl05",
        created_at_ms=NOW,
        previous_avatar_expression=previous,
        previous_expression_source="DURABLE_LAST_ACCEPTED",
    )
    assert guidance is not None
    return guidance.plan.as_dict()


def test_sentence_expression_protocol_is_paired_and_has_no_edge_applied_claim() -> None:
    plan = _plan()

    intents, receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan,
        sentence="刚刚那句话让我有点难过，但我愿意慢慢听你说。",
        sentence_index=2,
        created_at_ms=NOW + 1,
    )

    assert [item.channel for item in intents] == ["VOICE", "AVATAR"]
    assert [item.action for item in intents] == ["SPEAK", "TRANSITION"]
    assert len({item.synchronization_id for item in intents}) == 1
    assert len({item.self_affect_revision for item in intents}) == 1
    assert all(item.self_affect_revision == 31 for item in intents)
    assert intents[0].payload["edge_action"] == "speak"
    assert intents[1].payload["edge_action"] == "set_expression"
    assert intents[1].payload["previous_expression"] == "SOFT_COMFORT"
    assert all(item.status == "HOST_PROTOCOL_STAGED" for item in receipts)
    assert all(item.physical_receipt_ref is None for item in receipts)
    trace = expression_delivery_trace(intents, receipts)
    assert trace["channels"] == ["VOICE", "AVATAR"]
    assert "难过" not in str(trace)
    assert "EDGE_APPLIED" not in str(trace)


@pytest.mark.parametrize("action,status", [("CANCEL", "HOST_CANCEL_STAGED"), ("RELEASE", "HOST_RELEASE_STAGED")])
def test_expression_controls_are_deterministic_and_only_cover_completed_prefix(
    action: str,
    status: str,
) -> None:
    plan = _plan()

    first = build_expression_delivery_controls(
        affective_turn_plan=plan,
        action=action,
        sent_sentence_count=3,
        created_at_ms=NOW + 2,
        reason_code="USER_BARGE_IN",
    )
    second = build_expression_delivery_controls(
        affective_turn_plan=plan,
        action=action,
        sent_sentence_count=3,
        created_at_ms=NOW + 2,
        reason_code="USER_BARGE_IN",
    )

    intents, receipts = first
    assert [item.delivery_id for item in intents] == [item.delivery_id for item in second[0]]
    assert [item.action for item in intents] == [action, action]
    assert all(item.sentence_index == 3 for item in intents)
    assert all(item.status == status for item in receipts)
    assert all("text" not in item.payload for item in intents)


def test_surface_emotion_context_and_vts_transition_ignore_legacy_previous_tag() -> None:
    plan = _plan(previous="SHARED_JOY")
    agent = SimpleNamespace(
        emotion_context={
            "_prev_emotion_tag": "ANGRY",
            "_affective_life_internal": True,
        },
        emotion_snapshot={},
    )

    context = _surface_emotion_context(
        agent,
        trace_stage_meta={"affective_turn_plan": plan},
    )

    assert context["emotion_tag"] == "SAD"
    assert context["_prev_emotion_tag"] == "ANGRY"  # compatibility field remains inert
    assert context["_affective_expression_internal"]["previous_avatar_expression"] == "SHARED_JOY"
    assert previous_durable_avatar_emotion_tag(context) == "HAPPY"
    assert previous_durable_avatar_emotion_tag({"_prev_emotion_tag": "ANGRY"}) is None


def test_invalid_expression_protocol_fails_closed_before_any_channel_claim() -> None:
    plan = _plan()
    plan["expression_plan"] = {"synchronization_id": "sync-only"}

    with pytest.raises(ValueError, match="AFFECTIVE_EXPRESSION"):
        build_sentence_expression_delivery(
            affective_turn_plan=plan,
            sentence="不会发出去。",
            sentence_index=0,
            created_at_ms=NOW,
        )


def test_buffered_expression_segments_preserve_all_content_under_channel_bound() -> None:
    narrative = ("第一段先说清楚。" * 260) + "最后一句不能丢。"

    segments = _bounded_expression_segments(narrative, maximum=2_000)

    assert len(segments) >= 2
    assert all(0 < len(item) <= 2_000 for item in segments)
    assert "".join(segments) == narrative


def test_canonical_expression_plan_retires_legacy_embodiment_delivery_path(monkeypatch) -> None:
    from host_brain.surface_runtime import delivery_audit

    submitted: list[dict[str, object]] = []

    class Authorization:
        def as_dict(self) -> dict[str, str]:
            return {"request_id": "request-dl05", "authority_kind": "test"}

    monkeypatch.setattr(
        delivery_audit,
        "_surface_delivery_authorization",
        lambda *_args, **_kwargs: Authorization(),
    )
    monkeypatch.setattr(
        delivery_audit,
        "submit_life_command",
        lambda **kwargs: submitted.append(kwargs)
        or {
            "status": "APPLIED",
            "command_id": kwargs["command_id"],
            "event_id": "event-dl05",
            "state_version": 1,
            "replayed": False,
        },
    )
    agent = SimpleNamespace(
        request_id="request-dl05",
        workspace_id="workspace-dl05",
        person_id="person-dl05",
        session_id="session-dl05",
        user_message="在吗",
        ts_ms=NOW,
        reply_channel="gateway",
        reply_target="target-dl05",
        bond_summary={},
        emotion_context={},
        emotion_snapshot={},
        render_policy={},
        governor_result={"decision": "APPROVE"},
        delivery_authorization={},
        defer_effects_to_task_runtime=False,
    )
    trace = {
        "affective_turn_plan": _plan(),
        "life_director": {"embodiment_target": {"emotion_tag": "HAPPY"}},
    }

    receipt = delivery_audit._submit_surface_foreground_completed(
        life_command_service=object(),
        life_snapshot_reader=object(),
        relationship_lifecycle_service=None,
        agent_input=agent,
        result=None,
        envelope=ReplyEnvelope.final_reply(text="我在。", source="test"),
        narrative="我在。",
        trace_stage_meta=trace,
        timings_ms={"surface_main_chain_ms": 1},
        total_latency_ms=1,
        llm_latency_ms=1,
    )

    assert receipt["expression_delivery"]["status"] == "HOST_PROTOCOL_STAGED"
    payload = submitted[0]["payload"]
    assert payload["deliveries"] == []
    assert payload["legacy_direct_embodiment_delivery_retired"] is True
    assert [item["channel"] for item in payload["expression_delivery_intents"]] == [
        "VOICE",
        "AVATAR",
    ]
