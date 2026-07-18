"""Fail-closed production composition for signed Skill/Provider intake."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import threading
import unicodedata
from typing import Any, Mapping

from natsume_contracts.identity import CredentialProofKind

from host_brain.secure_ingress import (
    CredentialVerifierRegistry,
    FileEmergencyAuditSink,
    HmacSha256CredentialVerifier,
    SecureIngressAdapter,
    SecureIngressMode,
    SecureIngressService,
)
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.skill_lifecycle_repository import SQLiteSkillLifecycleRepository
from host_brain.persistence.supply_chain_repository import SQLiteSkillSupplyChainStore
from host_brain.core.ids import now_ms

from .signed_intake_ingress import (
    MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES,
    SecureSignedSkillProviderIntake,
)
from .supply_chain import (
    SupplyChainActivationMode,
    SkillSupplyChainVerifier,
    resolve_activation_mode,
)
from .supply_chain_quarantine import SkillBundleQuarantine
from .supply_chain_service import SkillSupplyChainService
from .skill_lifecycle import SkillLifecycleService


HOST_SECURE_INGRESS_MODE_ENV = "HOST_SECURE_INGRESS_MODE"
HOST_SECURE_INGRESS_HMAC_SECRETS_ENV = "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON"
HOST_SKILL_PROVIDER_INTAKE_ROOT_ENV = "HOST_SKILL_PROVIDER_INTAKE_ROOT"
HOST_SKILL_PROVIDER_QUARANTINE_ROOT_ENV = "HOST_SKILL_PROVIDER_QUARANTINE_ROOT"
HOST_SKILL_PROVIDER_EMERGENCY_AUDIT_PATH_ENV = (
    "HOST_SKILL_PROVIDER_EMERGENCY_AUDIT_PATH"
)

SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED = (
    "SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED"
)
SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED = (
    "SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED"
)
SKILL_PROVIDER_INTAKE_GLOBAL_ENFORCE_REQUIRED = (
    "SKILL_PROVIDER_INTAKE_GLOBAL_ENFORCE_REQUIRED"
)
SKILL_PROVIDER_INTAKE_MODE_INVALID = "SKILL_PROVIDER_INTAKE_MODE_INVALID"
SKILL_PROVIDER_INTAKE_ROOT_MISSING = "SKILL_PROVIDER_INTAKE_ROOT_MISSING"
SKILL_PROVIDER_QUARANTINE_ROOT_MISSING = (
    "SKILL_PROVIDER_QUARANTINE_ROOT_MISSING"
)
SKILL_PROVIDER_INTAKE_PATH_OVERLAP = "SKILL_PROVIDER_INTAKE_PATH_OVERLAP"
SKILL_PROVIDER_INTAKE_HMAC_SECRETS_MISSING = (
    "SKILL_PROVIDER_INTAKE_HMAC_SECRETS_MISSING"
)
SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID = (
    "SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID"
)

_SOURCE_REF_RE = re.compile(r"\Aintake://([A-Za-z0-9][A-Za-z0-9._-]{0,127})\Z")
_HMAC_KEY_ID_MAX_BYTES = 255
_HMAC_SECRET_MIN_BYTES = 32
_HMAC_SECRET_MAX_BYTES = 4096


class FilesystemSkillProviderIntakeSourceResolver:
    """Resolve one opaque inbox id without accepting caller filesystem paths."""

    def __init__(self, root: str | Path) -> None:
        configured = Path(root)
        resolved = configured.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("SKILL_PROVIDER_INTAKE_ROOT_INVALID")
        self._root = resolved

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, source_ref: str) -> Path:
        match = _SOURCE_REF_RE.fullmatch(str(source_ref or ""))
        if match is None:
            raise ValueError("SKILL_PROVIDER_INTAKE_SOURCE_REF_INVALID")
        unresolved = self._root / match.group(1)
        if unresolved.is_symlink():
            raise ValueError("SKILL_PROVIDER_INTAKE_SOURCE_ALIAS_FORBIDDEN")
        resolved = unresolved.resolve(strict=True)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("SKILL_PROVIDER_INTAKE_SOURCE_ESCAPE") from exc
        if resolved == self._root or not resolved.is_dir():
            raise ValueError("SKILL_PROVIDER_INTAKE_SOURCE_INVALID")
        return resolved


@dataclass(slots=True)
class SignedSkillProviderIntakeComposition:
    enabled: bool
    reason_code: str
    activation_mode: SupplyChainActivationMode
    secure_ingress_mode: SecureIngressMode
    ingress: SecureSignedSkillProviderIntake
    secure_store: SQLiteSecureIngressStore | None = None
    supply_chain_store: SQLiteSkillSupplyChainStore | None = None
    lifecycle_repository: SQLiteSkillLifecycleRepository | None = None
    lifecycle_service: SkillLifecycleService | None = None
    service: SecureIngressService | None = None
    adapter: SecureIngressAdapter | None = None
    source_resolver: FilesystemSkillProviderIntakeSourceResolver | None = None
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            if self.secure_store is not None:
                self.secure_store.close()
            self._closed = True

    def __enter__(self) -> "SignedSkillProviderIntakeComposition":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()


def build_signed_skill_provider_intake_composition(
    *,
    env: Mapping[str, str] | None = None,
    db_path: str | Path | None = None,
    intake_root: str | Path | None = None,
    quarantine_root: str | Path | None = None,
    emergency_audit_path: str | Path | None = None,
) -> SignedSkillProviderIntakeComposition:
    """Construct the signed intake boundary or a terminal disabled object."""

    source = os.environ if env is None else env
    activation = resolve_activation_mode(environ=source)
    if not activation.valid:
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=SecureIngressMode.OFF,
            reason_code=SKILL_PROVIDER_INTAKE_MODE_INVALID,
        )
    secure_mode, secure_valid = _secure_mode(source)
    if not secure_valid or secure_mode is not SecureIngressMode.ENFORCE:
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=SKILL_PROVIDER_INTAKE_GLOBAL_ENFORCE_REQUIRED,
        )
    if activation.mode is SupplyChainActivationMode.BUILTINS_ONLY:
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED,
        )

    raw_intake_root = intake_root or source.get(HOST_SKILL_PROVIDER_INTAKE_ROOT_ENV)
    raw_quarantine_root = quarantine_root or source.get(
        HOST_SKILL_PROVIDER_QUARANTINE_ROOT_ENV
    )
    if not str(raw_intake_root or "").strip():
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=SKILL_PROVIDER_INTAKE_ROOT_MISSING,
        )
    if not str(raw_quarantine_root or "").strip():
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=SKILL_PROVIDER_QUARANTINE_ROOT_MISSING,
        )

    try:
        hmac_secrets = _strict_hmac_secrets(source)
    except ValueError as exc:
        reason = str(exc)
        if reason not in {
            SKILL_PROVIDER_INTAKE_HMAC_SECRETS_MISSING,
            SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID,
        }:
            reason = SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=reason,
        )

    resolved_db = Path(
        str(
            db_path
            or source.get("NATSUME_AUDIT_DB")
            or source.get("Natsume_AUDIT_DB")
            or "host_brain/data/audit.sqlite3"
        )
    )
    secure_store: SQLiteSecureIngressStore | None = None
    try:
        source_resolver = FilesystemSkillProviderIntakeSourceResolver(
            Path(str(raw_intake_root))
        )
        resolved_quarantine = Path(str(raw_quarantine_root)).resolve()
        if _same_or_nested(source_resolver.root, resolved_quarantine) or _same_or_nested(
            resolved_quarantine,
            source_resolver.root,
        ):
            raise ValueError(SKILL_PROVIDER_INTAKE_PATH_OVERLAP)

        verifier = SkillSupplyChainVerifier.from_environment(environ=source)
        if (
            not verifier.configuration_valid
            or verifier.mode not in {
                SupplyChainActivationMode.QUARANTINE,
                SupplyChainActivationMode.STAGED,
            }
        ):
            raise ValueError(SKILL_PROVIDER_INTAKE_MODE_INVALID)
        quarantine = SkillBundleQuarantine(
            verifier=verifier,
            quarantine_root=resolved_quarantine,
        )
        supply_store = SQLiteSkillSupplyChainStore(resolved_db)
        lifecycle_repository = SQLiteSkillLifecycleRepository(resolved_db)
        lifecycle_service = SkillLifecycleService(
            supply_chain=supply_store,
            receipts=lifecycle_repository,
            clock=now_ms,
        )
        supply_service = SkillSupplyChainService(
            verifier=verifier,
            quarantine=quarantine,
            store=supply_store,
        )

        secure_store = SQLiteSecureIngressStore(resolved_db)

        def _secret_resolver(authority: Any) -> str | None:
            return hmac_secrets.get(str(getattr(authority, "key_id", "") or ""))

        credential_verifier = CredentialVerifierRegistry(
            {
                CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                    secret_resolver=_secret_resolver
                )
            }
        )
        emergency_path = Path(
            emergency_audit_path
            or source.get(HOST_SKILL_PROVIDER_EMERGENCY_AUDIT_PATH_ENV)
            or resolved_db.with_suffix(".skill-provider-intake-emergency.jsonl")
        )
        service = SecureIngressService(
            principal_store=secure_store,
            credential_verifier=credential_verifier,
            attempt_limiter=secure_store,
            rate_limiter=secure_store,
            state_store=secure_store,
            emergency_audit_sink=FileEmergencyAuditSink(emergency_path),
            max_body_bytes=MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES,
        )
        adapter = SecureIngressAdapter(
            service=service,
            mode=SecureIngressMode.ENFORCE,
        )
        ingress = SecureSignedSkillProviderIntake(
            ingress_adapter=adapter,
            ingress_service=service,
            claim_store=secure_store,
            supply_chain_service=supply_service,
            source_resolver=source_resolver,
        )
    except Exception as exc:
        if secure_store is not None:
            try:
                secure_store.close()
            except Exception:
                pass
        raw_reason = exc.args[0] if len(exc.args) == 1 else None
        stable_reason = (
            raw_reason
            if type(raw_reason) is str
            and raw_reason
            in {
                SKILL_PROVIDER_INTAKE_MODE_INVALID,
                SKILL_PROVIDER_INTAKE_PATH_OVERLAP,
            }
            else SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED
        )
        return _disabled(
            activation_mode=activation.mode,
            secure_mode=secure_mode,
            reason_code=stable_reason,
        )

    return SignedSkillProviderIntakeComposition(
        enabled=True,
        reason_code="SKILL_PROVIDER_INTAKE_COMPOSITION_ENFORCE",
        activation_mode=activation.mode,
        secure_ingress_mode=secure_mode,
        ingress=ingress,
        secure_store=secure_store,
        supply_chain_store=supply_store,
        lifecycle_repository=lifecycle_repository,
        lifecycle_service=lifecycle_service,
        service=service,
        adapter=adapter,
        source_resolver=source_resolver,
    )


def _disabled(
    *,
    activation_mode: SupplyChainActivationMode,
    secure_mode: SecureIngressMode,
    reason_code: str,
) -> SignedSkillProviderIntakeComposition:
    ingress = SecureSignedSkillProviderIntake(
        ingress_adapter=None,
        ingress_service=None,
        claim_store=None,
        supply_chain_service=None,
        source_resolver=None,
    )
    return SignedSkillProviderIntakeComposition(
        enabled=False,
        reason_code=str(reason_code),
        activation_mode=activation_mode,
        secure_ingress_mode=secure_mode,
        ingress=ingress,
    )


def _secure_mode(env: Mapping[str, str]) -> tuple[SecureIngressMode, bool]:
    raw = str(env.get(HOST_SECURE_INGRESS_MODE_ENV, "OFF")).strip().upper()
    try:
        return SecureIngressMode(raw), True
    except ValueError:
        return SecureIngressMode.OFF, False


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID)
        result[key] = value
    return result


def _strict_hmac_secrets(env: Mapping[str, str]) -> dict[str, str]:
    raw = str(env.get(HOST_SECURE_INGRESS_HMAC_SECRETS_ENV) or "").strip()
    if not raw:
        raise ValueError(SKILL_PROVIDER_INTAKE_HMAC_SECRETS_MISSING)
    try:
        decoded = json.loads(raw, object_pairs_hook=_strict_json_object)
    except Exception as exc:
        raise ValueError(SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID) from exc
    if not isinstance(decoded, dict) or not decoded:
        raise ValueError(SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID)
    result: dict[str, str] = {}
    for key_id, secret in decoded.items():
        normalized_key = str(key_id).strip()
        if (
            not normalized_key
            or not _bounded_utf8(normalized_key, 1, _HMAC_KEY_ID_MAX_BYTES)
            or normalized_key in result
            or not isinstance(secret, str)
            or not secret.strip()
            or any(unicodedata.category(char) == "Cc" for char in secret)
            or not _bounded_utf8(
                secret,
                _HMAC_SECRET_MIN_BYTES,
                _HMAC_SECRET_MAX_BYTES,
            )
        ):
            raise ValueError(SKILL_PROVIDER_INTAKE_HMAC_SECRETS_INVALID)
        result[normalized_key] = secret
    return result


def _bounded_utf8(value: str, minimum: int, maximum: int) -> bool:
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return False
    return minimum <= size <= maximum


def _same_or_nested(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "FilesystemSkillProviderIntakeSourceResolver",
    "HOST_SKILL_PROVIDER_EMERGENCY_AUDIT_PATH_ENV",
    "HOST_SKILL_PROVIDER_INTAKE_ROOT_ENV",
    "HOST_SKILL_PROVIDER_QUARANTINE_ROOT_ENV",
    "SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED",
    "SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED",
    "SignedSkillProviderIntakeComposition",
    "build_signed_skill_provider_intake_composition",
]
