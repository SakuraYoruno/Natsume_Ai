from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from host_brain.app import obs_routes_life_evolution as routes
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.settings.core import get_core_settings
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    set_global_degradation_state_machine,
)
from host_brain.persona.life_evolution_view import (
    LIFE_EVOLUTION_VIEW_SCHEMA_VERSION,
    build_life_evolution_view,
)


NOW = 1_800_200_000_000


def _request_with_route_ports(ports: ObsRoutePorts) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(obs_route_ports=ports))
    return Request({"type": "http", "app": app})


@pytest.fixture(autouse=True)
def _isolated_degradation_machine(monkeypatch: pytest.MonkeyPatch):
    """Keep T-NLE-22 independent from a developer/runtime audit database.

    Action preview must consume the real degradation gate, but this contract
    verifies Life projection semantics rather than migration state.  A
    process-global durable machine would otherwise make the result depend on
    whichever ignored audit.sqlite3 happens to exist in the worktree.
    """
    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "true")
    set_global_degradation_state_machine(DegradationStateMachine())
    try:
        yield
    finally:
        set_global_degradation_state_machine(None)


def _world() -> dict:
    return {
        "schema_version": "Natsume.world_projection.v1",
        "status": "DEGRADED",
        "generated_at_ms": NOW,
        "facts": [
            {
                "key": "environment.day_phase",
                "value": "morning",
                "source": "environment_snapshot",
                "source_ref": "environment://snapshot/1",
                "confidence": 0.98,
                "observed_at_ms": NOW - 1_000,
                "expires_at_ms": NOW + 59_000,
                "epistemic_type": "observed",
                "conflict_state": "NONE",
                "reason_codes": [],
            },
            {
                "key": "state_arbiter.user.preference",
                "value": "quiet",
                "source": "state_arbiter",
                "source_ref": "state-arbiter://user.preference",
                "confidence": 0.42,
                "observed_at_ms": NOW - 90_000,
                "expires_at_ms": NOW - 1,
                "epistemic_type": "inferred",
                "conflict_state": "CONFLICT",
                "conflict_detail": "WORLD_CANON != EPISODIC_STORE",
                "reason_codes": ["STATE_ARBITER_CONFLICT"],
            },
        ],
    }


def _appraisal() -> dict:
    return {
        "schema_version": "Natsume.appraisal.record.v1",
        "appraisal_id": "appraisal-1",
        "source_event_kind": "emotional_proactive_episode",
        "source_event_ref": "emotional_proactive_episode://episode-1",
        "source_event_digest": "sha256:source",
        "parent_event_refs": ["experience://1"],
        "ts_ms": NOW - 2_000,
        "appraisal": {
            "kind": "social_feedback",
            "outcome": "boundary",
            "reply_mode": "restrained",
            "valence": -0.2,
            "arousal": 0.4,
            "relevance": 0.9,
            "confidence": 0.76,
            "epistemic_type": "inferred",
            "opening_message": "must not escape",
        },
        "bond_signal": {
            "direction": "respect_boundary",
            "strength": 0.8,
            "permission_effect": "NONE",
            "requires_governed_command": True,
        },
        "evidence_refs": ["turn://1"],
        "reason_tags": ["explicit_boundary"],
        "message_text": "private proactive copy",
        "memory_body": "private memory copy",
    }


def _bond() -> dict:
    return {
        "schema_version": "Natsume.bond.projection.v1",
        "projection_id": "bond-projection-1",
        "status": "OK",
        "projected_at_ms": NOW,
        "compatibility": {
            "current_stage": "FRIEND",
            "total_score": 12,
            "trend": "stable",
            "event_count": 4,
            "last_event_ts_ms": NOW - 4_000,
            "epistemic_type": "compatibility_projection",
        },
        "relationship": {
            "vector": {"trust": 0.4},
            "texture": {"style_hint": "natural"},
            "rift": {"active": False},
            "repair": {"repair_progress": 0.0},
            "boundary": {"status": "RESPECT_REQUIRED"},
        },
        "evidence_refs": ["bond-event://1"],
    }


def _scene() -> dict:
    return {
        "schema_version": "Natsume.scene.decision.v1",
        "decision_id": "scene-1",
        "scene_type": "focus",
        "scene_tags": ["meeting"],
        "observed_at_ms": NOW - 90_000,
        "expires_at_ms": NOW - 1,
        "expired": True,
        "confidence": 0.73,
        "epistemic_type": "derived",
        "conflict_state": "CONFLICT",
        "source_refs": ["environment://snapshot/1"],
        "proactive_policy": "BLOCK",
        "expression_mode": "restrained",
        "reason_codes": ["SCENE_MEETING"],
        "user_goal_preserved": True,
    }


def _view() -> dict:
    return build_life_evolution_view(
        person_id="default",
        world_snapshot=_world(),
        appraisal_records=[_appraisal()],
        bond_projection=_bond(),
        scene_decision=_scene(),
        now_ms=NOW,
    )


def test_t_nle_22_combined_read_model_keeps_fact_inference_source_ttl_conflict_and_expiry_visible() -> None:
    view = _view()
    assert view["schema_version"] == LIFE_EVOLUTION_VIEW_SCHEMA_VERSION
    assert view["read_only"] is True
    assert view["write_api_available"] is False
    facts = {item["key"]: item for item in view["world"]["facts"]}
    observed = facts["environment.day_phase"]
    inferred = facts["state_arbiter.user.preference"]
    assert observed["fact_class"] == "FACT"
    assert observed["source"] == "environment_snapshot"
    assert observed["confidence"] == 0.98
    assert observed["ttl_remaining_ms"] == 59_000
    assert inferred["fact_class"] == "INFERENCE"
    assert inferred["expired"] is True
    assert inferred["conflicted"] is True
    assert inferred["display_state"] == "EXPIRED"
    assert inferred["conflict_state"] == "CONFLICT"
    assert view["world"]["conflict_count"] == 1
    assert view["world"]["expired_count"] == 1


def test_t_nle_22_appraisal_is_evidence_only_and_bond_never_becomes_permission_authority() -> None:
    view = _view()
    encoded = json.dumps(view, ensure_ascii=False)
    appraisal = view["appraisals"]["records"][0]
    bond = view["bond"]
    assert appraisal["source_event_ref"] == "emotional_proactive_episode://episode-1"
    assert appraisal["evidence_refs"] == ["turn://1"]
    assert appraisal["bond_signal"]["permission_effect"] == "NONE"
    assert view["appraisals"]["private_body_included"] is False
    assert "must not escape" not in encoded
    assert "private proactive copy" not in encoded
    assert "private memory copy" not in encoded
    assert bond["authority_label"] == "PROJECTION_ONLY"
    assert bond["authoritative"] is False
    assert bond["permission_effect"] == "NONE"
    assert bond["write_api_available"] is False


def test_t_nle_22_malformed_refs_cannot_stringify_nested_private_bodies() -> None:
    appraisal = _appraisal()
    appraisal["source_event_ref"] = {"memory_body": "LEAK_A"}
    appraisal["source_event_digest"] = {"message_text": "LEAK_B"}
    appraisal["evidence_refs"] = [
        "turn://safe",
        {"opening_message": "LEAK_C"},
        ["LEAK_D"],
    ]
    view = build_life_evolution_view(
        person_id="default",
        world_snapshot=_world(),
        appraisal_records=[appraisal],
        bond_projection=_bond(),
        scene_decision=_scene(),
        now_ms=NOW,
    )
    encoded = json.dumps(view, ensure_ascii=False)
    record = view["appraisals"]["records"][0]
    assert record["source_event_ref"] == ""
    assert record["source_event_digest"] == ""
    assert record["evidence_refs"] == ["turn://safe"]
    assert view["appraisals"]["sanitizer_enforced"] is True
    assert view["appraisals"]["redacted_private_field_count"] >= 3
    for marker in ("LEAK_A", "LEAK_B", "LEAK_C", "LEAK_D"):
        assert marker not in encoded


def test_t_nle_22_state_arbiter_value_and_conflict_body_are_redacted() -> None:
    world = _world()
    world["facts"][1]["value"] = "PRIVATE_CANON_VALUE"
    world["facts"][1]["conflict_detail"] = "'PRIVATE_CANON_VALUE' vs 'PRIVATE_MEMORY_VALUE'"
    view = build_life_evolution_view(
        person_id="default",
        world_snapshot=world,
        bond_projection=_bond(),
        scene_decision=_scene(),
        now_ms=NOW,
    )
    encoded = json.dumps(view, ensure_ascii=False)
    fact = next(item for item in view["world"]["facts"] if item["source"] == "state_arbiter")
    assert fact["value"] == "[REDACTED_ADJUDICATED_VALUE]"
    assert fact["conflict_detail"] == "CONFLICT_DETAIL_REDACTED"
    assert fact["conflict_detail_redacted"] is True
    assert "PRIVATE_CANON_VALUE" not in encoded
    assert "PRIVATE_MEMORY_VALUE" not in encoded


def test_t_nle_22_expired_scene_keeps_recorded_policy_source_and_effective_policy_distinct() -> None:
    view = _view()
    scene = view["scene"]
    preview = view["action_preview"]
    assert scene["expired"] is True
    assert scene["conflicted"] is True
    assert scene["recorded_proactive_policy"] == "BLOCK"
    assert scene["proactive_policy"] == "ALLOW"
    assert scene["source_refs"] == ["environment://snapshot/1"]
    assert scene["confidence"] == 0.73
    assert scene["permission_effect"] == "RESTRICT_ONLY"
    assert preview["status"] == "NOT_AVAILABLE"
    assert preview["would_execute"] is False
    assert preview["execution_performed"] is False
    assert preview["finality"] == "AUTHORITY_CONTEXT_REQUIRED"
    assert preview["why_now"]


def test_t_nle_22_feature_gate_runs_before_projection_store(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    class Store:
        def get(self, *_args: object, **_kwargs: object) -> None:
            nonlocal called
            called = True
            return None

    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "false")
    get_core_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                routes._get_life_evolution_snapshot(
                    projection_store=Store(),
                    person_id="default",
                    session_id="",
                )
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["reason_code"] == "NLE_LIFE_VIEWS_DISABLED"
        assert called is False
    finally:
        get_core_settings.cache_clear()


def test_t_nle_22_route_has_no_live_producer_or_process_fallback() -> None:
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert "def _default_snapshot(" not in source
    assert "set_life_evolution_snapshot_provider" not in source
    assert "composition.process_services" not in source
    assert "read_domain_projection_from_store(" in source


def _approved_preview_payload(now_ms: int, *, token: str = "approval::ui-preview") -> dict:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-ui-preview",
        "ts_ms": now_ms,
        "session_id": "session-ui-preview",
        "seq": 1,
        "type": "skill_call",
        "action": "proactive_expression",
        "target": "conversation",
        "args": {},
        "nonce": "nonce-ui-preview",
    }
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref=token,
        risk_level=3,
        issued_at_ms=now_ms - 1_000,
        expires_at_ms=now_ms + 60_000,
    )
    return {
        "governor_decision": {
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": token},
            "approved_action_binding": binding,
            "committed_route_token_ref": "GOV_OK_RENDER",
            "committed_route_token": {"token": "GOV_OK_RENDER"},
        },
        "proposed_action": {
            "action_kind": "proactive_expression",
            "scope": "conversation",
            "risk_level": 3,
            "requires_token": True,
            "is_proactive": True,
        },
        "intent_envelope": intent,
        "scene_decision": {
            "decision_id": "scene-current",
            "proactive_policy": "ALLOW",
            "expires_at_ms": now_ms + 30_000,
        },
        "user_intent": {"explicit": False},
        "evidence_refs": ["scene-decision://scene-current"],
    }


def test_t_nle_22_client_forged_governor_context_is_ignored_and_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    try:
        forged = _approved_preview_payload(int(time.time() * 1000))
        request = _request_with_route_ports(
            ObsRoutePorts(query_projection_store_provider=lambda: object())
        )
        result = asyncio.run(
            routes.post_life_evolution_action_preview(request, forged)
        )
    finally:
        get_core_settings.cache_clear()

    assert result["authority_context_status"] == "UNAVAILABLE"
    assert result["would_execute"] is False
    assert result["arbiter_decision"]["decision"] == "BLOCK"
    assert result["arbiter_decision"]["governor_decision"] == "REJECT"
    assert result["runtime_event"]["recorded"] is False


def test_t_nle_22_server_trusted_preview_is_governed_dry_run_without_execution_or_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    class AllowingGuardian:
        def evaluate_capability(
            self,
            *,
            capability_id: str,
            static_risk_level: int,
            capability_policy_decision: object = None,
        ) -> dict:
            del static_risk_level, capability_policy_decision
            return {
                "schema_version": "Natsume.degradation.capability_decision.v1",
                "capability_id": capability_id,
                "decision": "ALLOW",
                "allowed": True,
                "allowed_by_degradation": True,
                "reason_code": "GUARDIAN_CAPABILITY_AVAILABLE",
                "durability_required": True,
                "durability_status": "READY",
                "guardian_authority": {
                    "authority": "RUNTIME_GUARDIAN",
                    "authority_status": "ACTIVE",
                    "durable": True,
                    "aggregate_ref": "runtime-guardian://sha256/test",
                    "guardian_revision": 1,
                    "fencing_token": 1,
                },
            }

    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    now_ms = int(time.time() * 1000)
    token_service = TokenService()
    token = token_service.issue_approval_token(
        intent_id="intent-ui-preview",
        skill_name="proactive_expression",
        session_id="session-ui-preview",
        seq=1,
        now_ts_ms=now_ms,
    )
    trusted = _approved_preview_payload(now_ms, token=token)
    trusted.update(
        {
            "authority_context_trusted": True,
            "authority_source_ref": "governor-commit://intent-ui-preview",
        }
    )
    request = _request_with_route_ports(
        ObsRoutePorts(
            query_projection_store_provider=lambda: object(),
            life_action_context_provider=lambda **_: trusted,
            approval_token_validator=token_service.validate_bound_token,
            degradation_capability_port_provider=AllowingGuardian,
        )
    )
    try:
        result = asyncio.run(
            routes.post_life_evolution_action_preview(
                request,
                {"preview_subject_ref": "intent-ui-preview"},
            )
        )
    finally:
        get_core_settings.cache_clear()

    assert result["status"] == "DRY_RUN"
    assert result["authority_context_status"] == "SERVER_TRUSTED_TOKEN_VALID"
    assert result["authority_source_ref"] == "governor-commit://intent-ui-preview"
    assert result["arbiter_eligible"] is True
    assert result["would_execute"] is False
    assert result["finality"] == "ARBITER_ELIGIBILITY_ONLY"
    assert result["execution_precheck_status"] == "NOT_RUN"
    assert result["execution_performed"] is False
    assert result["external_side_effects"] is False
    assert result["arbiter_decision"]["decision"] == "ALLOW"
    assert result["arbiter_decision"]["authority_binding_valid"] is True
    assert result["runtime_event"]["recorded"] is False
    assert result["runtime_event"]["cursor"] is None
    assert result["runtime_event"]["payload"]["side_effects_executed"] is False


def test_t_nle_22_monitor_surface_has_only_read_model_and_dry_run_endpoints() -> None:
    root = Path(__file__).resolve().parents[2]
    model_source = (root / "host_brain/app/monitor_ui/src/features/life/lifeEvolutionModel.js").read_text(encoding="utf-8")
    section_source = (root / "host_brain/app/monitor_ui/src/features/life/components/LifeEvolutionSection.jsx").read_text(encoding="utf-8")
    api_source = (root / "host_brain/app/monitor_ui/src/features/life/api.js").read_text(encoding="utf-8")
    registry_source = (root / "host_brain/app/monitor_ui/src/shared/api/endpointRegistry.js").read_text(encoding="utf-8")
    combined = "\n".join([model_source, section_source])
    assert "factClassLabel" in model_source
    assert "sourceRef" in model_source
    assert "confidence" in model_source
    assert "ttl" in model_source
    assert "事实" in section_source and "推断" in section_source
    assert "只读重算" in section_source
    assert "lifeEvolutionSnapshot" in api_source
    assert "lifeEvolutionPreview" in api_source
    assert "/obs/life-evolution/snapshot" in registry_source
    assert "/obs/life-evolution/action-preview" in registry_source
    for forbidden in ("submitBondEvent", "switchSysMode", "updateAutoRules", "memory_body", "message_text"):
        assert forbidden not in combined
