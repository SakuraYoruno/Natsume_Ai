"""Process-owned Secure Ingress evaluator for Host command surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from natsume_contracts.identity import CredentialProofKind

from host_brain.secure_ingress import (
    CredentialVerifierRegistry,
    FileEmergencyAuditSink,
    HmacSha256CredentialVerifier,
    SecureIngressAuthorityEvaluator,
    SecureIngressMode,
    SecureIngressService,
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
