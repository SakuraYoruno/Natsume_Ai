from __future__ import annotations

from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace
from host_brain.app.chat_routes import build_chat_bio_ops_preflight_trace
from host_brain.agent_runtime.fastpaths import _is_low_risk_short_chat_route_reason
from host_brain.app.schemas import ChatRequest
from host_brain.tribe.thalamus_agent import classify_task_intent_by_rules, classify_task_intent_fastpath
from tests.unit.test_owner_candidate_store import _queue_item, _write_json
from host_brain.organ_ops.owner_candidate_store import OwnerCandidateStateStore


def test_thalamus_short_ack_attaches_bio_ops_reflex_without_agent_worker() -> None:
    decision = classify_task_intent_fastpath("好的")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["bio_ops_reflex"]["matched"] is True
    assert "agent_worker" in decision["bio_ops_reflex"]["forbidden_actions"]


def test_task_like_message_does_not_get_reflex_short_circuited() -> None:
    message = "please fix the failing test and show me the code"
    assert classify_task_intent_fastpath(message) is None

    decision = classify_task_intent_by_rules(message)

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_worker"


def test_connectivity_route_reason_is_low_risk_short_chat() -> None:
    assert not _is_low_risk_short_chat_route_reason("THALAMUS_RULE_CONNECTIVITY_CHECK")
    assert not _is_low_risk_short_chat_route_reason("THALAMUS_RULE_PHATIC_GREETING")
    assert not _is_low_risk_short_chat_route_reason("RULE_FORCE_agent_worker")


def test_bio_ops_runtime_trace_observes_weather_breathing_and_silence() -> None:
    trace = build_bio_ops_runtime_trace(
        message="我好累",
        route_type="chat",
        target_id="",
        emotion_context={"emotion_tag": "EXHAUSTED", "stress_level": "high"},
        runtime_signals={
            "current_hour": 2,
            "codex_queue_size": 4,
            "tts_status": "degraded",
            "explicit_help_request": False,
        },
    )

    assert trace["proposal_only"] is True
    assert trace["reflex"]["matched"] is True
    assert trace["internal_weather"]["internal_weather"] == "static"
    assert trace["active_silence"]["silence_policy"] == "quiet_presence"
    assert trace["response_breathing"]["reply_density"] == "soft_brief"


def test_bio_ops_runtime_trace_marks_execution_routes_as_explicit_help() -> None:
    trace = build_bio_ops_runtime_trace(
        message="帮我修复测试",
        route_type="skill",
        target_id="agent_worker",
        emotion_context={"emotion_tag": "NEUTRAL"},
        runtime_signals={"current_hour": 2, "user_stress": "high"},
    )

    assert trace["reflex"]["matched"] is False
    assert trace["active_silence"]["silence_policy"] == "normal_presence"
    assert trace["response_breathing"]["reply_density"] == "compact_status"


def test_chat_preflight_trace_records_reflex_without_execution_actions() -> None:
    trace = build_chat_bio_ops_preflight_trace(ChatRequest(message="好的", emotion_context={"stress_level": "low"}))

    assert trace["proposal_only"] is True
    assert trace["reflex"]["matched"] is True
    assert "agent_worker" in trace["forbidden_actions"]
    assert trace["response_breathing"]["reply_density"] in {"normal", "soft_brief", "grounded_brief"}


def test_bio_ops_runtime_trace_loads_owner_runtime_hints(tmp_path) -> None:
    queue_root = tmp_path / "owner_queues"
    state_root = tmp_path / "owner_state"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item(summary="Prefer short owner-reviewed confirmations."))
    store = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root)
    store.import_all()
    store.review_candidate_state("candidate-1", owner_organ="social_brain_agent", decision="OWNER_ACCEPTED")

    trace = build_bio_ops_runtime_trace(
        message="谢谢",
        route_type="chat",
        runtime_signals={"owner_candidate_state_root": str(state_root)},
    )

    overlay = trace["owner_behavior_overlay"]
    assert overlay["applied"] is True
    assert overlay["hint_count"] == 1
    assert overlay["direct_long_term_write"] is False
    assert overlay["hints"][0]["guidance_type"] == "habit_hint"
    assert trace["response_breathing"]["owner_behavior_overlay_applied"] is True
