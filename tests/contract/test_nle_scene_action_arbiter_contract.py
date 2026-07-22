from __future__ import annotations

import time
from types import SimpleNamespace

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.journal import SQLiteCapabilityExecutionJournal
from host_brain.capability_plane.production_composition import (
    build_production_capability_runtime,
)
from host_brain.governor.action_arbiter import (
    ACTION_ARBITER_SCHEMA_VERSION,
    ARBITER_ALLOW,
    ARBITER_ALLOW_WITH_RESTRICTIONS,
    ARBITER_BLOCK,
    ActionArbiter as _ActionArbiter,
    arbitrate_action as _arbitrate_action,
    build_governed_action_binding,
)
from host_brain.governor.action_preview import (
    ACTION_ARBITER_EVENT_TYPE,
    ACTION_PREVIEW_SCHEMA_VERSION,
    preview_action,
)
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.surface_runtime.eligibility import _surface_scene_guardrails
from host_brain.executive_runtime.skill_execution import (
    _execute_skill as _execute_skill_impl,
)
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.persona.proactive_voice import (
    SCENE_MORNING_GREET,
    SCENE_TOPIC_FOLLOWUP,
    PersonaProactiveVoice,
)
from host_brain.persona.scene_policy import (
    SCENE_DECISION_SCHEMA_VERSION,
    SCENE_PROACTIVE_BLOCK,
    ScenePolicy,
    build_scene_decision,
    evaluate_proactive_scene,
)
from host_brain.runtime.context_contracts import ContextConstraintV1, SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2
from host_brain.runtime.degradation_authority import (
    LegacyModuleGlobalDegradationAdapter,
)


_LEGACY_DEGRADATION_PORT = LegacyModuleGlobalDegradationAdapter()


def arbitrate_action(**kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault(
        "degradation_capability_port",
        _LEGACY_DEGRADATION_PORT,
    )
    return _arbitrate_action(**kwargs)


class ActionArbiter(_ActionArbiter):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(
            enabled=enabled,
            degradation_capability_port=_LEGACY_DEGRADATION_PORT,
        )


def _execute_skill(**kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault(
        "degradation_capability_port",
        _LEGACY_DEGRADATION_PORT,
    )
    return _execute_skill_impl(**kwargs)


def _skill_execution_test_ports(
    *,
    registry,
    ledger,
    token_service,
    capability_broker,
):  # type: ignore[no-untyped-def]
    """Inject the low-level worker collaborators without reviving app wiring."""

    return SimpleNamespace(
        get_skill_registry=lambda: registry,
        get_ledger=lambda: ledger,
        get_token_service=lambda: token_service,
        get_resource_manager=lambda *args, **kwargs: None,
        get_episodic_store=lambda: None,
        get_semantic_recaller=lambda: None,
        get_capability_broker=lambda: capability_broker,
    )


NOW = 1_800_100_000_000


def _scene_v2(
    *,
    scene_id: str = "scene.casual-chat",
    constraints: tuple[ContextConstraintV1, ...] = (),
) -> tuple[dict, dict]:
    decision = SceneDecisionV2(
        workspace_id="workspace_scene_contract",
        subject_id="person_scene_contract",
        scene_id=scene_id,
        revision=1,
        world_snapshot_digest="a" * 64,
        context_digest="b" * 64,
        guardian_snapshot_digest="c" * 64,
        constraints=constraints,
    )
    return decision.to_dict(), SceneIdentityV2.from_decision(decision).to_dict()


def _intent(**overrides) -> dict:
    value = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent_scene_1",
        "ts_ms": NOW,
        "session_id": "session_scene_1",
        "seq": 1,
        "type": "skill_call",
        "action": "work_code",
        "target": "host",
        "args": {"text": "implement the approved task"},
        "nonce": "nonce_scene_1",
    }
    value.update(overrides)
    return value


def _approved(
    *,
    intent: dict | None = None,
    token_ref: str = "approval::intent_scene_1",
    route_token: str = "GOV_OK_RENDER",
    risk_level: int = 3,
) -> dict:
    envelope = dict(intent or _intent())
    binding = build_governed_action_binding(
        intent_envelope=envelope,
        approval_token_ref=token_ref,
        risk_level=risk_level,
        issued_at_ms=NOW,
        expires_at_ms=NOW + 120_000,
    )
    return {
        "decision": "APPROVE",
        "reason_code": None,
        "intent_id": envelope["id"],
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "committed_route_token_ref": route_token,
        "committed_route_token": {"token": route_token},
    }


def _action(*, intent: dict | None = None, **overrides) -> dict:
    envelope = dict(intent or _intent())
    value = {
        "action_kind": envelope["action"],
        "scope": envelope["target"],
        "risk_level": 3,
        "requires_token": True,
        "is_proactive": False,
        # This field is proposal data and deliberately is not trusted by the arbiter.
        "explicit_user_intent": True,
    }
    value.update(overrides)
    return value


def _user_intent(intent: dict | None = None, *, explicit: bool = True) -> dict:
    envelope = dict(intent or _intent())
    return {
        "explicit": explicit,
        "intent_id": envelope["id"],
        "action_kind": envelope["action"],
        "goal_ref": f"intent://{envelope['id']}",
    }


def test_t_nle_17_scene_decision_preserves_source_ttl_and_restrict_only_contract() -> None:
    decision = build_scene_decision(
        scene_awareness={
            "scene_tags": ["focus", "gaming"],
            "source_ref": "environment://desktop/scene-1",
            "observed_at_ms": NOW - 60_000,
            "expires_at_ms": NOW - 30_000,
            "confidence": 0.72,
        },
        task_mode="work_code",
        sys_mode="focus",
        user_goal_ref="intent://intent_scene_1",
        now_ms=NOW,
        ttl_ms=30_000,
    )
    payload = decision.as_dict(now_ms=NOW)

    assert payload["schema_version"] == SCENE_DECISION_SCHEMA_VERSION
    assert payload["observed_at_ms"] == NOW - 60_000
    assert payload["expires_at_ms"] == NOW - 30_000
    assert payload["expired"] is True
    assert payload["confidence"] == 0.72
    assert payload["source_refs"] == ("environment://desktop/scene-1",)
    assert payload["conflict_state"] == "CONFLICT"
    assert payload["proactive_policy"] == SCENE_PROACTIVE_BLOCK
    assert payload["constraints"]["can_change_user_goal"] is False
    assert payload["constraints"]["can_expand_scope"] is False
    assert payload["constraints"]["can_reduce_risk"] is False
    assert payload["permission_effect"] == "RESTRICT_ONLY"
    assert payload["user_goal_preserved"] is True
    assert evaluate_proactive_scene(payload, proactive_scene_type=SCENE_MORNING_GREET, now_ms=NOW)[0] is True


def test_t_nle_17_proactive_voice_requires_exact_frozen_scene_v2() -> None:
    voice = PersonaProactiveVoice()
    blocked_scene, blocked_identity = _scene_v2(
        scene_id="scene.sleep",
        constraints=(
            ContextConstraintV1(
                code="PROACTIVE_ACTION_BLOCKED",
                disposition="BLOCK",
                origin="GUARDIAN",
                source_ref="guardian://snapshot/sha256/" + "d" * 64,
                reason="sleep blocks proactive",
            ),
        ),
    )
    gaming_scene, gaming_identity = _scene_v2(
        scene_id="scene.game-low-interruption",
        constraints=(
            ContextConstraintV1(
                code="LOW_INTERRUPTION",
                disposition="RESTRICT",
                origin="SCENE",
                source_ref="policy://scene/v1/game-low-interruption",
                reason="gaming is low interruption",
            ),
        ),
    )
    blocked, blocked_reason = voice._scene_allows_scene(
        SCENE_MORNING_GREET,
        {
            "scene_decision_v2": blocked_scene,
            "scene_identity_v2": blocked_identity,
        },
    )
    missing, missing_reason = voice._scene_allows_scene(
        SCENE_MORNING_GREET,
        {},
    )
    gaming_generic, gaming_reason = voice._scene_allows_scene(
        SCENE_MORNING_GREET,
        {
            "scene_decision_v2": gaming_scene,
            "scene_identity_v2": gaming_identity,
        },
    )
    gaming_followup, _ = voice._scene_allows_scene(
        SCENE_TOPIC_FOLLOWUP,
        {
            "scene_decision_v2": gaming_scene,
            "scene_identity_v2": gaming_identity,
        },
    )

    assert blocked is False and "PROACTIVE_ACTION_BLOCKED" in blocked_reason
    assert missing is False and "SCENE_DECISION_V2_REQUIRED" in missing_reason
    assert gaming_generic is False and "low-interruption" in gaming_reason
    assert gaming_followup is True


def test_t_nle_17_scene_decision_projects_into_surface_expression_without_changing_task() -> None:
    task_control = {"task_mode": "technical_help", "response_move": "answer"}
    scene_v2, scene_identity = _scene_v2(
        scene_id="scene.release-maintenance",
        constraints=(
            ContextConstraintV1(
                code="MAINTENANCE_EFFECT_REVIEW",
                disposition="REVIEW",
                origin="SCENE",
                source_ref="policy://scene/v1/release-maintenance",
                reason="maintenance requires review",
            ),
        ),
    )
    scene, guardrails = _surface_scene_guardrails(
        scene_decision_v2=scene_v2,
        scene_identity_v2=scene_identity,
        user_goal_ref="intent://intent_scene_1",
    )

    assert scene["proactive_policy"] == "BLOCK"
    assert scene["user_goal_preserved"] is True
    assert scene["validation_status"] == "VERIFIED"
    assert any("不得自行扩大行动" in item for item in guardrails)
    assert task_control == {"task_mode": "technical_help", "response_move": "answer"}


def test_t_nle_17_real_governor_approve_emits_intent_token_scope_and_risk_binding() -> None:
    token_service = TokenService(approval_ttl_ms=120_000)
    engine = IntentDecisionEngine(token_service=token_service)
    envelope = _intent()
    governor = engine.evaluate_intent(envelope, current_ms=NOW)

    assert governor["decision"] == "APPROVE"
    assert governor["committed_route_token_ref"] == "GOV_OK_RENDER"
    assert governor["approved_action_binding"]["intent_id"] == envelope["id"]
    assert governor["approved_action_binding"]["scope"] == envelope["target"]
    result = arbitrate_action(
        governor_decision=governor,
        proposed_action=_action(intent=envelope, risk_level=1),
        intent_envelope=envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )
    assert result["decision"] == ARBITER_ALLOW
    assert result["authority_binding_valid"] is True
    assert result["effective_risk_level"] == 5


def test_t_nle_17_governor_rejection_and_airlock_can_never_be_relaxed() -> None:
    rejected = arbitrate_action(
        governor_decision={
            "decision": "REJECT",
            "reason_code": "SAFETY_BLOCKED",
            "committed_route_token_ref": "GOV_FAIL_FALLBACK",
            "committed_route_token": {"token": "GOV_FAIL_FALLBACK"},
        },
        proposed_action=_action(risk_level=1),
        intent_envelope=_intent(),
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(),
        now_ms=NOW,
    )
    airlock_governor = _approved(route_token="GOV_AIRLOCK_REVIEW", risk_level=5)
    airlock = arbitrate_action(
        governor_decision=airlock_governor,
        proposed_action=_action(risk_level=1),
        intent_envelope=_intent(),
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(),
        now_ms=NOW,
    )

    assert rejected["schema_version"] == ACTION_ARBITER_SCHEMA_VERSION
    assert rejected["decision"] == ARBITER_BLOCK
    assert rejected["may_relax_governor"] is False
    assert airlock["decision"] == ARBITER_BLOCK
    assert airlock["execution_state"] == "PAUSED_REVIEW"
    assert "ARBITER_AIRLOCK_REVIEW_REQUIRED" in airlock["reason_codes"]


def test_t_nle_17_explicit_user_action_preserves_bound_goal_and_only_restricts_expression() -> None:
    envelope = _intent()
    scene = build_scene_decision(
        scene_awareness={"scene_tags": ["focus_work"]},
        task_mode="work_code",
        user_goal_ref="intent://intent_scene_1",
        now_ms=NOW,
    )
    result = ActionArbiter().decide(
        governor_decision=_approved(intent=envelope),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=scene,
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )

    assert result["decision"] == ARBITER_ALLOW_WITH_RESTRICTIONS
    assert result["effective_action_kind"] == "work_code"
    assert result["effective_scope"] == "host"
    assert result["effective_risk_level"] == 3
    assert result["token_present"] is True
    assert result["user_goal_preserved"] is True
    assert "ARBITER_EXPLICIT_USER_GOAL_PRESERVED" in result["reason_codes"]
    assert any(item.startswith("expression_mode:") for item in result["restrictions"])


def test_t_nle_17_untrusted_action_cannot_forge_user_intent_or_bypass_token() -> None:
    envelope = _intent()
    scene = ScenePolicy().resolve(scene_awareness={"scene_tags": ["meeting"]}, now_ms=NOW)
    forged_explicit = arbitrate_action(
        governor_decision=_approved(intent=envelope),
        proposed_action=_action(
            intent=envelope,
            action_kind="work_code",
            is_proactive=True,
            explicit_user_intent=True,
        ),
        intent_envelope=envelope,
        scene_decision=scene,
        user_intent=_user_intent(envelope, explicit=False),
        now_ms=NOW,
    )
    no_token_governor = _approved(intent=envelope)
    no_token_governor["tokens"] = {}
    no_token = arbitrate_action(
        governor_decision=no_token_governor,
        proposed_action=_action(intent=envelope, requires_token=False),
        intent_envelope=envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )

    assert forged_explicit["decision"] == ARBITER_BLOCK
    assert forged_explicit["explicit_user_goal"] is False
    assert no_token["decision"] == ARBITER_BLOCK
    assert "ARBITER_TOKEN_REQUIRED" in no_token["reason_codes"]
    assert no_token["may_bypass_token"] is False


def test_t_nle_17_cross_intent_action_scope_and_risk_reuse_fail_closed() -> None:
    approved_envelope = _intent()
    other_envelope = _intent(id="intent_other", nonce="nonce_other", args={"text": "other"})
    governor = _approved(intent=approved_envelope, risk_level=5)
    cross_intent = arbitrate_action(
        governor_decision=governor,
        proposed_action=_action(intent=other_envelope),
        intent_envelope=other_envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(other_envelope),
        now_ms=NOW,
    )
    wrong_action = arbitrate_action(
        governor_decision=governor,
        proposed_action=_action(intent=approved_envelope, action_kind="work_translate"),
        intent_envelope=approved_envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(approved_envelope),
        now_ms=NOW,
    )
    wrong_scope = arbitrate_action(
        governor_decision=governor,
        proposed_action=_action(intent=approved_envelope, scope="memory"),
        intent_envelope=approved_envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(approved_envelope),
        now_ms=NOW,
    )
    lowered_risk = arbitrate_action(
        governor_decision=governor,
        proposed_action=_action(intent=approved_envelope, risk_level=1),
        intent_envelope=approved_envelope,
        scene_decision=build_scene_decision(now_ms=NOW),
        user_intent=_user_intent(approved_envelope),
        now_ms=NOW,
    )

    assert cross_intent["decision"] == ARBITER_BLOCK
    assert "ARBITER_AUTHORITY_BINDING_INVALID" in cross_intent["reason_codes"]
    assert wrong_action["decision"] == ARBITER_BLOCK
    assert "ARBITER_ACTION_MISMATCH" in wrong_action["reason_codes"]
    assert wrong_scope["decision"] == ARBITER_BLOCK
    assert "ARBITER_SCOPE_MISMATCH" in wrong_scope["reason_codes"]
    assert lowered_risk["decision"] == ARBITER_ALLOW
    assert lowered_risk["effective_risk_level"] == 5


def test_t_nle_17_real_skill_execution_consumes_bound_token_only_after_arbiter(
    monkeypatch,
    tmp_path,
) -> None:
    registry = build_builtin_skill_registry()
    meta = registry.require("session_summary")
    token_service = TokenService(approval_ttl_ms=120_000)
    now = int(time.time() * 1000)
    envelope = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent_exec_bound_1",
        "ts_ms": now,
        "session_id": "session_exec_bound_1",
        "seq": 7,
        "type": "skill_call",
        "action": "session_summary",
        "target": meta.scope,
        "args": {"user_message": "summarize the approved session"},
        "nonce": "nonce_exec_bound_1",
        "person_id": "person_exec_bound_1",
        "principal_id": "person_exec_bound_1",
    }
    token_ref = token_service.issue_approval_token(
        intent_id=envelope["id"],
        skill_name=envelope["action"],
        session_id=envelope["session_id"],
        seq=envelope["seq"],
        now_ts_ms=now,
    )
    governor = _approved(
        intent=envelope,
        token_ref=token_ref,
        risk_level=meta.risk_level,
    )
    # _approved uses the deterministic contract clock; replace with a live binding.
    governor["approved_action_binding"] = build_governed_action_binding(
        intent_envelope=envelope,
        approval_token_ref=token_ref,
        risk_level=meta.risk_level,
        issued_at_ms=now,
        expires_at_ms=now + 120_000,
    )
    monkeypatch.setenv("HOST_ACTION_ARBITER_ENABLED", "true")
    monkeypatch.setattr("host_brain.composition.process_services.get_skill_registry", lambda: registry)
    monkeypatch.setattr("host_brain.composition.process_services.get_token_service", lambda: token_service)
    ledger = SQLiteLedger(tmp_path / "nle-scene-skill.sqlite3")
    capability_broker = build_production_capability_runtime(
        execution_journal=SQLiteCapabilityExecutionJournal(ledger),
    ).broker
    governor.update(
        {
            "request_id": "req_exec_bound_1",
            "stage": "S_INTENT_OK",
            "ts_ms": now,
        }
    )
    ledger.log_governor(governor)

    kwargs = {
        "ports": _skill_execution_test_ports(
            registry=registry,
            ledger=ledger,
            token_service=token_service,
            capability_broker=capability_broker,
        ),
        "request_body": {},
        "request_id": "req_exec_bound_1",
        "session_id": envelope["session_id"],
        "person_id": "person_exec_bound_1",
        "intent_id": envelope["id"],
        "skill_id": envelope["action"],
        "task_text": "summarize the approved session",
        "user_message": "summarize the approved session",
        "governor_result": governor,
        "intent_draft": envelope,
        "emotion_context": {"mood_state": "CALM", "emotion_tag": "FOCUSED"},
        "render_target_lang": "zh-CN",
        "ledger": ledger,
        "durable_authority_ledger": ledger,
        "capability_broker_override": capability_broker,
    }
    missing_scene = _execute_skill(**kwargs)
    frozen_scene, frozen_scene_identity = _scene_v2()
    envelope["scene_decision_v2"] = frozen_scene
    envelope["scene_identity_v2"] = frozen_scene_identity
    first = _execute_skill(**kwargs)
    replay = _execute_skill(**kwargs)

    assert missing_scene.success is False
    assert missing_scene.error_text == "SCENE_DECISION_V2_REQUIRED"
    assert first.success is True, (first.error_text, first.payload)
    assert first.payload["approval_token_ref"] == token_ref
    assert replay.success is False
    assert replay.error_text == "TOKEN_INVALID"
    ledger.close()


class _EventStore:
    def __init__(self) -> None:
        self.events = []

    def append_event(self, event):
        event.validate()
        self.events.append(event)
        return {"cursor": len(self.events), "event_id": event.event_id}


def test_t_nle_17_action_preview_is_dry_run_and_never_marks_airlock_executable() -> None:
    envelope = _intent()
    store = _EventStore()
    preview = preview_action(
        governor_decision=_approved(intent=envelope),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=build_scene_decision(task_mode="work_code", now_ms=NOW),
        user_intent=_user_intent(envelope),
        evidence_refs=["intent://intent_scene_1", "governor://decision/approved-1"],
        now_ms=NOW,
        runtime_event_store=store,
        record_runtime_event=True,
    )
    airlock = preview_action(
        governor_decision=_approved(intent=envelope, route_token="GOV_AIRLOCK_REVIEW"),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )

    assert preview["schema_version"] == ACTION_PREVIEW_SCHEMA_VERSION
    assert preview["status"] == "DRY_RUN"
    assert preview["execution_performed"] is False
    assert preview["external_side_effects"] is False
    assert preview["runtime_event"]["event_type"] == ACTION_ARBITER_EVENT_TYPE
    assert preview["runtime_event"]["recorded"] is True
    assert len(store.events) == 1
    assert store.events[0].payload["side_effects_executed"] is False
    assert "raw_text" not in store.events[0].payload
    assert airlock["would_execute"] is False


def test_t_nle_17_expired_scene_never_grants_more_than_bound_governor() -> None:
    envelope = _intent()
    scene = build_scene_decision(
        scene_awareness={"scene_tags": ["meeting"], "observed_at_ms": NOW, "expires_at_ms": NOW + 1},
        now_ms=NOW,
    )
    approved = arbitrate_action(
        governor_decision=_approved(intent=envelope),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=scene.as_dict(now_ms=NOW + 2),
        user_intent=_user_intent(envelope),
        now_ms=NOW + 2,
    )
    rejected = arbitrate_action(
        governor_decision={
            "decision": "REJECT",
            "reason_code": "SCOPE_DENY",
            "committed_route_token_ref": "GOV_FAIL_FALLBACK",
            "committed_route_token": {"token": "GOV_FAIL_FALLBACK"},
        },
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=scene.as_dict(now_ms=NOW + 2),
        user_intent=_user_intent(envelope),
        now_ms=NOW + 2,
    )

    assert approved["decision"] == ARBITER_ALLOW
    assert approved["scene_expired"] is True
    assert rejected["decision"] == ARBITER_BLOCK


def test_t_nle_17_disabled_soft_policy_falls_back_to_committed_governor_route() -> None:
    envelope = _intent()
    disabled_scene = build_scene_decision(
        scene_awareness={"scene_tags": ["meeting"]},
        now_ms=NOW,
        enabled=False,
    )
    approved = ActionArbiter(enabled=False).decide(
        governor_decision=_approved(intent=envelope),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=disabled_scene,
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )
    airlock = ActionArbiter(enabled=False).decide(
        governor_decision=_approved(intent=envelope, route_token="GOV_AIRLOCK_REVIEW"),
        proposed_action=_action(intent=envelope),
        intent_envelope=envelope,
        scene_decision=disabled_scene,
        user_intent=_user_intent(envelope),
        now_ms=NOW,
    )

    assert disabled_scene.proactive_policy == "ALLOW"
    assert approved["decision"] == ARBITER_ALLOW
    assert "ACTION_ARBITER_DISABLED_GOVERNOR_PRESERVED" in approved["reason_codes"]
    assert airlock["decision"] == ARBITER_BLOCK
