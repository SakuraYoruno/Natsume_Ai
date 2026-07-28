"""Application boundary for the authoritative NE-05 Skill lifecycle."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from host_brain.contracts.skill_lifecycle_v2 import (
    SkillActivationEvidenceV2,
    SkillLifecycleReceiptV2,
)


class SkillLifecycleV2Authority(Protocol):
    def request_canary(self, **kwargs: Any) -> dict[str, Any]: ...

    def claim_canary_outbox(self, **kwargs: Any) -> dict[str, Any] | None: ...

    def settle_canary(self, **kwargs: Any) -> dict[str, Any]: ...

    def activate(
        self,
        evidence: SkillActivationEvidenceV2,
        **kwargs: Any,
    ) -> SkillLifecycleReceiptV2: ...

    def degrade(self, **kwargs: Any) -> SkillLifecycleReceiptV2: ...

    def rollback(self, **kwargs: Any) -> SkillLifecycleReceiptV2: ...

    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> Mapping[str, Any] | None: ...

    def list_active_versions(self) -> list[dict[str, Any]]: ...


class SkillLifecycleV2Service:
    """Expose one explicit application API over the atomic SQLite authority."""

    def __init__(self, *, authority: SkillLifecycleV2Authority) -> None:
        required = (
            "request_canary",
            "claim_canary_outbox",
            "settle_canary",
            "activate",
            "degrade",
            "rollback",
            "resolve_active",
            "list_active_versions",
        )
        if any(not callable(getattr(authority, name, None)) for name in required):
            raise TypeError("Skill lifecycle V2 authority is incomplete")
        self._authority = authority

    def request_canary(self, **kwargs: Any) -> dict[str, Any]:
        return self._authority.request_canary(**kwargs)

    def claim_canary_outbox(self, **kwargs: Any) -> dict[str, Any] | None:
        return self._authority.claim_canary_outbox(**kwargs)

    def settle_canary(self, **kwargs: Any) -> dict[str, Any]:
        return self._authority.settle_canary(**kwargs)

    def activate(
        self,
        evidence: SkillActivationEvidenceV2,
        *,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        return self._authority.activate(
            evidence,
            expected_activation_generation=expected_activation_generation,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )

    def degrade(
        self,
        *,
        bundle_id: str,
        idempotency_key: str,
        reason_code: str = "SKILL_DEGRADED_BY_GUARDIAN",
    ) -> SkillLifecycleReceiptV2:
        return self._authority.degrade(
            bundle_id=bundle_id,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
        )

    def rollback(
        self,
        *,
        current_bundle_id: str,
        target_bundle_id: str,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        return self._authority.rollback(
            current_bundle_id=current_bundle_id,
            target_bundle_id=target_bundle_id,
            expected_activation_generation=expected_activation_generation,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )

    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> Mapping[str, Any] | None:
        return self._authority.resolve_active(
            bundle_kind=bundle_kind,
            bundle_name=bundle_name,
        )

    def list_active_versions(self) -> list[dict[str, Any]]:
        return self._authority.list_active_versions()


__all__ = [
    "SkillLifecycleV2Authority",
    "SkillLifecycleV2Service",
]
