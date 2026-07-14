from __future__ import annotations

from dataclasses import dataclass

import pytest

from host_brain.secure_ingress import (
    AuditStatus,
    IngressReasonCode,
    IngressStage,
    SecureIngressAdapter,
    SecureIngressDecision,
    SecureIngressMode,
    load_secure_ingress_mode,
)


@dataclass
class _Service:
    allowed: bool
    calls: int = 0

    def decide(self, _request: object) -> SecureIngressDecision:
        self.calls += 1
        return SecureIngressDecision(
            allowed=self.allowed,
            stage=IngressStage.COMPLETE,
            reason_code=(
                IngressReasonCode.ALLOWED
                if self.allowed
                else IngressReasonCode.CREDENTIAL_INVALID
            ),
            primary_reason_code=(
                IngressReasonCode.ALLOWED
                if self.allowed
                else IngressReasonCode.CREDENTIAL_INVALID
            ),
            audit_ref="audit://adapter/1",
            audit_status=AuditStatus.PRIMARY_DURABLE,
            claim_ref="claim://adapter/1",
            existing_result_ref=None,
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, SecureIngressMode.OFF),
        ("", SecureIngressMode.OFF),
        ("invalid", SecureIngressMode.OFF),
        ("shadow", SecureIngressMode.SHADOW),
        ("ENFORCE", SecureIngressMode.ENFORCE),
    ],
)
def test_mode_loader_is_default_off_and_strict(
    raw: str | None,
    expected: SecureIngressMode,
) -> None:
    env = {} if raw is None else {"HOST_SECURE_INGRESS_MODE": raw}
    assert load_secure_ingress_mode(env) is expected


def test_off_does_not_evaluate_or_expose_a_legacy_allow() -> None:
    service = _Service(allowed=True)
    adapter = SecureIngressAdapter(service=service, mode=SecureIngressMode.OFF)

    outcome = adapter.evaluate(object())

    assert service.calls == 0
    assert outcome.evaluated is False
    assert outcome.permit_execution is False
    assert outcome.decision is None


@pytest.mark.parametrize("allowed", [True, False])
def test_shadow_never_permits_execution(allowed: bool) -> None:
    service = _Service(allowed=allowed)
    adapter = SecureIngressAdapter(service=service, mode=SecureIngressMode.SHADOW)

    outcome = adapter.evaluate(object())

    assert service.calls == 1
    assert outcome.evaluated is True
    assert outcome.permit_execution is False
    assert outcome.decision is not None


@pytest.mark.parametrize("allowed", [True, False])
def test_enforce_mirrors_only_the_audited_service_decision(allowed: bool) -> None:
    service = _Service(allowed=allowed)
    adapter = SecureIngressAdapter(service=service, mode=SecureIngressMode.ENFORCE)

    outcome = adapter.evaluate(object())

    assert service.calls == 1
    assert outcome.evaluated is True
    assert outcome.permit_execution is allowed
    assert outcome.decision is not None
    assert outcome.decision.audit_ref == "audit://adapter/1"
