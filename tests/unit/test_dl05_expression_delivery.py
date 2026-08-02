from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from host_brain.digital_life.affective_expression_delivery import (
    build_buffered_expression_delivery,
    build_expression_delivery_controls,
    build_sentence_expression_delivery,
    expression_delivery_trace,
    previous_durable_avatar_emotion_tag,
)
from host_brain.digital_life.companion_expression_acceptance import (
    COMPANION_EXPRESSION_ENQUEUE_EFFECT,
    CompanionExpressionEffectError,
    compile_companion_expression_effect_payloads,
    parse_companion_expression_effect_payload,
)
from host_brain.app.accepted_companion_expression_handler import (
    AcceptedCompanionExpressionEnqueueHandler,
)
from host_brain.digital_life.affective_turn_planner import build_affective_turn_guidance
from host_brain.surface_runtime.reply_context import _surface_emotion_context
from host_brain.surface_runtime.delivery_audit import _bounded_expression_segments
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from natsume_contracts.life import digest_json


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
    assert intents[0].payload["voice_profile"] == plan["expression_plan"]["voice_style"]
    assert intents[0].payload["emotion_tag"] == plan["self_emotion_tag"]
    assert intents[1].payload["edge_action"] == "set_expression"
    assert intents[1].payload["previous_expression"] == "SOFT_COMFORT"
    assert all(item.status == "HOST_PROTOCOL_STAGED" for item in receipts)
    assert all(item.physical_receipt_ref is None for item in receipts)
    trace = expression_delivery_trace(intents, receipts)
    assert trace["channels"] == ["VOICE", "AVATAR"]
    assert "难过" not in str(trace)
    assert "EDGE_APPLIED" not in str(trace)


def test_sentence_expression_protocol_keeps_chinese_display_and_japanese_voice_separate() -> None:
    intents, _receipts = build_sentence_expression_delivery(
        affective_turn_plan=_plan(),
        sentence="先歇一下，我就在这里。",
        speech_sentence="少し休もっか。私はここにいるよ。",
        speech_language="ja",
        sentence_index=0,
        created_at_ms=NOW + 1,
    )

    voice = intents[0]
    assert voice.payload["display_text"] == "先歇一下，我就在这里。"
    assert voice.payload["text"] == "少し休もっか。私はここにいるよ。"
    assert voice.payload["lang_hint"] == "ja"

    with pytest.raises(ValueError, match="bounded Japanese"):
        build_sentence_expression_delivery(
            affective_turn_plan=_plan(),
            sentence="我在。",
            speech_sentence="这不是日语。",
            speech_language="ja",
            sentence_index=0,
            created_at_ms=NOW + 1,
        )


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


def test_surface_foreground_stages_same_call_japanese_speech_text(monkeypatch) -> None:
    from host_brain.surface_runtime import delivery_audit

    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    submitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        delivery_audit,
        "submit_life_command",
        lambda **kwargs: submitted.append(kwargs)
        or {
            "status": "APPLIED",
            "command_id": kwargs["command_id"],
            "event_id": "event-dl05-ja",
            "state_version": 1,
            "replayed": False,
        },
    )
    agent = SimpleNamespace(
        request_id="request-dl05",
        workspace_id="workspace-dl05",
        person_id="person-dl05",
        session_id="session-dl05",
        user_message="有点累。",
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
    narrative = "那就先歇一下，我陪你待会儿。"
    speech = "じゃあ、少し休もっか。私がそばにいるから。"
    result = SimpleNamespace(
        selected_text=narrative,
        tts_text_ja=speech,
        natsume_emotion_tag="CALM",
        user_emotion_tag="SAD",
        vts_expression_id="calm",
        observation_codes=(),
        compiled=SimpleNamespace(trace={}),
        runtime=SimpleNamespace(
            tts_text_ja=speech,
            emotion_confidence=0.8,
            generation_trace={},
            latency_ms=12,
        ),
    )

    delivery_audit._submit_surface_foreground_completed(
        life_command_service=object(),
        life_snapshot_reader=object(),
        relationship_lifecycle_service=None,
        agent_input=agent,
        result=result,
        envelope=ReplyEnvelope.final_reply(text=narrative, source="test"),
        narrative=narrative,
        trace_stage_meta={"affective_turn_plan": _plan()},
        timings_ms={"surface_main_chain_ms": 12},
        total_latency_ms=12,
        llm_latency_ms=10,
    )

    payload = submitted[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["assistant_reply"] == narrative
    assert payload["tts_text_ja"] == speech
    voice = payload["expression_delivery_intents"][0]
    assert voice["payload"]["display_text"] == narrative
    assert voice["payload"]["text"] == speech
    assert voice["payload"]["lang_hint"] == "ja"


def _governed_foreground(*, narrative: str = "我在，慢一点也没关系。") -> dict[str, object]:
    plan = _plan()
    intents, controls, receipts = build_buffered_expression_delivery(
        affective_turn_plan=plan,
        narrative=narrative,
        created_at_ms=NOW + 3,
    )
    return {
        "command_type": "foreground_completed",
        "person_id": "person-dl05",
        "command_id": "life::foreground_completed::request-dl05",
        "correlation_id": "request-dl05",
        "payload": {
            "assistant_reply": narrative,
            "affective_turn_plan": plan,
            "affective_turn_plan_digest": digest_json(plan),
            "expression_delivery_intents": [item.as_dict() for item in intents],
            "expression_delivery_controls": [item.as_dict() for item in controls],
            "expression_delivery_receipts": [item.as_dict() for item in receipts],
        },
        "created_at_ms": NOW + 3,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


def _companion_effects() -> tuple[dict[str, object], ...]:
    return compile_companion_expression_effect_payloads(
        graph_id="graph-dl05",
        graph_revision=1,
        node_id="node-dl05",
        attempt_id="attempt-dl05",
        request_id="request-dl05",
        principal_id="person-dl05",
        relationship_subject_id="person-dl05",
        workspace_id="workspace-dl05",
        session_id="session-dl05",
        node_grant_ref="node-grant://dl05",
        foreground=_governed_foreground(),
    )


def test_governed_expression_acceptance_rebuilds_exact_host_outbox_commands() -> None:
    effects = _companion_effects()

    assert len(effects) == 4  # paired sentence protocol followed by paired release
    assert [effect["delivery_sequence"] for effect in effects] == [0, 1, 2, 3]
    parsed = [parse_companion_expression_effect_payload(item) for item in effects]
    assert [item["command"].kind for item in parsed] == ["voice", "avatar", "voice", "avatar"]
    assert [item["command"].action for item in parsed] == ["speak", "set_expression", "release", "release"]
    assert all(item["command"].session_id == "session-dl05" for item in parsed)
    assert all(item["intent"].self_affect_revision == 31 for item in parsed)
    assert all(item["protocol_receipt"].physical_receipt_ref is None for item in parsed)
    assert all(
        item["command"].authority_ref.startswith("authority://task-acceptance/sha256/")
        for item in parsed
    )
    assert COMPANION_EXPRESSION_ENQUEUE_EFFECT == "host.companion_expression_enqueue"


def test_governed_expression_acceptance_binds_japanese_speech_to_chinese_narrative() -> None:
    narrative = "先歇一下，我就在这里。"
    speech = "少し休もっか。私はここにいるよ。"
    plan = _plan()
    intents, sentence_receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan,
        sentence=narrative,
        speech_sentence=speech,
        speech_language="ja",
        sentence_index=0,
        created_at_ms=NOW + 3,
    )
    controls, control_receipts = build_expression_delivery_controls(
        affective_turn_plan=plan,
        action="RELEASE",
        sent_sentence_count=1,
        created_at_ms=NOW + 3,
    )
    foreground = _governed_foreground(narrative=narrative)
    payload = foreground["payload"]
    assert isinstance(payload, dict)
    payload["tts_text_ja"] = speech
    payload["expression_delivery_intents"] = [item.as_dict() for item in intents]
    payload["expression_delivery_controls"] = [item.as_dict() for item in controls]
    payload["expression_delivery_receipts"] = [
        *(item.as_dict() for item in sentence_receipts),
        *(item.as_dict() for item in control_receipts),
    ]

    effects = compile_companion_expression_effect_payloads(
        graph_id="graph-dl05",
        graph_revision=1,
        node_id="node-dl05",
        attempt_id="attempt-dl05",
        request_id="request-dl05",
        principal_id="person-dl05",
        relationship_subject_id="person-dl05",
        workspace_id="workspace-dl05",
        session_id="session-dl05",
        node_grant_ref="node-grant://dl05",
        foreground=foreground,
    )

    voice = parse_companion_expression_effect_payload(effects[0])["command"]
    assert voice.payload["text"] == speech
    assert voice.payload["display_text"] == narrative
    assert voice.payload["lang_hint"] == "ja"

    payload["tts_text_ja"] = "別の台詞だよ。"
    with pytest.raises(CompanionExpressionEffectError, match="PROTOCOL_DRIFT"):
        compile_companion_expression_effect_payloads(
            graph_id="graph-dl05",
            graph_revision=1,
            node_id="node-dl05",
            attempt_id="attempt-dl05",
            request_id="request-dl05",
            principal_id="person-dl05",
            relationship_subject_id="person-dl05",
            workspace_id="workspace-dl05",
            session_id="session-dl05",
            node_grant_ref="node-grant://dl05",
            foreground=foreground,
        )


def test_governed_expression_acceptance_rejects_worker_protocol_drift() -> None:
    foreground = _governed_foreground()
    payload = foreground["payload"]
    assert isinstance(payload, dict)
    intents = payload["expression_delivery_intents"]
    assert isinstance(intents, list)
    intents[0] = {**intents[0], "payload": {**intents[0]["payload"], "text": "伪造"}}

    with pytest.raises(CompanionExpressionEffectError, match="PROTOCOL_DRIFT"):
        compile_companion_expression_effect_payloads(
            graph_id="graph-dl05",
            graph_revision=1,
            node_id="node-dl05",
            attempt_id="attempt-dl05",
            request_id="request-dl05",
            principal_id="person-dl05",
            relationship_subject_id="person-dl05",
            workspace_id="workspace-dl05",
            session_id="session-dl05",
            node_grant_ref="node-grant://dl05",
            foreground=foreground,
        )


def test_governed_expression_acceptance_keeps_service_principal_separate_from_companion_subject() -> None:
    effects = compile_companion_expression_effect_payloads(
        graph_id="graph-dl05",
        graph_revision=1,
        node_id="node-dl05",
        attempt_id="attempt-dl05",
        request_id="request-dl05",
        principal_id="service.edge.gateway",
        relationship_subject_id="person-dl05",
        workspace_id="workspace-dl05",
        session_id="session-dl05",
        node_grant_ref="node-grant://dl05",
        foreground=_governed_foreground(),
    )

    assert len(effects) == 4
    assert all(
        parse_companion_expression_effect_payload(effect)["life_foreground"]["person_id"]
        == "person-dl05"
        for effect in effects
    )


def test_governed_expression_acceptance_rejects_subject_or_workspace_drift() -> None:
    with pytest.raises(CompanionExpressionEffectError, match="SCOPE_DRIFT"):
        compile_companion_expression_effect_payloads(
            graph_id="graph-dl05",
            graph_revision=1,
            node_id="node-dl05",
            attempt_id="attempt-dl05",
            request_id="request-dl05",
            principal_id="service.edge.gateway",
            relationship_subject_id="other-person",
            workspace_id="other-workspace",
            session_id="session-dl05",
            node_grant_ref="node-grant://dl05",
            foreground=_governed_foreground(),
        )


def test_governed_expression_acceptance_preserves_frozen_sentence_boundaries() -> None:
    foreground = _governed_foreground(narrative="先听我说再缓一缓")
    payload = foreground["payload"]
    assert isinstance(payload, dict)
    plan = payload["affective_turn_plan"]
    assert isinstance(plan, dict)
    first_intents, first_receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan,
        sentence="先听我说",
        sentence_index=0,
        created_at_ms=NOW + 3,
    )
    second_intents, second_receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan,
        sentence="再缓一缓",
        sentence_index=1,
        created_at_ms=NOW + 3,
    )
    controls, control_receipts = build_expression_delivery_controls(
        affective_turn_plan=plan,
        action="RELEASE",
        sent_sentence_count=2,
        created_at_ms=NOW + 3,
    )
    payload["expression_delivery_intents"] = [
        *(item.as_dict() for item in first_intents),
        *(item.as_dict() for item in second_intents),
    ]
    payload["expression_delivery_controls"] = [
        *(item.as_dict() for item in controls),
    ]
    payload["expression_delivery_receipts"] = [
        *(item.as_dict() for item in first_receipts),
        *(item.as_dict() for item in second_receipts),
        *(item.as_dict() for item in control_receipts),
    ]

    effects = compile_companion_expression_effect_payloads(
        graph_id="graph-dl05",
        graph_revision=1,
        node_id="node-dl05",
        attempt_id="attempt-dl05",
        request_id="request-dl05",
        principal_id="service.edge.gateway",
        relationship_subject_id="person-dl05",
        workspace_id="workspace-dl05",
        session_id="session-dl05",
        node_grant_ref="node-grant://dl05",
        foreground=foreground,
    )

    assert len(effects) == 6


def test_accepted_companion_expression_only_enqueues_host_outbox(monkeypatch) -> None:
    from host_brain.governor import action_preview

    effect = _companion_effects()[0]
    business = dict(effect)
    calls: list[object] = []

    class PreviewService:
        def require_execution(self, **kwargs: object) -> object:
            calls.append(kwargs)
            encoded = json.dumps(
                business,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return SimpleNamespace(
                payload={
                    "authority_kind": "ACCEPTANCE_EFFECT",
                    "action_kind": "acceptance-effect.host.companion_expression_enqueue",
                    "action_scope": "acceptance-effect://dl05/0",
                    "risk_level": 4,
                    "action_args_digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                }
            )

    monkeypatch.setattr(action_preview, "ActionPreviewService", PreviewService)

    class Runtime:
        runtime_scope_id = "runtime-dl05"

        def __init__(self) -> None:
            self.rows: dict[str, dict[str, object]] = {}

        def enqueue(self, command: object, **_kwargs: object) -> object:
            calls.append(command)
            self.rows[command.command_id] = {
                "outbox_id": "outbox-dl05",
                "runtime_scope_id": self.runtime_scope_id,
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
                "status": "PENDING",
                "command": command.as_dict(),
            }
            return SimpleNamespace(
                outbox_id="outbox-dl05",
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
            )

        def reopen_outbox_command(self, command: object) -> dict[str, object] | None:
            return self.rows.get(command.command_id)

    runtime = Runtime()
    life = effect["life_foreground"]
    assert isinstance(life, dict)

    class Reader:
        def get_receipt(self, command_id: str) -> dict[str, object] | None:
            if command_id != life["command_id"]:
                return None
            return {
                "status": "APPLIED",
                "command_id": life["command_id"],
                "person_id": life["person_id"],
                "command_hash": life["command_hash"],
                "event_id": "life_evt_dl05",
            }

    boundary = {
        "action_kind": "acceptance-effect.host.companion_expression_enqueue",
        "action_scope": "acceptance-effect://dl05/0",
        "risk_level": 4,
        "action_args": business,
        "node_grant_ref": "node-grant://dl05",
        "authority_context": {
            "schema_version": "Natsume.action_preview.acceptance_effect_authority_request.v1",
            "effect_id": "acceptance-effect://dl05/0",
            "lease_owner_id": "dispatcher-dl05",
            "fencing_token": 1,
        },
    }
    handler = AcceptedCompanionExpressionEnqueueHandler(
        runtime_provider=lambda: runtime,
        life_receipt_reader_provider=Reader,
    )
    receipt = handler(
        {
            **business,
            "_action_preview_terminal_proof": {
                "service": PreviewService(),
                "event_id": "preview-dl05",
                "boundary": boundary,
            },
        }
    )

    assert receipt["host_outbox_status"] == "PENDING"
    assert receipt["physical_receipt_ref"] is None
    assert receipt["receipt_ref"].startswith("embodiment-outbox://")
    assert len(runtime.rows) == 1
    assert len([item for item in calls if hasattr(item, "command_id")]) == 1


def test_production_handler_registry_requires_life_receipt_reader() -> None:
    from host_brain.app.acceptance_effect_handlers import (
        build_production_acceptance_effect_handlers,
    )

    handlers = build_production_acceptance_effect_handlers(
        embodiment_runtime_provider=lambda: object(),
        life_receipt_reader_provider=lambda: object(),
    )

    assert "host.companion_expression_enqueue" in handlers
    assert isinstance(
        handlers["host.companion_expression_enqueue"],
        AcceptedCompanionExpressionEnqueueHandler,
    )
