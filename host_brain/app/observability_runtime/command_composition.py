"""Explicit, fail-closed composition for the OBS command plane."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time
import unicodedata
from typing import Any, Callable, Mapping

from host_brain.runtime.degradation_authority import DegradationCapabilityPort

from host_brain.app.host_route_ports import HostRoutePorts

from natsume_contracts.identity import CredentialProofKind, PrincipalKind
from host_brain.secure_ingress.adapter import SecureIngressAdapter, SecureIngressMode
from host_brain.secure_ingress.credential import (
    CredentialVerifierRegistry,
    HmacSha256CredentialVerifier,
)
from host_brain.secure_ingress.emergency_audit import FileEmergencyAuditSink
from host_brain.secure_ingress.service import SecureIngressService
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore

from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    ObsCommandPortRegistry,
)
from host_brain.observability.command_outcome_journal import ObsCommandOutcomeJournal
from host_brain.observability.command_router import ObsCommandRouter
from .domain_command_ports import (
    GOVERNED_OPERATOR_COMMAND_IDS,
    build_default_obs_domain_command_registry,
)
from .review_wait_recovery import CanonicalReviewWaitRecoveryResolver
from host_brain.observability.secure_command_ingress import SecureObsCommandIngress
from host_brain.observability.shadow_ingress_state import ShadowIngressStateStore


HOST_SECURE_INGRESS_MODE_ENV = "HOST_SECURE_INGRESS_MODE"
HOST_SECURE_INGRESS_HMAC_SECRETS_ENV = "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON"
OPERATOR_QUERY_AUTH_BINDINGS_ENV = "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS"
HOST_OBS_COMMAND_OUTCOME_JOURNAL_PATH_ENV = (
    "HOST_OBS_COMMAND_OUTCOME_JOURNAL_PATH"
)
OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED = (
    "OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED"
)
OBS_COMMAND_HMAC_SECRETS_INVALID = "OBS_COMMAND_HMAC_SECRETS_INVALID"
OBS_COMMAND_HMAC_SECRETS_MISSING = "OBS_COMMAND_HMAC_SECRETS_MISSING"
OBS_COMMAND_OPERATOR_AUTHORITY_INVALID = "OBS_COMMAND_OPERATOR_AUTHORITY_INVALID"
_HMAC_KEY_ID_MAX_BYTES = 255
_HMAC_SECRET_MIN_BYTES = 32
_HMAC_SECRET_MAX_BYTES = 4_096


_OWNER_LEASES: set[str] = set()
_OWNER_LEASES_GUARD = threading.Lock()


class _OutcomeJournalOwnerLease:
    """Hold exclusive command-runtime ownership for one outcome journal.

    Journal record locks only serialize individual appends.  Recovery needs a
    stronger guarantee: no other runtime may still be executing a command for
    this journal.  This lease is therefore held for the whole composition
    lifetime and is deliberately never implemented by deleting a lock file.
    """

    def __init__(self, journal_path: Path) -> None:
        self._path = journal_path.with_suffix(journal_path.suffix + ".owner.lock")
        self._key = os.path.normcase(str(self._path.resolve()))
        self._handle: Any | None = None
        self._closed = False

    def acquire(self) -> "_OutcomeJournalOwnerLease":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _OWNER_LEASES_GUARD:
            if self._key in _OWNER_LEASES:
                raise RuntimeError("OBS_COMMAND_OUTCOME_OWNER_UNAVAILABLE")
            handle = self._path.open("a+b", buffering=0)
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                else:  # pragma: no cover - never run without an OS lease
                    raise RuntimeError("OBS_COMMAND_OUTCOME_OWNER_UNSUPPORTED")
            except Exception as exc:
                handle.close()
                if str(exc).startswith("OBS_COMMAND_OUTCOME_OWNER_"):
                    raise
                raise RuntimeError("OBS_COMMAND_OUTCOME_OWNER_UNAVAILABLE") from exc
            self._handle = handle
            _OWNER_LEASES.add(self._key)
        return self

    def close(self) -> None:
        with _OWNER_LEASES_GUARD:
            if self._closed:
                return
            handle = self._handle
            try:
                if handle is not None:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt  # noqa: PLC0415

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    elif os.name == "posix":
                        import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                if handle is not None:
                    handle.close()
                self._handle = None
                _OWNER_LEASES.discard(self._key)
                self._closed = True


@dataclass(slots=True)
class ObsCommandComposition:
    """Own every command-plane dependency and its SQLite lifecycle."""

    enabled: bool
    execution_enabled: bool
    effective_mode: SecureIngressMode
    global_mode: SecureIngressMode
    reason_code: str
    ingress: SecureObsCommandIngress
    registry: ObsCommandPortRegistry
    outcome_journal: ObsCommandOutcomeJournal | None = None
    router: ObsCommandRouter | None = None
    store: SQLiteSecureIngressStore | None = None
    service: SecureIngressService | None = None
    verifier: CredentialVerifierRegistry | None = None
    emergency_audit: FileEmergencyAuditSink | None = None
    adapter: SecureIngressAdapter | None = None
    _owner_lease: _OutcomeJournalOwnerLease | None = field(
        default=None,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _review_recovery_deferred: bool = field(default=False, repr=False)
    _close_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            close_admission = getattr(self.ingress, "close_admission", None)
            if callable(close_admission):
                close_admission()
            if int(getattr(self.ingress, "in_flight", 0) or 0) != 0:
                # Releasing the owner while a domain call is live would let a
                # replacement runtime recover that call as UNCERTAIN.  A sync
                # caller must drain first; ASGI lifespan code uses aclose().
                raise RuntimeError("OBS_COMMAND_INGRESS_DRAIN_REQUIRED")
            try:
                if self.store is not None:
                    self.store.close()
            finally:
                try:
                    if self._owner_lease is not None:
                        self._owner_lease.close()
                finally:
                    self._closed = True

    async def aclose(self) -> None:
        """Stop admission, drain admitted commands, then release ownership."""

        drain = getattr(self.ingress, "close_admission_and_drain", None)
        if callable(drain):
            await drain()
        self.close()

    def recover_deferred_review_admissions(
        self,
        *,
        command_repository: Any,
        task_store: Any,
        review_store: Any,
        review_continuation_store: Any,
    ) -> tuple[Any, ...]:
        """Settle crash-left Operator Review waits before opening admission."""

        if not self._review_recovery_deferred:
            return ()
        if self.outcome_journal is None or self.store is None:
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_UNAVAILABLE")
        resolver = CanonicalReviewWaitRecoveryResolver(
            command_repository=command_repository,
            task_store=task_store,
            review_store=review_store,
            review_continuation_store=review_continuation_store,
            secure_ingress_authority_resolver=self.store,
        )
        recovered = self.outcome_journal.recover_incomplete(
            review_wait_resolver=resolver,
        )
        opener = getattr(self.ingress, "open_admission_after_recovery", None)
        if not callable(opener):
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_ADMISSION_UNAVAILABLE")
        opener()
        self._review_recovery_deferred = False
        return tuple(recovered)

    def __enter__(self) -> "ObsCommandComposition":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()


def _parse_global_mode(env: Mapping[str, str]) -> tuple[SecureIngressMode, bool]:
    raw = str(env.get(HOST_SECURE_INGRESS_MODE_ENV, "OFF")).strip().upper()
    try:
        return SecureIngressMode(raw), True
    except ValueError:
        return SecureIngressMode.OFF, False


def _select_effective_mode(
    *,
    global_mode: SecureIngressMode,
    global_valid: bool,
) -> tuple[SecureIngressMode, str]:
    if not global_valid:
        return SecureIngressMode.OFF, "OBS_COMMAND_GLOBAL_INGRESS_MODE_INVALID"
    if global_mode is SecureIngressMode.ENFORCE:
        return SecureIngressMode.ENFORCE, "OBS_COMMAND_COMPOSITION_ENFORCE"
    if global_mode is SecureIngressMode.SHADOW:
        return SecureIngressMode.SHADOW, "OBS_COMMAND_COMPOSITION_SHADOW"
    return SecureIngressMode.OFF, "OBS_COMMAND_GLOBAL_INGRESS_DISABLED"


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("OBS_COMMAND_HMAC_SECRETS_INVALID")
        parsed[key] = value
    return parsed


def _bounded_utf8(value: str, *, minimum: int = 1, maximum: int) -> bool:
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return False
    return minimum <= size <= maximum


def _strict_hmac_secrets(env: Mapping[str, str]) -> dict[str, str]:
    raw = str(env.get(HOST_SECURE_INGRESS_HMAC_SECRETS_ENV) or "").strip()
    if not raw:
        raise ValueError("OBS_COMMAND_HMAC_SECRETS_MISSING")
    try:
        decoded = json.loads(raw, object_pairs_hook=_strict_json_object)
    except Exception as exc:
        raise ValueError("OBS_COMMAND_HMAC_SECRETS_INVALID") from exc
    if not isinstance(decoded, dict) or not decoded:
        raise ValueError("OBS_COMMAND_HMAC_SECRETS_INVALID")
    secrets: dict[str, str] = {}
    for key_id, secret in decoded.items():
        normalized_key = str(key_id).strip()
        if (
            not normalized_key
            or not _bounded_utf8(
                normalized_key,
                maximum=_HMAC_KEY_ID_MAX_BYTES,
            )
            or normalized_key in secrets
            or not isinstance(secret, str)
            or not secret.strip()
            or any(unicodedata.category(char) == "Cc" for char in secret)
            or not _bounded_utf8(
                secret,
                minimum=_HMAC_SECRET_MIN_BYTES,
                maximum=_HMAC_SECRET_MAX_BYTES,
            )
        ):
            raise ValueError("OBS_COMMAND_HMAC_SECRETS_INVALID")
        secrets[normalized_key] = secret
    return secrets


def _configured_operator_hmac_authorities(
    env: Mapping[str, str],
    *,
    secrets: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    """Resolve local Operator BFF principals without exposing bearer tokens.

    Read-only Monitor bindings need no command authority.  A binding that asks
    for ``obs:write`` must name an HMAC key and may only receive command actions
    whose complete scope set is present in that same binding.
    """

    raw = str(env.get(OPERATOR_QUERY_AUTH_BINDINGS_ENV) or "").strip()
    if not raw:
        return ()
    try:
        decoded = json.loads(raw, object_pairs_hook=_strict_json_object)
    except Exception as exc:
        raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID) from exc
    if not isinstance(decoded, list):
        raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)

    authorities: list[dict[str, Any]] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)
        raw_scopes = item.get("scopes")
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)
        scopes = tuple(sorted({str(value).strip() for value in raw_scopes}))
        if not all(scopes):
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)
        if "obs:write" not in scopes:
            continue

        principal_id = str(item.get("principal_id") or "").strip()
        tenant_id = str(item.get("tenant_id") or "local").strip()
        key_id = str(item.get("key_id") or "").strip()
        try:
            principal_kind = PrincipalKind(
                str(item.get("principal_kind") or "service").strip().lower()
            )
        except ValueError as exc:
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID) from exc
        if (
            not principal_id
            or not tenant_id
            or not key_id
            or key_id not in secrets
            or not _bounded_utf8(principal_id, maximum=255)
            or not _bounded_utf8(tenant_id, maximum=255)
            or not _bounded_utf8(key_id, maximum=_HMAC_KEY_ID_MAX_BYTES)
        ):
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)

        scope_set = frozenset(scopes)
        allowed_actions = tuple(
            sorted(
                spec.action
                for spec in DEFAULT_OBS_COMMAND_SPECS
                if set(spec.required_scopes).issubset(scope_set)
            )
        )
        if not allowed_actions:
            raise ValueError(OBS_COMMAND_OPERATOR_AUTHORITY_INVALID)
        authorities.append(
            {
                "principal_id": principal_id,
                "principal_kind": principal_kind,
                "tenant_id": tenant_id,
                "key_id": key_id,
                "granted_scopes": scopes,
                "allowed_actions": allowed_actions,
            }
        )
    return tuple(authorities)


def _disabled_composition(
    *,
    global_mode: SecureIngressMode,
    reason_code: str,
) -> ObsCommandComposition:
    registry = ObsCommandPortRegistry()
    ingress = SecureObsCommandIngress(
        ingress_adapter=None,
        ingress_service=None,
        claim_result_store=None,
        registry=registry,
        router=None,
        outcome_journal=None,
    )
    return ObsCommandComposition(
        enabled=False,
        execution_enabled=False,
        effective_mode=SecureIngressMode.OFF,
        global_mode=global_mode,
        reason_code=reason_code,
        ingress=ingress,
        registry=registry,
        outcome_journal=None,
    )


def build_obs_command_composition(
    *,
    env: Mapping[str, str] | None = None,
    db_path: str | Path | None = None,
    emergency_audit_path: str | Path | None = None,
    outcome_journal_path: str | Path | None = None,
    route_ports: HostRoutePorts | None = None,
    degradation_capability_port_provider: (
        Callable[[], DegradationCapabilityPort | None] | None
    ) = None,
    defer_review_recovery: bool = False,
) -> ObsCommandComposition:
    """Build one command plane or a terminal disabled composition.

    The single Secure Ingress mode owns admission. Missing secrets, schema
    drift, invalid configuration or dependency construction failure resolve to
    OFF; there is no relocation/legacy rollout flag.
    """

    source = os.environ if env is None else env
    global_mode, global_valid = _parse_global_mode(source)
    effective_mode, reason_code = _select_effective_mode(
        global_mode=global_mode,
        global_valid=global_valid,
    )
    if effective_mode is SecureIngressMode.OFF:
        return _disabled_composition(
            global_mode=global_mode,
            reason_code=reason_code,
        )

    try:
        secrets = _strict_hmac_secrets(source)
    except Exception as exc:
        raw_reason = exc.args[0] if len(exc.args) == 1 else None
        reason_code = (
            raw_reason
            if type(raw_reason) is str
            and raw_reason
            in {
                OBS_COMMAND_HMAC_SECRETS_INVALID,
                OBS_COMMAND_HMAC_SECRETS_MISSING,
            }
            else OBS_COMMAND_HMAC_SECRETS_INVALID
        )
        return _disabled_composition(
            global_mode=global_mode,
            reason_code=reason_code,
        )

    resolved_db_path = Path(
        str(
            db_path
            or source.get("NATSUME_AUDIT_DB")
            or source.get("Natsume_AUDIT_DB")
            or "host_brain/data/audit.sqlite3"
        )
    )
    resolved_emergency_path = Path(
        emergency_audit_path
        or resolved_db_path.with_suffix(".secure-ingress-emergency.jsonl")
    )
    resolved_outcome_journal_path = Path(
        outcome_journal_path
        or source.get(HOST_OBS_COMMAND_OUTCOME_JOURNAL_PATH_ENV)
        or resolved_db_path.with_suffix(".obs-command-outcomes.jsonl")
    )
    store: SQLiteSecureIngressStore | None = None
    outcome_journal: ObsCommandOutcomeJournal | None = None
    owner_lease: _OutcomeJournalOwnerLease | None = None
    try:
        store = SQLiteSecureIngressStore(resolved_db_path)
        operator_authorities = _configured_operator_hmac_authorities(
            source,
            secrets=secrets,
        )
        for authority in operator_authorities:
            store.ensure_hmac_principal_authority(
                principal_id=authority["principal_id"],
                principal_kind=authority["principal_kind"],
                key_id=authority["key_id"],
                tenant_id=authority["tenant_id"],
                granted_scopes=authority["granted_scopes"],
                allowed_audiences=("obs",),
                allowed_actions=authority["allowed_actions"],
                secret_ref=(
                    f"env://{HOST_SECURE_INGRESS_HMAC_SECRETS_ENV}"
                    f"#{authority['key_id']}"
                ),
                at_ms=int(time.time() * 1000),
            )
        outcome_journal = ObsCommandOutcomeJournal(resolved_outcome_journal_path)
        owner_lease = _OutcomeJournalOwnerLease(
            resolved_outcome_journal_path.resolve()
        ).acquire()
        if defer_review_recovery:
            outcome_journal.recover_incomplete(
                defer_review_command_ids=GOVERNED_OPERATOR_COMMAND_IDS,
            )
        else:
            outcome_journal.recover_incomplete()

        def _resolve_hmac_secret(authority: Any) -> str | None:
            return secrets.get(str(getattr(authority, "key_id", "") or ""))

        hmac_verifier = HmacSha256CredentialVerifier(
            secret_resolver=_resolve_hmac_secret,
        )
        verifier = CredentialVerifierRegistry({
            CredentialProofKind.HMAC_SHA256: hmac_verifier,
        })
        emergency_audit = FileEmergencyAuditSink(resolved_emergency_path)
        state_store = (
            ShadowIngressStateStore(audit_sink=store)
            if effective_mode is SecureIngressMode.SHADOW
            else store
        )
        service = SecureIngressService(
            principal_store=store,
            credential_verifier=verifier,
            attempt_limiter=store,
            rate_limiter=store,
            state_store=state_store,
            emergency_audit_sink=emergency_audit,
        )
        adapter = SecureIngressAdapter(service=service, mode=effective_mode)
        registry = build_default_obs_domain_command_registry(
            route_ports=route_ports,
            degradation_capability_port_provider=(
                degradation_capability_port_provider
            ),
        )
        router = ObsCommandRouter(registry)
        ingress = SecureObsCommandIngress(
            ingress_adapter=adapter,
            ingress_service=service,
            claim_result_store=store,
            registry=registry,
            router=router,
            outcome_journal=outcome_journal,
            admission_open=not defer_review_recovery,
        )
    except Exception:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        if owner_lease is not None:
            try:
                owner_lease.close()
            except Exception:
                pass
        return _disabled_composition(
            global_mode=global_mode,
            reason_code=OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED,
        )

    return ObsCommandComposition(
        enabled=True,
        execution_enabled=effective_mode is SecureIngressMode.ENFORCE,
        effective_mode=effective_mode,
        global_mode=global_mode,
        reason_code=reason_code,
        ingress=ingress,
        registry=registry,
        outcome_journal=outcome_journal,
        router=router,
        store=store,
        service=service,
        verifier=verifier,
        emergency_audit=emergency_audit,
        adapter=adapter,
        _owner_lease=owner_lease,
        _review_recovery_deferred=bool(defer_review_recovery),
    )


__all__ = [
    "HOST_SECURE_INGRESS_HMAC_SECRETS_ENV",
    "HOST_SECURE_INGRESS_MODE_ENV",
    "HOST_OBS_COMMAND_OUTCOME_JOURNAL_PATH_ENV",
    "OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED",
    "OBS_COMMAND_HMAC_SECRETS_INVALID",
    "OBS_COMMAND_HMAC_SECRETS_MISSING",
    "ObsCommandComposition",
    "build_obs_command_composition",
]
