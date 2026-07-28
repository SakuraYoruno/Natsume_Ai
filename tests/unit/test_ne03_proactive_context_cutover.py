from __future__ import annotations

from types import SimpleNamespace

from host_brain.core.emotion_timeline import RetiredEmotionTimelineProjection
from host_brain.lifecycle_runtime import proactive_dispatch
from host_brain.lifecycle_runtime.proactive_dispatch import (
    _require_proactive_runtime_context,
    run_proactive_intent_dispatch,
)
from host_brain.runtime.context_contracts import SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
)
from host_brain.scheduler.governed_runtime import GovernedSchedulerRuntime
from host_brain.tribe.ans_agent import ANSAgent


NOW = 1_900_000_000_000


def _frozen_scene() -> dict:
    decision = SceneDecisionV2(
        workspace_id="tenant:workspace-ne03",
        subject_id="person-ne03",
        scene_id="scene.casual-chat",
        revision=7,
        world_snapshot_digest="1" * 64,
        context_digest="2" * 64,
        guardian_snapshot_digest="3" * 64,
        constraints=(),
    )
    return {
        "runtime_context_schema": "natsume.runtime-context.request.v1",
        "workspace_id": decision.workspace_id,
        "subject_id": decision.subject_id,
        "scene_decision_v2": decision.to_dict(),
        "scene_identity_v2": SceneIdentityV2.from_decision(decision).to_dict(),
    }


class _RuntimeContextAuthority:
    def __init__(self, frozen: dict) -> None:
        self.frozen = dict(frozen)
        self.freeze_calls: list[dict] = []
        self.require_calls: list[dict] = []

    def freeze_request(self, **kwargs):
        self.freeze_calls.append(dict(kwargs))
        return SimpleNamespace(to_dict=lambda: dict(self.frozen))

    def require_frozen(self, value):
        payload = dict(value)
        self.require_calls.append(payload)
        if payload.get("scene_identity_v2") != self.frozen["scene_identity_v2"]:
            raise ValueError("scene identity drift")
        return SimpleNamespace(to_dict=lambda: dict(self.frozen))


def _execution_context(frozen: dict) -> dict:
    context = {
        "work_order_id": "work-order-ne03",
        "graph_id": "graph-ne03",
        "graph_revision": 1,
        "node_id": "node-ne03",
        "attempt_id": "attempt-ne03",
        "lease_id": "lease-ne03",
        "fencing_token": 4,
        "node_grant_ref": "governor://decision/child-ne03",
        "frozen_input_pack": {
            "occurrence_id": "scheduleroccurrence://sha256/" + "a" * 64,
            "definition_ref": "scheduler-definition://proactive/1/" + "b" * 64,
            **frozen,
        },
    }
    context["verified_node_grant"] = {
        "graph_id": context["graph_id"],
        "graph_revision": context["graph_revision"],
        "node_id": context["node_id"],
        "grant_ref": context["node_grant_ref"],
    }
    return context


def test_scheduler_freezes_scene_from_server_owned_inputs_only() -> None:
    authority = _RuntimeContextAuthority(_frozen_scene())
    occurrence = SchedulerOccurrence.from_mapping(
        {
            "occurrence_id": "scheduleroccurrence://sha256/" + "a" * 64,
            "task_id": "proactive_talk_periodic",
            "definition_revision": 1,
            "source": "manual",
            "request_id": "scheduler-request-ne03",
            "scheduled_at_ms": NOW,
            "payload": {
                "scene_awareness": {"scene_tags": ["meeting"]},
                "scene_id": "scene.attacker-selected",
            },
            "principal_id": "scheduler-clock",
            "claim_ref": "secure-ingress-claim://scheduler/ne03",
            "audit_ref": "audit://secure-ingress/scheduler/ne03",
            "body_sha256": "4" * 64,
        }
    )
    definition = SchedulerDefinitionSnapshot.from_mapping(
        {
            "task_id": "proactive_talk_periodic",
            "definition_revision": 1,
            "head_version": 1,
            "enabled": True,
            "definition": {
                "trigger_mode": "interval",
                "trigger_config": {"seconds": 180},
                "intent_template": {
                    "skill_name": "proactive_talk",
                    "args": {
                        "user_id": "person-ne03",
                        "scene_id": "scene.definition-selected",
                    },
                },
                "worker_key": "scheduler_production",
                "priority": "LOW",
                "mode_allowlist": ["normal", "sleep"],
                "resource_profile": {},
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
            },
        }
    )

    frozen = GovernedSchedulerRuntime._freeze_runtime_context(
        SimpleNamespace(_runtime_context_application=authority),
        occurrence=occurrence,
        definition=definition,
        authority={
            "principal_id": "scheduler-clock",
            "tenant_id": "workspace-ne03",
        },
        received_at_ms=NOW,
    )

    assert frozen["scene_identity_v2"] == _frozen_scene()["scene_identity_v2"]
    call = authority.freeze_calls[0]
    assert call["request_body"] is None
    assert call["intent_draft"]["task_kind"] == "PROACTIVE"
    assert "scene_id" not in call["intent_draft"]
    assert call["command_authority"]["tenant_id"] == "workspace-ne03"


def test_proactive_dispatch_uses_exact_frozen_scene_and_ignores_caller_scene(
    monkeypatch,
) -> None:
    frozen = _frozen_scene()
    authority = _RuntimeContextAuthority(frozen)
    monkeypatch.setattr(proactive_dispatch, "_load_recent_topics", lambda **_: [])
    monkeypatch.setattr(proactive_dispatch, "_load_recent_diary_snippet", lambda: "")
    monkeypatch.setattr(proactive_dispatch, "_load_recent_dream_snippet", lambda **_: "")
    ports = SimpleNamespace(
        get_user_emotion_profile=lambda: SimpleNamespace(to_summary=lambda: {}),
        emotion_timeline=SimpleNamespace(
            rebuild_summary=lambda **_: {},
            build_daily_diary=lambda **_: {},
        ),
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "scheduler-request-ne03",
            "args": {
                "idle_threshold_ms": 0,
                "scene_awareness": {"scene_tags": ["meeting"]},
            },
            "scene_awareness": {"scene_tags": ["focus_work"]},
        },
        ports=ports,
        logger=SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        get_ledger=lambda: object(),
        retention_policy=None,
        ui_pusher=lambda _message: {"status": "OK"},
        llm_generator_override=lambda _payload: {"greeting": "我来陪你一会儿。"},
        last_interaction_ts_override=0,
        motive_state_override={},
        render_policy_override={},
        execution_context=_execution_context(frozen),
        runtime_context_application=authority,
    )

    assert result["status"] == "EXECUTED"
    assert result["scene_identity_v2"] == frozen["scene_identity_v2"]
    assert result["scene_awareness"]["source"] == "SceneDecisionV2"
    assert result["scene_awareness"]["scene_tags"] == ["casual_chat"]
    assert result["delivery_intent"]["payload"]["scene_tags"] == ["casual_chat"]


def test_proactive_dispatch_omits_retired_legacy_timeline_from_context(
    monkeypatch,
) -> None:
    frozen = _frozen_scene()
    authority = _RuntimeContextAuthority(frozen)
    captured: dict[str, object] = {}
    monkeypatch.setattr(proactive_dispatch, "_load_recent_topics", lambda **_: [])
    monkeypatch.setattr(proactive_dispatch, "_load_recent_diary_snippet", lambda: "")
    monkeypatch.setattr(proactive_dispatch, "_load_recent_dream_snippet", lambda **_: "")

    def execute(_args, context):
        captured["context"] = dict(context or {})
        return {"status": "EXECUTED"}

    monkeypatch.setattr(
        "host_brain.skills.impl.proactive_talk.execute_proactive_talk",
        execute,
    )
    ports = SimpleNamespace(
        get_user_emotion_profile=lambda: SimpleNamespace(to_summary=lambda: {}),
        emotion_timeline=RetiredEmotionTimelineProjection(),
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "scheduler-retired-timeline",
            "args": {"idle_threshold_ms": 0},
        },
        ports=ports,
        logger=SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
        get_ledger=lambda: object(),
        retention_policy=None,
        ui_pusher=lambda _message: {"status": "OK"},
        llm_generator_override=lambda _payload: {"greeting": "我来陪你一会儿。"},
        last_interaction_ts_override=0,
        motive_state_override={},
        render_policy_override={},
        execution_context=_execution_context(frozen),
        runtime_context_application=authority,
    )

    assert result["status"] == "EXECUTED"
    emotion_context = dict(captured["context"]["emotion_context"])
    assert "emotion_timeline_summary" not in emotion_context
    assert "emotion_diary" not in emotion_context


def test_proactive_dispatch_fails_closed_before_generation_without_frozen_scene() -> None:
    llm_calls: list[dict] = []
    result = run_proactive_intent_dispatch(
        {"skill_name": "proactive_talk", "args": {}},
        ports=SimpleNamespace(),
        logger=SimpleNamespace(),
        get_ledger=lambda: object(),
        retention_policy=None,
        ui_pusher=lambda _message: {},
        llm_generator_override=lambda payload: llm_calls.append(payload) or {},
        execution_context={},
        runtime_context_application=None,
    )

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "PROACTIVE_NODE_AUTHORITY_REQUIRED"
    assert llm_calls == []


def test_proactive_scene_identity_tamper_fails_closed() -> None:
    frozen = _frozen_scene()
    authority = _RuntimeContextAuthority(frozen)
    execution = _execution_context(frozen)
    execution["frozen_input_pack"]["scene_identity_v2"] = {
        **frozen["scene_identity_v2"],
        "decision_digest": "0" * 64,
    }

    try:
        _require_proactive_runtime_context(
            execution_context=execution,
            runtime_context_application=authority,
        )
    except Exception as exc:
        assert getattr(exc, "reason_code", "") == "PROACTIVE_SCENE_CONTEXT_INVALID"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("tampered Scene identity must be rejected")


def test_ans_inner_world_observation_cannot_execute_proactive_voice(monkeypatch) -> None:
    agent = ANSAgent(state_registry=SimpleNamespace())
    calls: list[dict] = []
    monkeypatch.setattr(
        ANSAgent,
        "trigger_proactive_voice",
        lambda self, **kwargs: calls.append(kwargs) or {"triggered": True},
    )

    agent.emit_inner_world_ticked(
        tick_result={"loneliness": 0.9, "proactive_voice_eligible": True}
    )

    assert calls == []


def test_retired_ans_direct_route_is_explicitly_blocked() -> None:
    result = ANSAgent(state_registry=SimpleNamespace()).trigger_proactive_voice(
        user_id="person-ne03",
        context=_frozen_scene(),
    )

    assert result["triggered"] is False
    assert result["reason_code"] == "PROACTIVE_GOVERNED_SCHEDULER_REQUIRED"
