from __future__ import annotations

from host_brain.app.capability_gap_detector import CapabilityGapDecision
from host_brain.app.runtime_port_wiring import build_executive_runtime_application_ports
from host_brain.executive_runtime.skill_execution import _maybe_synthesize_runtime_capability
from host_brain.app.runtime_capability_synthesizer import RuntimeCapabilityRegistry


def test_runtime_synthesis_attaches_auto_plan_result(monkeypatch) -> None:
    gap = CapabilityGapDecision(
        detected=True,
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        confidence=0.9,
        missing_capability_tags=("coding",),
        suggested_family="code",
        details={"source": "unit-test"},
    )

    class _FakeForgeService:
        def get_runtime_route_override(self, **kwargs):
            return {"apply": False, "reason": "no_active_proposal"}

        def maybe_auto_plan_for_gap(self, **kwargs):
            return {
                "triggered": True,
                "reason": "PROPOSAL_CREATED",
                "proposal_id": "forge_prop_unit",
                "candidate_key": "ACTION_INTENT_FALLBACK_CHAT|code|coding",
            }

    class _FakeTelemetry:
        def __init__(self) -> None:
            self.calls = []

        def record_detected(self, **kwargs) -> None:
            self.calls.append(dict(kwargs))

    telemetry = _FakeTelemetry()
    registry = RuntimeCapabilityRegistry()

    monkeypatch.setattr("host_brain.executive_runtime.skill_execution.detect_capability_gap", lambda **_: gap)
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_capability_forge_service",
        lambda: _FakeForgeService(),
    )
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_capability_gap_telemetry",
        lambda: telemetry,
    )
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_runtime_capability_registry",
        lambda: registry,
    )

    payload = _maybe_synthesize_runtime_capability(
        ports=build_executive_runtime_application_ports(),
        request_id="req-auto-forge",
        session_id="sess-auto-forge",
        user_message="please implement and fix",
        route_type="chat",
        target_id="",
        reason_code="llm_route:chat",
        llm_route_used=True,
    )

    assert payload is not None
    assert payload["executor_route"] == "skill"
    assert payload["executor_target"] == "agent_worker"
    assert payload["capability_forge"]["auto_plan"]["triggered"] is True
    assert payload["capability_forge"]["auto_plan"]["proposal_id"] == "forge_prop_unit"
    assert len(telemetry.calls) == 1
