"""Process-owned Secure Ingress evaluator for Host command surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Mapping

from natsume_contracts.identity import CredentialProofKind

from host_brain.secure_ingress.adapter import SecureIngressMode
from host_brain.secure_ingress.authority import SecureIngressAuthorityEvaluator
from host_brain.secure_ingress.credential import (
    CredentialVerifierRegistry,
    HmacSha256CredentialVerifier,
)
from host_brain.secure_ingress.emergency_audit import FileEmergencyAuditSink
from host_brain.secure_ingress.service import SecureIngressService
from host_brain.secure_ingress.surfaces import (
    SECURE_INGRESS_SURFACE_POLICIES,
    IngressSurface,
)
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore


COMMAND_INGRESS_CONFIGURATION_INVALID = "COMMAND_INGRESS_CONFIGURATION_INVALID"


class CommandIngressCompositionError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = COMMAND_INGRESS_CONFIGURATION_INVALID
        self.detail = str(detail or "command ingress composition is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(slots=True)
class ProcessCommandIngressContainer:
    canonical_db_path: str
    store: SQLiteSecureIngressStore
    evaluator: SecureIngressAuthorityEvaluator
    mode: SecureIngressMode
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self.store.close()
        self._closed = True

    async def aclose(self) -> None:
        self.close()


def build_process_command_ingress_container(
    *,
    canonical_db_path: str | Path,
    mode: str | SecureIngressMode,
    hmac_secrets: Mapping[str, str],
    gateway_principal_id: str | None = None,
    gateway_key_id: str | None = None,
    scheduler_clock_enabled: bool = False,
    scheduler_principal_id: str | None = None,
    scheduler_tenant_id: str | None = None,
    scheduler_key_id: str | None = None,
) -> ProcessCommandIngressContainer:
    raw_path = str(canonical_db_path or "").strip()
    if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
        raise CommandIngressCompositionError("durable canonical SQLite path is required")
    canonical = str(Path(raw_path).expanduser().resolve())
    try:
        resolved_mode = mode if isinstance(mode, SecureIngressMode) else SecureIngressMode(str(mode).strip().upper())
    except ValueError as exc:
        raise CommandIngressCompositionError("Secure Ingress mode is invalid") from exc
    secrets = {
        str(key).strip(): str(value)
        for key, value in dict(hmac_secrets or {}).items()
        if str(key).strip() and isinstance(value, str) and value
    }

    def _resolve(authority) -> str | None:
        return secrets.get(str(getattr(authority, "key_id", "") or ""))

    store = SQLiteSecureIngressStore(canonical)
    try:
        gateway_principal = str(gateway_principal_id or "").strip()
        gateway_key = str(gateway_key_id or "").strip()
        if gateway_principal and gateway_key and gateway_key in secrets:
            try:
                store.ensure_hmac_service_authority(
                    principal_id=gateway_principal,
                    key_id=gateway_key,
                    tenant_id="local",
                    granted_scopes=("host:write",),
                    allowed_audiences=("host",),
                    allowed_actions=("host:input",),
                    secret_ref="env://HOST_EDGE_GATEWAY_HMAC_SECRET",
                    at_ms=int(time.time() * 1000),
                )
            except Exception as exc:
                raise CommandIngressCompositionError(
                    "configured Gateway Secure Ingress authority is invalid"
                ) from exc
        if scheduler_clock_enabled:
            scheduler_principal = str(scheduler_principal_id or "").strip()
            scheduler_tenant = str(scheduler_tenant_id or "").strip()
            scheduler_key = str(scheduler_key_id or "").strip()
            if not all(
                (
                    scheduler_principal,
                    scheduler_tenant,
                    scheduler_key,
                    secrets.get(scheduler_key),
                )
            ):
                raise CommandIngressCompositionError(
                    "enabled SchedulerClock lacks an explicit service identity"
                )
            scheduler_policy = SECURE_INGRESS_SURFACE_POLICIES[
                IngressSurface.SCHEDULER_ADMIN
            ]
            try:
                store.ensure_hmac_service_authority(
                    principal_id=scheduler_principal,
                    key_id=scheduler_key,
                    tenant_id=scheduler_tenant,
                    granted_scopes=tuple(scheduler_policy.required_scopes),
                    allowed_audiences=(scheduler_policy.audience,),
                    allowed_actions=(scheduler_policy.action,),
                    secret_ref="env://HOST_SCHEDULER_CLOCK_HMAC_SECRET",
                    at_ms=int(time.time() * 1000),
                )
            except Exception as exc:
                raise CommandIngressCompositionError(
                    "configured SchedulerClock Secure Ingress authority is invalid"
                ) from exc
        verifier = CredentialVerifierRegistry(
            {
                CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                    secret_resolver=_resolve,
                )
            }
        )
        service = SecureIngressService(
            principal_store=store,
            credential_verifier=verifier,
            attempt_limiter=store,
            rate_limiter=store,
            state_store=store,
            emergency_audit_sink=FileEmergencyAuditSink(
                Path(canonical).with_suffix(".command-ingress-emergency.jsonl")
            ),
        )
        evaluator = SecureIngressAuthorityEvaluator(
            service=service,
            mode=resolved_mode,
        )
    except BaseException:
        store.close()
        raise
    return ProcessCommandIngressContainer(
        canonical_db_path=canonical,
        store=store,
        evaluator=evaluator,
        mode=resolved_mode,
    )


__all__ = [
    "COMMAND_INGRESS_CONFIGURATION_INVALID",
    "CommandIngressCompositionError",
    "ProcessCommandIngressContainer",
    "build_process_command_ingress_container",
]
