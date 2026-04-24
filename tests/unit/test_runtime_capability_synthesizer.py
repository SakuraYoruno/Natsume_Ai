from __future__ import annotations

from host_brain.app.capability_gap_detector import CapabilityGapDecision
from host_brain.app.runtime_capability_synthesizer import (
    RuntimeCapabilityRegistry,
    synthesize_runtime_capability,
)


def _gap_decision(*, tags: tuple[str, ...], family: str | None) -> CapabilityGapDecision:
    return CapabilityGapDecision(
        detected=True,
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        confidence=0.82,
        missing_capability_tags=tags,
        suggested_family=family,
        details={"source": "unit-test"},
    )


def test_synthesize_runtime_capability_picks_code_executor() -> None:
    registry = RuntimeCapabilityRegistry()
    spec = synthesize_runtime_capability(
        request_id="req-1",
        session_id="sess-1",
        user_message="请帮我写一个脚本",
        gap_decision=_gap_decision(tags=("coding",), family="code"),
        registry=registry,
    )

    assert spec.route_type == "skill"
    assert spec.target_id == "codex_worker"
    assert spec.skill_family == "code"
    assert "RuntimeCapability" in spec.task
    assert registry.get(spec.runtime_capability_id) is not None


def test_synthesize_runtime_capability_falls_back_to_intent_agent() -> None:
    spec = synthesize_runtime_capability(
        request_id="req-2",
        session_id="sess-2",
        user_message="请帮我做一个复杂任务",
        gap_decision=_gap_decision(tags=("reasoning",), family="reasoning"),
        registry=None,
    )

    assert spec.route_type == "delegate"
    assert spec.target_id == "intent_agent"
    assert spec.skill_family == "reasoning"


def test_runtime_capability_registry_drops_expired_items() -> None:
    registry = RuntimeCapabilityRegistry()
    spec = synthesize_runtime_capability(
        request_id="req-3",
        session_id="sess-3",
        user_message="翻译这段文本",
        gap_decision=_gap_decision(tags=("translation",), family="translation"),
        registry=registry,
        ttl_seconds=1,
    )

    assert registry.get(spec.runtime_capability_id) is not None
    # Simulate expiration by direct remove to keep test deterministic.
    registry.remove(spec.runtime_capability_id)
    assert registry.get(spec.runtime_capability_id) is None

