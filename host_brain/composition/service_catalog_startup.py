"""Fail-closed service-catalog startup composition for TASK-0492.

This module is the only bridge between catalog preflight observations and a
candidate's lifecycle callbacks.  It intentionally does not read
``os.environ``, open sockets, start processes, stop incumbents or discover
services.  All ambient capabilities are explicit constructor dependencies.

An ENFORCE preparation contains only an opaque local reference and a
process-local HMAC.  The underlying probe reservation remains private to the
coordinator, is bound to the complete catalog snapshot, expires quickly and
is consumed before the first startup callback.  A prepared object is frozen,
but the HMAC validation also detects ``object.__setattr__`` mutation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from host_brain.service_catalog.preflight import preflight_catalog
from host_brain.service_catalog.models import CatalogSnapshot
from host_brain.service_catalog.probes import (
    BindProbe,
    HealthProbe,
    ReservationAttestation,
)


HOST_SERVICE_CATALOG_MODE_ENV = "HOST_SERVICE_CATALOG_MODE"
HOST_SERVICE_CATALOG_RESERVATION_TTL_MS_ENV = (
    "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS"
)

MODE_AUDIT = "AUDIT"
MODE_PREFLIGHT = "PREFLIGHT"
MODE_ENFORCE = "ENFORCE"
_ALLOWED_MODES = frozenset({MODE_AUDIT, MODE_PREFLIGHT, MODE_ENFORCE})

DEFAULT_RESERVATION_TTL_MS = 5_000
MIN_RESERVATION_TTL_MS = 100
MAX_RESERVATION_TTL_MS = 10_000

STATUS_PREPARED = "PREPARED"
STATUS_BLOCKED = "BLOCKED"
STATUS_AUDIT_ONLY = "AUDIT_ONLY"
STATUS_STARTED = "STARTED"
STATUS_START_FAILED = "START_FAILED"
STATUS_HEALTH_FAILED = "HEALTH_FAILED"

REASON_START_PREPARED = "SERVICE_CATALOG_START_PREPARED"
REASON_AUDIT_ONLY = "SERVICE_CATALOG_AUDIT_ONLY"
REASON_MODE_INVALID = "SERVICE_CATALOG_MODE_INVALID"
REASON_TTL_INVALID = "SERVICE_CATALOG_RESERVATION_TTL_INVALID"
REASON_CANDIDATE_INVALID = "SERVICE_CATALOG_CANDIDATE_INVALID"
REASON_CANDIDATE_NOT_FOUND = "SERVICE_CATALOG_CANDIDATE_NOT_FOUND"
REASON_PREFLIGHT_FAILED = "SERVICE_CATALOG_PREFLIGHT_FAILED"
REASON_PREFLIGHT_BLOCKED = "SERVICE_CATALOG_PREFLIGHT_BLOCKED"
REASON_RESERVATION_REQUIRED = "SERVICE_CATALOG_RESERVATION_REQUIRED"
REASON_RESERVATION_INVALID = "SERVICE_CATALOG_RESERVATION_INVALID"
REASON_RESERVATION_EXPIRED = "SERVICE_CATALOG_RESERVATION_EXPIRED"
REASON_RESERVATION_REPLAYED = "SERVICE_CATALOG_RESERVATION_REPLAYED"
REASON_PREPARATION_TAMPERED = "SERVICE_CATALOG_PREPARATION_TAMPERED"
REASON_SNAPSHOT_CHANGED = "SERVICE_CATALOG_SNAPSHOT_CHANGED"
REASON_START_CALLBACK_FAILED = "SERVICE_CATALOG_START_CALLBACK_FAILED"
REASON_HEALTH_PROBE_FAILED = "SERVICE_CATALOG_HEALTH_PROBE_FAILED"
REASON_HEALTH_IDENTITY_MISMATCH = "SERVICE_CATALOG_HEALTH_IDENTITY_MISMATCH"
REASON_STARTED = "SERVICE_CATALOG_CANDIDATE_STARTED"


@dataclass(frozen=True, slots=True)
class PreparedServiceStart:
    """Immutable, sealed result of a catalog startup preflight.

    ``reservation_ref`` is a coordinator-local opaque reference.  It is not
    the probe reservation identifier and is useless outside the coordinator
    that issued it.
    """

    candidate_owner_id: str
    mode: str
    snapshot_digest: str
    endpoint_ids: tuple[str, ...]
    prepared_at_ms: int
    expires_at_ms: int
    start_allowed: bool
    status: str
    reason_code: str
    issue_codes: tuple[str, ...]
    reservation_ref: str
    authority_seal: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_owner_id": self.candidate_owner_id,
            "mode": self.mode,
            "snapshot_digest": self.snapshot_digest,
            "endpoint_ids": list(self.endpoint_ids),
            "prepared_at_ms": self.prepared_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "start_allowed": self.start_allowed,
            "status": self.status,
            "reason_code": self.reason_code,
            "issue_codes": list(self.issue_codes),
            "reservation_ref": self.reservation_ref,
            # The seal is deliberately omitted from normal audit rendering.
        }


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """Stable candidate lifecycle outcome without leaking callback objects."""

    candidate_owner_id: str
    mode: str
    snapshot_digest: str
    status: str
    reason_code: str
    reservation_ref: str
    started: bool
    start_called: bool
    health_checked: bool
    stop_called: bool
    candidate_stopped: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _ActivationState:
    prepared_fingerprint: str
    snapshot_fingerprint: str
    endpoints: tuple[Any, ...]
    attestation: ReservationAttestation | None
    consumed: bool = False


def _clock_ms() -> int:
    return int(time.time() * 1000)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return _jsonable(as_dict())
    raise TypeError(f"non-canonical catalog value: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_attr(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _snapshot_endpoints(snapshot: Any) -> tuple[Any, ...]:
    endpoints = _read_attr(snapshot, "endpoints", "endpoint_specs", default=())
    if isinstance(endpoints, (str, bytes)) or not isinstance(endpoints, Sequence):
        return ()
    return tuple(endpoints)


def _endpoint_id(endpoint: Any) -> str:
    return str(_read_attr(endpoint, "endpoint_id", default="") or "").strip()


def _endpoint_owner(endpoint: Any) -> str:
    return str(
        _read_attr(endpoint, "owner_id", "service_id", "owner", default="") or ""
    ).strip()


def _endpoint_protocol_id(endpoint: Any) -> str:
    health = _read_attr(endpoint, "health", default=None)
    expected = _read_attr(health, "expected_protocol", default="")
    if expected:
        return str(_enum_value(expected) or "").strip()
    direct = _read_attr(endpoint, "protocol_id", "protocol", default="")
    if direct:
        return str(_enum_value(direct) or "").strip()
    protocols = _read_attr(endpoint, "protocols", default=())
    if isinstance(protocols, Sequence) and not isinstance(protocols, (str, bytes)):
        if protocols:
            return str(_read_attr(protocols[0], "name", default="") or "").strip()
    return ""


def _endpoint_protocol_version(endpoint: Any) -> str:
    health = _read_attr(endpoint, "health", default=None)
    expected = _read_attr(health, "expected_protocol_version", default="")
    if expected:
        return str(expected).strip()
    direct = _read_attr(endpoint, "protocol_version", default="")
    if direct:
        return str(direct).strip()
    protocols = _read_attr(endpoint, "protocols", default=())
    if isinstance(protocols, Sequence) and not isinstance(protocols, (str, bytes)):
        if protocols:
            return str(_read_attr(protocols[0], "version", default="") or "").strip()
    return ""


def _is_enabled_listener(endpoint: Any) -> bool:
    if _read_attr(endpoint, "enabled", default=True) is not True:
        return False
    kind = str(
        _enum_value(
            _read_attr(endpoint, "kind", "endpoint_kind", "claim_kind", default="LISTENER")
        )
        or ""
    ).strip().upper()
    return kind in {"LISTENER", "STATIC"}


def _snapshot_digest(snapshot: Any) -> str:
    return str(
        _read_attr(snapshot, "snapshot_digest", "catalog_digest", "digest", default="")
        or ""
    ).strip()


def _issue_codes(report: Any) -> tuple[str, ...]:
    issues = _read_attr(report, "issues", default=())
    if isinstance(issues, (str, bytes)) or not isinstance(issues, Sequence):
        return ()
    output: list[str] = []
    for issue in issues:
        code = str(
            _read_attr(issue, "reason_code", "code", default="") or ""
        ).strip()
        if code and code not in output:
            output.append(code)
    return tuple(output)


def _copy_attestation(value: Any) -> ReservationAttestation | None:
    if value is None:
        return None
    try:
        return ReservationAttestation(
            reservation_ref=str(value.reservation_ref),
            snapshot_digest=str(value.snapshot_digest),
            endpoint_ids=tuple(str(item) for item in value.endpoint_ids),
            issued_at_ms=int(value.issued_at_ms),
            expires_at_ms=int(value.expires_at_ms),
            consumable=value.consumable is True,
        )
    except (AttributeError, TypeError, ValueError):
        return None


class ServiceCatalogStartupCoordinator:
    """Prepare and activate exactly one catalog-bound service candidate.

    The environment and probes are mandatory explicit inputs.  The coordinator
    keeps no reference to the caller's environment mapping after construction.
    """

    def __init__(
        self,
        *,
        snapshot: CatalogSnapshot,
        env: Mapping[str, str],
        bind_probe: BindProbe,
        health_probe: HealthProbe,
        clock_ms: Callable[[], int] = _clock_ms,
    ) -> None:
        if not isinstance(env, Mapping):
            raise TypeError("env must be an explicit mapping")
        if type(snapshot) is not CatalogSnapshot:
            raise TypeError("snapshot must be a typed CatalogSnapshot")
        if bind_probe is None or health_probe is None:
            raise TypeError("bind_probe and health_probe must be injected")
        if not callable(clock_ms):
            raise TypeError("clock_ms must be callable")

        self._snapshot = snapshot
        self._env = {str(key): str(value) for key, value in env.items()}
        self._bind_probe = bind_probe
        self._health_probe = health_probe
        self._clock_ms = clock_ms
        self._seal_key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._activation_states: dict[str, _ActivationState] = {}

        raw_mode = self._env.get(HOST_SERVICE_CATALOG_MODE_ENV, MODE_AUDIT)
        self._mode = str(raw_mode or "").strip().upper()
        self._mode_valid = self._mode in _ALLOWED_MODES

        raw_ttl = self._env.get(
            HOST_SERVICE_CATALOG_RESERVATION_TTL_MS_ENV,
            str(DEFAULT_RESERVATION_TTL_MS),
        )
        try:
            ttl_ms = int(str(raw_ttl).strip())
        except (TypeError, ValueError):
            ttl_ms = -1
        self._ttl_ms = ttl_ms
        self._ttl_valid = MIN_RESERVATION_TTL_MS <= ttl_ms <= MAX_RESERVATION_TTL_MS

    @property
    def mode(self) -> str:
        return self._mode

    def prepare_service_start(self, *, candidate_owner_id: str) -> PreparedServiceStart:
        now_ms = int(self._clock_ms())
        candidate = str(candidate_owner_id or "").strip()
        snapshot_digest = _snapshot_digest(self._snapshot)
        all_endpoints = tuple(
            endpoint
            for endpoint in _snapshot_endpoints(self._snapshot)
            if _is_enabled_listener(endpoint)
        )
        endpoints = tuple(
            endpoint
            for endpoint in all_endpoints
            if _endpoint_owner(endpoint) == candidate
        )
        endpoint_ids = tuple(sorted(_endpoint_id(endpoint) for endpoint in endpoints))

        if not candidate:
            return self._blocked_preparation(
                candidate_owner_id="",
                snapshot_digest=snapshot_digest,
                endpoint_ids=(),
                now_ms=now_ms,
                reason_code=REASON_CANDIDATE_INVALID,
            )
        if not self._mode_valid:
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=REASON_MODE_INVALID,
            )
        if not self._ttl_valid:
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=REASON_TTL_INVALID,
            )
        if not endpoints or any(not endpoint_id for endpoint_id in endpoint_ids):
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=REASON_CANDIDATE_NOT_FOUND,
            )

        try:
            report = preflight_catalog(
                self._snapshot,
                mode=self._mode,
                bind_probe=self._bind_probe,
                health_probe=self._health_probe,
                now_ms=now_ms,
                reservation_ttl_ms=self._ttl_ms,
                claim_endpoint_ids=endpoint_ids,
            )
        except Exception as exc:  # fail closed across injected boundaries
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=REASON_PREFLIGHT_FAILED,
                detail_code=type(exc).__name__,
            )

        report_digest = str(_read_attr(report, "snapshot_digest", default="") or "")
        issues = _issue_codes(report)
        report_allowed = (
            _read_attr(report, "allowed", "start_allowed", default=False) is True
        )
        if not snapshot_digest or report_digest != snapshot_digest:
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=REASON_SNAPSHOT_CHANGED,
                issue_codes=issues,
            )
        if self._mode == MODE_AUDIT:
            return self._new_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                expires_at_ms=now_ms + self._ttl_ms,
                start_allowed=False,
                status=STATUS_AUDIT_ONLY,
                reason_code=REASON_AUDIT_ONLY,
                issue_codes=issues,
                reservation_ref="",
            )
        if not report_allowed:
            return self._blocked_preparation(
                candidate_owner_id=candidate,
                snapshot_digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=now_ms,
                reason_code=issues[0] if issues else REASON_PREFLIGHT_BLOCKED,
                issue_codes=issues,
            )

        attestation: ReservationAttestation | None = None
        expires_at_ms = now_ms + self._ttl_ms
        if self._mode == MODE_ENFORCE:
            attestation = _copy_attestation(
                _read_attr(report, "reservation", default=None)
            )
            expected_claim_ids = endpoint_ids
            if not self._valid_attestation(
                attestation,
                snapshot_digest=snapshot_digest,
                expected_endpoint_ids=expected_claim_ids,
                now_ms=now_ms,
            ):
                return self._blocked_preparation(
                    candidate_owner_id=candidate,
                    snapshot_digest=snapshot_digest,
                    endpoint_ids=endpoint_ids,
                    now_ms=now_ms,
                    reason_code=REASON_RESERVATION_REQUIRED,
                    issue_codes=issues,
                )
            assert attestation is not None
            expires_at_ms = min(expires_at_ms, attestation.expires_at_ms)

        reservation_ref = "svcstart_" + secrets.token_urlsafe(24)
        prepared = self._new_preparation(
            candidate_owner_id=candidate,
            snapshot_digest=snapshot_digest,
            endpoint_ids=endpoint_ids,
            now_ms=now_ms,
            expires_at_ms=expires_at_ms,
            start_allowed=True,
            status=STATUS_PREPARED,
            reason_code=REASON_START_PREPARED,
            issue_codes=issues,
            reservation_ref=reservation_ref,
        )
        state = _ActivationState(
            prepared_fingerprint=self._prepared_fingerprint(prepared),
            snapshot_fingerprint=_sha256(self._snapshot),
            endpoints=endpoints,
            attestation=attestation,
        )
        with self._lock:
            self._activation_states[reservation_ref] = state
        return prepared

    def activate_candidate(
        self,
        prepared: PreparedServiceStart,
        *,
        start_candidate: Callable[[], Any],
        stop_candidate: Callable[[Any], None],
    ) -> ActivationResult:
        """Consume a preparation and run candidate-only lifecycle callbacks."""

        if type(prepared) is not PreparedServiceStart or not self._verify_prepared(prepared):
            return self._result(
                prepared,
                status=STATUS_BLOCKED,
                reason_code=REASON_PREPARATION_TAMPERED,
            )
        if prepared.mode == MODE_AUDIT:
            # AUDIT can report start_allowed=True for rollout observation, but
            # this composition boundary never turns that observation into an
            # effect.
            return self._result(
                prepared,
                status=STATUS_AUDIT_ONLY,
                reason_code=REASON_AUDIT_ONLY,
            )
        if not prepared.start_allowed or prepared.status != STATUS_PREPARED:
            return self._result(
                prepared,
                status=STATUS_BLOCKED,
                reason_code=prepared.reason_code or REASON_PREFLIGHT_BLOCKED,
            )
        if not callable(start_candidate) or not callable(stop_candidate):
            return self._result(
                prepared,
                status=STATUS_BLOCKED,
                reason_code=REASON_START_CALLBACK_FAILED,
            )

        now_ms = int(self._clock_ms())
        with self._lock:
            state = self._activation_states.get(prepared.reservation_ref)
            if state is None or not hmac.compare_digest(
                state.prepared_fingerprint,
                self._prepared_fingerprint(prepared),
            ):
                return self._result(
                    prepared,
                    status=STATUS_BLOCKED,
                    reason_code=REASON_RESERVATION_INVALID,
                )
            if state.consumed:
                return self._result(
                    prepared,
                    status=STATUS_BLOCKED,
                    reason_code=REASON_RESERVATION_REPLAYED,
                )
            if now_ms >= prepared.expires_at_ms:
                return self._result(
                    prepared,
                    status=STATUS_BLOCKED,
                    reason_code=REASON_RESERVATION_EXPIRED,
                )
            try:
                current_snapshot_fingerprint = _sha256(self._snapshot)
            except (TypeError, ValueError):
                current_snapshot_fingerprint = ""
            if not current_snapshot_fingerprint or not hmac.compare_digest(
                state.snapshot_fingerprint,
                current_snapshot_fingerprint,
            ):
                return self._result(
                    prepared,
                    status=STATUS_BLOCKED,
                    reason_code=REASON_SNAPSHOT_CHANGED,
                )

            # Claim locally before calling an injected boundary.  Any failure
            # is terminal for this preparation, so concurrent/retry callers
            # can never reach start_candidate.
            state.consumed = True
            if prepared.mode == MODE_ENFORCE:
                attestation = state.attestation
                if not self._valid_attestation(
                    attestation,
                    snapshot_digest=prepared.snapshot_digest,
                    expected_endpoint_ids=prepared.endpoint_ids,
                    now_ms=now_ms,
                ):
                    return self._result(
                        prepared,
                        status=STATUS_BLOCKED,
                        reason_code=REASON_RESERVATION_INVALID,
                    )
                assert attestation is not None
                try:
                    consumed = self._bind_probe.consume_reservation(
                        attestation,
                        snapshot_digest=prepared.snapshot_digest,
                        now_ms=now_ms,
                    )
                except Exception:
                    consumed = False
                if consumed is not True:
                    return self._result(
                        prepared,
                        status=STATUS_BLOCKED,
                        reason_code=REASON_RESERVATION_INVALID,
                    )

        try:
            candidate_handle = start_candidate()
        except Exception as exc:
            return self._result(
                prepared,
                status=STATUS_START_FAILED,
                reason_code=REASON_START_CALLBACK_FAILED,
                start_called=True,
                detail=type(exc).__name__,
            )

        health_ok, health_reason, health_detail = self._verify_candidate_health(
            state.endpoints
        )
        if health_ok:
            return self._result(
                prepared,
                status=STATUS_STARTED,
                reason_code=REASON_STARTED,
                started=True,
                start_called=True,
                health_checked=True,
            )

        candidate_stopped = False
        stop_detail = ""
        try:
            stop_candidate(candidate_handle)
            candidate_stopped = True
        except Exception as exc:
            stop_detail = f";stop:{type(exc).__name__}"
        return self._result(
            prepared,
            status=STATUS_HEALTH_FAILED,
            reason_code=health_reason,
            start_called=True,
            health_checked=True,
            stop_called=True,
            candidate_stopped=candidate_stopped,
            detail=health_detail + stop_detail,
        )

    def _valid_attestation(
        self,
        attestation: ReservationAttestation | None,
        *,
        snapshot_digest: str,
        expected_endpoint_ids: tuple[str, ...],
        now_ms: int,
    ) -> bool:
        if type(attestation) is not ReservationAttestation:
            return False
        assert attestation is not None
        return bool(
            attestation.reservation_ref
            and attestation.consumable is True
            and attestation.snapshot_digest == snapshot_digest
            and tuple(sorted(attestation.endpoint_ids)) == expected_endpoint_ids
            and attestation.issued_at_ms <= now_ms < attestation.expires_at_ms
            and 0 < attestation.expires_at_ms - attestation.issued_at_ms
            <= MAX_RESERVATION_TTL_MS
            and attestation.expires_at_ms <= now_ms + self._ttl_ms
        )

    def _verify_candidate_health(
        self, endpoints: tuple[Any, ...]
    ) -> tuple[bool, str, str]:
        for endpoint in endpoints:
            endpoint_id = _endpoint_id(endpoint)
            expected_owner = _endpoint_owner(endpoint)
            expected_protocol = _endpoint_protocol_id(endpoint)
            expected_version = _endpoint_protocol_version(endpoint)
            try:
                observation = self._health_probe.inspect(endpoint)
            except Exception as exc:
                return False, REASON_HEALTH_PROBE_FAILED, type(exc).__name__
            observed_endpoint = str(
                _read_attr(observation, "endpoint_id", default="") or ""
            )
            healthy = _read_attr(observation, "healthy", default=False) is True
            observed_owner = str(
                _read_attr(observation, "owner_id", default="") or ""
            )
            observed_protocol = str(
                _read_attr(observation, "protocol_id", default="") or ""
            )
            observed_version = str(
                _read_attr(observation, "protocol_version", default="") or ""
            )
            if not healthy:
                return False, REASON_HEALTH_PROBE_FAILED, endpoint_id
            if (
                observed_endpoint != endpoint_id
                or observed_owner != expected_owner
                or observed_protocol != expected_protocol
                or observed_version != expected_version
            ):
                return False, REASON_HEALTH_IDENTITY_MISMATCH, endpoint_id
        return True, REASON_STARTED, ""

    def _blocked_preparation(
        self,
        *,
        candidate_owner_id: str,
        snapshot_digest: str,
        endpoint_ids: tuple[str, ...],
        now_ms: int,
        reason_code: str,
        issue_codes: tuple[str, ...] = (),
        detail_code: str = "",
    ) -> PreparedServiceStart:
        codes = issue_codes
        if detail_code and detail_code not in codes:
            codes = (*codes, detail_code)
        return self._new_preparation(
            candidate_owner_id=candidate_owner_id,
            snapshot_digest=snapshot_digest,
            endpoint_ids=endpoint_ids,
            now_ms=now_ms,
            expires_at_ms=now_ms,
            start_allowed=False,
            status=STATUS_BLOCKED,
            reason_code=reason_code,
            issue_codes=codes,
            reservation_ref="",
        )

    def _new_preparation(
        self,
        *,
        candidate_owner_id: str,
        snapshot_digest: str,
        endpoint_ids: tuple[str, ...],
        now_ms: int,
        expires_at_ms: int,
        start_allowed: bool,
        status: str,
        reason_code: str,
        issue_codes: tuple[str, ...],
        reservation_ref: str,
    ) -> PreparedServiceStart:
        fields = {
            "candidate_owner_id": candidate_owner_id,
            "mode": self._mode,
            "snapshot_digest": snapshot_digest,
            "endpoint_ids": endpoint_ids,
            "prepared_at_ms": now_ms,
            "expires_at_ms": expires_at_ms,
            "start_allowed": start_allowed,
            "status": status,
            "reason_code": reason_code,
            "issue_codes": issue_codes,
            "reservation_ref": reservation_ref,
        }
        seal = hmac.new(
            self._seal_key,
            _canonical_bytes(fields),
            hashlib.sha256,
        ).hexdigest()
        return PreparedServiceStart(**fields, authority_seal=seal)

    def _prepared_fingerprint(self, prepared: PreparedServiceStart) -> str:
        return _sha256(
            {
                "candidate_owner_id": prepared.candidate_owner_id,
                "mode": prepared.mode,
                "snapshot_digest": prepared.snapshot_digest,
                "endpoint_ids": prepared.endpoint_ids,
                "prepared_at_ms": prepared.prepared_at_ms,
                "expires_at_ms": prepared.expires_at_ms,
                "start_allowed": prepared.start_allowed,
                "status": prepared.status,
                "reason_code": prepared.reason_code,
                "issue_codes": prepared.issue_codes,
                "reservation_ref": prepared.reservation_ref,
                "authority_seal": prepared.authority_seal,
            }
        )

    def _verify_prepared(self, prepared: PreparedServiceStart) -> bool:
        try:
            expected = hmac.new(
                self._seal_key,
                _canonical_bytes(
                    {
                        "candidate_owner_id": prepared.candidate_owner_id,
                        "mode": prepared.mode,
                        "snapshot_digest": prepared.snapshot_digest,
                        "endpoint_ids": prepared.endpoint_ids,
                        "prepared_at_ms": prepared.prepared_at_ms,
                        "expires_at_ms": prepared.expires_at_ms,
                        "start_allowed": prepared.start_allowed,
                        "status": prepared.status,
                        "reason_code": prepared.reason_code,
                        "issue_codes": prepared.issue_codes,
                        "reservation_ref": prepared.reservation_ref,
                    }
                ),
                hashlib.sha256,
            ).hexdigest()
        except (AttributeError, TypeError, ValueError):
            return False
        return hmac.compare_digest(expected, str(prepared.authority_seal))

    @staticmethod
    def _result(
        prepared: Any,
        *,
        status: str,
        reason_code: str,
        started: bool = False,
        start_called: bool = False,
        health_checked: bool = False,
        stop_called: bool = False,
        candidate_stopped: bool = False,
        detail: str = "",
    ) -> ActivationResult:
        return ActivationResult(
            candidate_owner_id=str(
                _read_attr(prepared, "candidate_owner_id", default="") or ""
            ),
            mode=str(_read_attr(prepared, "mode", default="") or ""),
            snapshot_digest=str(
                _read_attr(prepared, "snapshot_digest", default="") or ""
            ),
            status=status,
            reason_code=reason_code,
            reservation_ref=str(
                _read_attr(prepared, "reservation_ref", default="") or ""
            ),
            started=started,
            start_called=start_called,
            health_checked=health_checked,
            stop_called=stop_called,
            candidate_stopped=candidate_stopped,
            detail=detail,
        )


def prepare_service_start(
    coordinator: ServiceCatalogStartupCoordinator,
    *,
    candidate_owner_id: str,
) -> PreparedServiceStart:
    """Functional facade for explicit composition roots."""

    if type(coordinator) is not ServiceCatalogStartupCoordinator:
        raise TypeError("coordinator must be ServiceCatalogStartupCoordinator")
    return coordinator.prepare_service_start(candidate_owner_id=candidate_owner_id)


def activate_candidate(
    coordinator: ServiceCatalogStartupCoordinator,
    prepared: PreparedServiceStart,
    *,
    start_candidate: Callable[[], Any],
    stop_candidate: Callable[[Any], None],
) -> ActivationResult:
    """Functional facade for explicit composition roots."""

    if type(coordinator) is not ServiceCatalogStartupCoordinator:
        raise TypeError("coordinator must be ServiceCatalogStartupCoordinator")
    return coordinator.activate_candidate(
        prepared,
        start_candidate=start_candidate,
        stop_candidate=stop_candidate,
    )


__all__ = [
    "ActivationResult",
    "DEFAULT_RESERVATION_TTL_MS",
    "HOST_SERVICE_CATALOG_MODE_ENV",
    "HOST_SERVICE_CATALOG_RESERVATION_TTL_MS_ENV",
    "MAX_RESERVATION_TTL_MS",
    "MIN_RESERVATION_TTL_MS",
    "MODE_AUDIT",
    "MODE_ENFORCE",
    "MODE_PREFLIGHT",
    "PreparedServiceStart",
    "ServiceCatalogStartupCoordinator",
    "activate_candidate",
    "prepare_service_start",
]
