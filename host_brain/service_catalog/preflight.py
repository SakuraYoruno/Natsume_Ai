"""Deterministic validation and startup preflight for service endpoints.

The gate is intentionally policy-only.  It observes configured claims and can
request a reservation from an injected provider, but it cannot kill or adopt a
process, mutate a port, consult DNS, or turn an HTTP status code into service
identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
from typing import Any, Iterable, Mapping, Sequence

from host_brain.service_catalog.models import (
    AuthKind,
    CatalogIssue,
    CatalogMode,
    CatalogSnapshot,
    EndpointKind,
    PreflightDecision,
)

from host_brain.service_catalog.probes import (
    BindProbe,
    BindProbeResult,
    HealthProbe,
    HealthProbeResult,
    ReservationAttestation,
    strict_socket_addresses,
)

MODE_AUDIT = CatalogMode.AUDIT.value
MODE_SHADOW = CatalogMode.SHADOW.value
MODE_PREFLIGHT = CatalogMode.PREFLIGHT.value
MODE_CANARY = CatalogMode.CANARY.value
MODE_ENFORCE = CatalogMode.ENFORCE.value
_MODES = frozenset(CatalogMode)

ISSUE_ENDPOINT_ID_REQUIRED = "SERVICE_CATALOG_ENDPOINT_ID_REQUIRED"
ISSUE_DUPLICATE_ENDPOINT_ID = "SERVICE_CATALOG_DUPLICATE_ENDPOINT_ID"
ISSUE_OWNER_REQUIRED = "SERVICE_CATALOG_OWNER_REQUIRED"
ISSUE_PROTOCOL_REQUIRED = "SERVICE_CATALOG_PROTOCOL_REQUIRED"
ISSUE_HEALTH_REQUIRED = "SERVICE_CATALOG_HEALTH_REQUIRED"
ISSUE_LISTENER_REQUIRED = "SERVICE_CATALOG_LISTENER_REQUIRED"
ISSUE_BIND_INVALID = "SERVICE_CATALOG_BIND_INVALID"
ISSUE_PUBLIC_AUTH_REQUIRED = "SERVICE_CATALOG_PUBLIC_BIND_AUTH_REQUIRED"
ISSUE_LISTENER_CONFLICT = "SERVICE_CATALOG_BIND_CONFLICT"
ISSUE_BIND_PROBE_FAILED = "SERVICE_CATALOG_BIND_PROBE_FAILED"
ISSUE_BIND_UNAVAILABLE = "SERVICE_CATALOG_BIND_UNAVAILABLE"
ISSUE_HEALTH_IDENTITY_MISMATCH = "SERVICE_CATALOG_HEALTH_IDENTITY_MISMATCH"
ISSUE_RESERVATION_REQUIRED = "SERVICE_CATALOG_RESERVATION_HANDOFF_REQUIRED"
ISSUE_MODE_INVALID = "SERVICE_CATALOG_MODE_INVALID"
ISSUE_CLAIM_SELECTION_INVALID = "SERVICE_CATALOG_CLAIM_SELECTION_INVALID"


@dataclass(frozen=True, slots=True)
class _Claim:
    endpoint: Any
    endpoint_id: str
    owner_id: str
    listener_id: str
    bind_host: str
    port: int
    transport: str
    protocol_id: str
    protocol_version: str


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _first_attr(value: object, names: Sequence[str], default: object = "") -> object:
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _endpoints(snapshot: object) -> tuple[Any, ...]:
    value = _first_attr(snapshot, ("endpoints", "entries"), ())
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return ()
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _endpoint_id(endpoint: object) -> str:
    return str(_first_attr(endpoint, ("endpoint_id", "id"), "") or "").strip()


def _owner_id(endpoint: object) -> str:
    return str(_first_attr(endpoint, ("owner_id", "owner"), "") or "").strip()


def _listener_id(endpoint: object) -> str:
    return str(_first_attr(endpoint, ("listener_id",), "") or "").strip()


def _bind_host(endpoint: object) -> str:
    return str(_first_attr(endpoint, ("bind_host", "host"), "") or "").strip()


def _transport(endpoint: object) -> str:
    return _enum_value(_first_attr(endpoint, ("transport",), "")).lower()


def _protocol_id(endpoint: object) -> str:
    protocols = _first_attr(endpoint, ("protocols",), ())
    try:
        primary = tuple(protocols)[0]  # type: ignore[arg-type]
    except (TypeError, IndexError):
        primary = None
    if primary is not None:
        return str(_first_attr(primary, ("name", "protocol_id"), "") or "").strip()
    return str(_first_attr(endpoint, ("protocol_id", "protocol"), "") or "").strip()


def _protocol_version(endpoint: object) -> str:
    protocols = _first_attr(endpoint, ("protocols",), ())
    try:
        primary = tuple(protocols)[0]  # type: ignore[arg-type]
    except (TypeError, IndexError):
        primary = None
    if primary is not None:
        return str(_first_attr(primary, ("version", "protocol_version"), "") or "").strip()
    return str(_first_attr(endpoint, ("protocol_version",), "") or "").strip()


def _protocol_pairs(endpoint: object) -> frozenset[tuple[str, str]]:
    protocols = _first_attr(endpoint, ("protocols",), ())
    try:
        return frozenset(
            (
                str(_first_attr(item, ("name", "protocol_id"), "") or "").strip(),
                str(_first_attr(item, ("version", "protocol_version"), "") or "").strip(),
            )
            for item in protocols  # type: ignore[union-attr]
        )
    except TypeError:
        return frozenset()


def _health_identity_is_bound(endpoint: object) -> bool:
    health = _first_attr(endpoint, ("health", "health_spec", "health_check"), None)
    if health is None:
        return False
    owner = str(_first_attr(health, ("expected_owner_id",), "") or "").strip()
    protocol = str(_first_attr(health, ("expected_protocol",), "") or "").strip()
    version = str(
        _first_attr(health, ("expected_protocol_version",), "") or ""
    ).strip()
    # Legacy declarations without typed identity fields are validated by their
    # adapter; the v1 catalog HealthSpec must bind all three values exactly.
    if not any(hasattr(health, field) for field in (
        "expected_owner_id",
        "expected_protocol",
        "expected_protocol_version",
    )):
        return True
    return bool(
        owner
        and owner == _owner_id(endpoint)
        and protocol
        and version
        and (protocol, version) in _protocol_pairs(endpoint)
    )


def _health_declared(endpoint: object) -> bool:
    value = _first_attr(
        endpoint,
        (
            "health",
            "health_spec",
            "health_check",
            "health_endpoint",
            "health_path",
            "health_uri",
        ),
        None,
    )
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    # Typed HealthSpec instances are declarations even when they use a non-HTTP
    # check and therefore have no path.
    return True


def _claim_kind(endpoint: object) -> str:
    value = _first_attr(endpoint, ("claim_kind", "endpoint_kind", "kind"), "static")
    return _enum_value(value).lower() or "static"


def _claims_listener(endpoint: object) -> bool:
    if not bool(_first_attr(endpoint, ("enabled",), True)):
        return False
    kind = _claim_kind(endpoint)
    if kind in {
        EndpointKind.DEPENDENCY.value.lower(),
        EndpointKind.DYNAMIC_TEMPLATE.value.lower(),
        "dynamic",
        "external",
        "client",
        "none",
    }:
        return False
    return True


def _auth_policy_complete(endpoint: object) -> bool:
    auth_kind = _enum_value(_first_attr(endpoint, ("auth_kind",), "")).upper()
    if auth_kind:
        policy_ref = str(_first_attr(endpoint, ("auth_policy_ref",), "") or "").strip()
        authenticated_kinds = {
            AuthKind.HMAC.value,
            AuthKind.TOKEN.value,
            AuthKind.MTLS.value,
            AuthKind.PLATFORM_SIGNATURE.value,
            AuthKind.ROUTE_POLICY.value,
        }
        return auth_kind in authenticated_kinds and bool(policy_ref)
    policy = _first_attr(endpoint, ("auth_policy", "authentication", "auth"), None)
    if policy is None:
        return False
    if isinstance(policy, str):
        return policy.strip().lower() not in {"", "none", "anonymous", "disabled", "off"}
    if isinstance(policy, Mapping):
        required = policy.get("required", policy.get("authenticated", policy.get("enabled", False)))
        mechanism = policy.get("mechanism", policy.get("scheme", policy.get("kind", "")))
        scopes = policy.get("required_scopes", policy.get("scopes", ()))
        audit = policy.get("audit_required", policy.get("audit", False))
    else:
        required = _first_attr(policy, ("required", "authenticated", "enabled"), False)
        mechanism = _first_attr(policy, ("mechanism", "scheme", "kind"), "")
        scopes = _first_attr(policy, ("required_scopes", "scopes"), ())
        audit = _first_attr(policy, ("audit_required", "audit"), False)
    mechanism_value = _enum_value(mechanism).lower()
    if mechanism_value in {"", "none", "anonymous", "disabled", "off"}:
        return False
    if isinstance(scopes, str):
        scopes_ok = bool(scopes.strip())
    else:
        try:
            scopes_ok = bool(tuple(scopes))
        except TypeError:
            scopes_ok = False
    return bool(required) and scopes_ok and bool(audit)


def _host_tokens(host: str) -> frozenset[str]:
    """Normalize a bind into conservative IPv4/IPv6 overlap tokens."""

    raw = host.strip()
    addresses = strict_socket_addresses(raw)
    tokens: set[str] = set()
    lowered = raw.lower()
    if lowered == "*":
        return frozenset({"v4:any", "v6:any", "dual:any"})
    for family, address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.version == 4:
            tokens.add("v4:any" if parsed.is_unspecified else f"v4:{parsed.compressed}")
        else:
            # An unspecified IPv6 socket may also accept IPv4-mapped traffic on
            # platforms where IPV6_V6ONLY is false.  Catalog validation cannot
            # depend on that ambient setting, so it conservatively overlaps both.
            if parsed.ipv4_mapped is not None:
                tokens.add(f"v4:{parsed.ipv4_mapped.compressed}")
                tokens.add(f"v6:{parsed.compressed}")
            elif parsed.is_unspecified:
                tokens.update({"v6:any", "dual:any"})
            else:
                tokens.add(f"v6:{parsed.compressed}")
    return frozenset(tokens)


def _hosts_overlap(left: str, right: str) -> bool:
    left_tokens = _host_tokens(left)
    right_tokens = _host_tokens(right)
    if "dual:any" in left_tokens or "dual:any" in right_tokens:
        return True
    left_v4 = {item for item in left_tokens if item.startswith("v4:")}
    right_v4 = {item for item in right_tokens if item.startswith("v4:")}
    if left_v4 and right_v4:
        if "v4:any" in left_v4 or "v4:any" in right_v4 or left_v4 & right_v4:
            return True
    left_v6 = {item for item in left_tokens if item.startswith("v6:")}
    right_v6 = {item for item in right_tokens if item.startswith("v6:")}
    return bool(
        left_v6
        and right_v6
        and ("v6:any" in left_v6 or "v6:any" in right_v6 or left_v6 & right_v6)
    )


def _is_public_or_wildcard(host: str) -> bool:
    for _family, address in strict_socket_addresses(host):
        parsed = ipaddress.ip_address(address)
        if parsed.is_unspecified or not parsed.is_loopback:
            return True
    return False


def _canonical_endpoint(endpoint: object) -> dict[str, object]:
    auth = _first_attr(endpoint, ("auth_policy", "authentication", "auth"), None)
    return {
        "endpoint_id": _endpoint_id(endpoint),
        "owner_id": _owner_id(endpoint),
        "listener_id": _listener_id(endpoint),
        "bind_host": _bind_host(endpoint),
        "port": _first_attr(endpoint, ("port",), None),
        "transport": _transport(endpoint),
        "protocol_id": _protocol_id(endpoint),
        "protocol_version": _protocol_version(endpoint),
        "claim_kind": _claim_kind(endpoint),
        "health": repr(
            _first_attr(
                endpoint,
                (
                    "health",
                    "health_spec",
                    "health_check",
                    "health_endpoint",
                    "health_path",
                    "health_uri",
                ),
                None,
            )
        ),
        "auth": repr(auth),
    }


def _snapshot_digest(snapshot: object, endpoints: Sequence[object]) -> str:
    for name in ("digest", "snapshot_digest", "content_digest", "catalog_digest"):
        value = getattr(snapshot, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    digest_method = getattr(snapshot, "calculate_digest", None)
    if callable(digest_method):
        value = digest_method()
        if isinstance(value, str) and value.strip():
            return value.strip()
    payload = [_canonical_endpoint(endpoint) for endpoint in endpoints]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _make_claim(endpoint: object) -> _Claim:
    health = _first_attr(endpoint, ("health", "health_spec", "health_check"), None)
    return _Claim(
        endpoint=endpoint,
        endpoint_id=_endpoint_id(endpoint),
        owner_id=_owner_id(endpoint),
        listener_id=_listener_id(endpoint),
        bind_host=_bind_host(endpoint),
        port=int(_first_attr(endpoint, ("port",), 0)),
        transport=_transport(endpoint),
        protocol_id=str(
            _first_attr(health, ("expected_protocol",), _protocol_id(endpoint)) or ""
        ).strip(),
        protocol_version=str(
            _first_attr(
                health,
                ("expected_protocol_version",),
                _protocol_version(endpoint),
            )
            or ""
        ).strip(),
    )


def _issue(
    reason_code: str,
    endpoint_ids: Iterable[str],
    detail: str,
) -> CatalogIssue:
    return CatalogIssue(
        reason_code=reason_code,
        endpoint_ids=tuple(sorted({item for item in endpoint_ids if item})),
        detail=detail,
    )


def _sort_issues(issues: Iterable[CatalogIssue]) -> tuple[CatalogIssue, ...]:
    unique: dict[tuple[str, tuple[str, ...], str], CatalogIssue] = {}
    for issue in issues:
        unique[(issue.reason_code, issue.endpoint_ids, issue.detail)] = issue
    return tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[0], item[1], item[2]))
    )


def validate_catalog(snapshot: CatalogSnapshot | object) -> tuple[CatalogIssue, ...]:
    """Validate ownership and listener claims without observing the machine."""

    endpoints = _endpoints(snapshot)
    issues: list[CatalogIssue] = []
    seen_ids: set[str] = set()
    claims: list[_Claim] = []

    for endpoint in endpoints:
        endpoint_id = _endpoint_id(endpoint)
        issue_ids = (endpoint_id,) if endpoint_id else ()
        if not endpoint_id:
            issues.append(_issue(ISSUE_ENDPOINT_ID_REQUIRED, (), "endpoint_id is required"))
        elif endpoint_id in seen_ids:
            issues.append(
                _issue(ISSUE_DUPLICATE_ENDPOINT_ID, (endpoint_id,), "endpoint_id must be unique")
            )
        seen_ids.add(endpoint_id)

        if not _owner_id(endpoint):
            issues.append(_issue(ISSUE_OWNER_REQUIRED, issue_ids, "owner_id is required"))
        if not _protocol_id(endpoint) or not _protocol_version(endpoint):
            issues.append(
                _issue(
                    ISSUE_PROTOCOL_REQUIRED,
                    issue_ids,
                    "protocol_id and protocol_version are required",
                )
            )
        if not _health_declared(endpoint):
            issues.append(_issue(ISSUE_HEALTH_REQUIRED, issue_ids, "health declaration is required"))
        elif not _health_identity_is_bound(endpoint):
            issues.append(
                _issue(
                    ISSUE_HEALTH_IDENTITY_MISMATCH,
                    issue_ids,
                    "health identity must bind the endpoint owner and a declared protocol version",
                )
            )

        if not _claims_listener(endpoint):
            continue
        if not _listener_id(endpoint):
            issues.append(_issue(ISSUE_LISTENER_REQUIRED, issue_ids, "listener_id is required"))
        try:
            port = int(_first_attr(endpoint, ("port",), 0))
            if not 1 <= port <= 65535:
                raise ValueError("port out of range")
            if _transport(endpoint) not in {"tcp", "udp"}:
                raise ValueError("transport must be TCP or UDP")
            strict_socket_addresses(_bind_host(endpoint))
        except (TypeError, ValueError, AttributeError) as exc:
            issues.append(_issue(ISSUE_BIND_INVALID, issue_ids, f"invalid bind claim: {exc}"))
            continue
        if _is_public_or_wildcard(_bind_host(endpoint)) and not _auth_policy_complete(endpoint):
            issues.append(
                _issue(
                    ISSUE_PUBLIC_AUTH_REQUIRED,
                    issue_ids,
                    "public or wildcard bind requires authentication, scope and audit policy",
                )
            )
        claims.append(_make_claim(endpoint))

    for index, left in enumerate(claims):
        for right in claims[index + 1 :]:
            if left.transport != right.transport or left.port != right.port:
                continue
            if not _hosts_overlap(left.bind_host, right.bind_host):
                continue
            if left.owner_id == right.owner_id and left.listener_id == right.listener_id:
                continue
            issues.append(
                _issue(
                    ISSUE_LISTENER_CONFLICT,
                    (left.endpoint_id, right.endpoint_id),
                    "overlapping listener claim requires the same owner_id and listener_id",
                )
            )

    return _sort_issues(issues)


def _normalize_mode(mode: object) -> tuple[CatalogMode, CatalogIssue | None]:
    if isinstance(mode, CatalogMode):
        return mode, None
    value = _enum_value(mode).upper()
    try:
        return CatalogMode(value), None
    except ValueError:
        pass
    return CatalogMode.PREFLIGHT, _issue(
        ISSUE_MODE_INVALID,
        (),
        "mode must be AUDIT, SHADOW, PREFLIGHT, CANARY or ENFORCE",
    )


def _safe_bind_inspect(bind_probe: BindProbe, claim: _Claim) -> BindProbeResult:
    try:
        result = bind_probe.inspect(claim.endpoint)
    except Exception as exc:  # provider failures are stable fail-closed observations
        return BindProbeResult(
            endpoint_id=claim.endpoint_id,
            available=False,
            detail=f"bind probe failed: {type(exc).__name__}",
        )
    if not isinstance(result, BindProbeResult):
        return BindProbeResult(
            endpoint_id=claim.endpoint_id,
            available=False,
            detail="bind probe returned an invalid result",
        )
    if result.endpoint_id != claim.endpoint_id or type(result.available) is not bool:
        return BindProbeResult(
            endpoint_id=claim.endpoint_id,
            available=False,
            detail="bind probe returned an invalid endpoint or availability value",
        )
    return result


def _safe_health_inspect(
    health_probe: HealthProbe | None,
    claim: _Claim,
) -> HealthProbeResult | None:
    if health_probe is None:
        return None
    try:
        result = health_probe.inspect(claim.endpoint)
    except Exception as exc:
        return HealthProbeResult(
            endpoint_id=claim.endpoint_id,
            healthy=False,
            detail=f"health probe failed: {type(exc).__name__}",
        )
    if not isinstance(result, HealthProbeResult):
        return HealthProbeResult(
            endpoint_id=claim.endpoint_id,
            healthy=False,
            detail="health probe returned an invalid result",
        )
    return result


def _identity_matches(claim: _Claim, health: HealthProbeResult | None) -> bool:
    return bool(
        health is not None
        and health.endpoint_id == claim.endpoint_id
        and health.healthy is True
        and health.owner_id == claim.owner_id
        and health.protocol_id == claim.protocol_id
        and health.protocol_version == claim.protocol_version
    )


def _reservation_valid(
    attestation: ReservationAttestation | None,
    *,
    digest: str,
    endpoint_ids: tuple[str, ...],
    now_ms: int,
    ttl_ms: int,
) -> bool:
    if type(attestation) is not ReservationAttestation:
        return False
    assert attestation is not None
    if (
        not isinstance(attestation.reservation_ref, str)
        or not isinstance(attestation.snapshot_digest, str)
        or not isinstance(attestation.endpoint_ids, tuple)
        or any(not isinstance(item, str) or not item for item in attestation.endpoint_ids)
        or type(attestation.issued_at_ms) is not int
        or type(attestation.expires_at_ms) is not int
        or type(attestation.consumable) is not bool
    ):
        return False
    return bool(
        attestation.consumable
        and attestation.reservation_ref.strip()
        and attestation.snapshot_digest == digest
        and tuple(sorted(attestation.endpoint_ids)) == endpoint_ids
        and attestation.issued_at_ms <= now_ms < attestation.expires_at_ms
        and attestation.expires_at_ms - attestation.issued_at_ms <= ttl_ms
        and attestation.expires_at_ms - attestation.issued_at_ms > 0
    )


def preflight_catalog(
    snapshot: CatalogSnapshot,
    *,
    mode: object,
    bind_probe: BindProbe,
    health_probe: HealthProbe | None = None,
    now_ms: int,
    reservation_ttl_ms: int = 5_000,
    claim_endpoint_ids: Iterable[str] | None = None,
) -> PreflightDecision:
    """Observe fixed listener claims and apply the selected startup gate mode."""

    if type(snapshot) is not CatalogSnapshot:
        raise TypeError("startup preflight requires a typed CatalogSnapshot")
    if bind_probe is None:
        raise TypeError("bind_probe must be injected")
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative int")

    normalized_mode, mode_issue = _normalize_mode(mode)
    endpoints = _endpoints(snapshot)
    snapshot_digest = _snapshot_digest(snapshot, endpoints)
    issues = list(validate_catalog(snapshot))
    if mode_issue is not None:
        issues.append(mode_issue)
    claims: list[_Claim] = []
    selected_ids: frozenset[str] | None = None
    if claim_endpoint_ids is not None:
        try:
            requested = tuple(str(item or "").strip() for item in claim_endpoint_ids)
        except TypeError:
            requested = ()
        selected_ids = frozenset(item for item in requested if item)
        eligible_ids = frozenset(
            _endpoint_id(endpoint)
            for endpoint in endpoints
            if _claims_listener(endpoint)
        )
        if (
            not selected_ids
            or len(selected_ids) != len(requested)
            or not selected_ids.issubset(eligible_ids)
        ):
            issues.append(
                _issue(
                    ISSUE_CLAIM_SELECTION_INVALID,
                    selected_ids,
                    "claim selection must name one or more enabled typed listeners",
                )
            )

    for endpoint in endpoints:
        if not _claims_listener(endpoint):
            continue
        if selected_ids is not None and _endpoint_id(endpoint) not in selected_ids:
            continue
        try:
            claim = _make_claim(endpoint)
            strict_socket_addresses(claim.bind_host)
            if claim.transport not in {"tcp", "udp"} or not 1 <= claim.port <= 65535:
                continue
        except (TypeError, ValueError, AttributeError):
            continue
        claims.append(claim)
        bind = _safe_bind_inspect(bind_probe, claim)
        health = None
        if not bind.available:
            health = _safe_health_inspect(health_probe, claim)
            # Even exact incumbent identity remains occupied.  Reporting it is
            # useful, but treating it as startup authority would silently adopt
            # a process that this gate did not create.
            if health is not None and not _identity_matches(claim, health):
                issues.append(
                    _issue(
                        ISSUE_HEALTH_IDENTITY_MISMATCH,
                        (claim.endpoint_id,),
                        "healthy/status response does not prove the configured owner and protocol",
                    )
                )
            reason = ISSUE_BIND_PROBE_FAILED if bind.detail.startswith("bind probe failed") else ISSUE_BIND_UNAVAILABLE
            issues.append(
                _issue(
                    reason,
                    (claim.endpoint_id,),
                    bind.detail or "configured listener is occupied",
                )
            )

    reservation: ReservationAttestation | None = None
    sorted_issues = _sort_issues(issues)
    if normalized_mode in {CatalogMode.CANARY, CatalogMode.ENFORCE} and not sorted_issues and claims:
        ttl = int(reservation_ttl_ms)
        endpoint_ids = tuple(sorted({claim.endpoint_id for claim in claims}))
        if ttl <= 0 or ttl > 30_000:
            issues.append(
                _issue(
                    ISSUE_RESERVATION_REQUIRED,
                    endpoint_ids,
                    "reservation TTL must be in the short-lived 1..30000ms range",
                )
            )
        else:
            try:
                candidate = bind_probe.reserve(
                    tuple(claim.endpoint for claim in claims),
                    snapshot_digest=snapshot_digest,
                    now_ms=int(now_ms),
                    ttl_ms=ttl,
                )
            except Exception:
                candidate = None
            if _reservation_valid(
                candidate,
                digest=snapshot_digest,
                endpoint_ids=endpoint_ids,
                now_ms=int(now_ms),
                ttl_ms=ttl,
            ):
                reservation = candidate
            else:
                issues.append(
                    _issue(
                        ISSUE_RESERVATION_REQUIRED,
                        endpoint_ids,
                        f"{normalized_mode.value} requires an exact, consumable short-lived reservation",
                    )
                )

    sorted_issues = _sort_issues(issues)
    if normalized_mode in {CatalogMode.AUDIT, CatalogMode.SHADOW}:
        sorted_issues = tuple(
            CatalogIssue(
                reason_code=issue.reason_code,
                endpoint_ids=issue.endpoint_ids,
                detail=issue.detail,
                socket_ref=issue.socket_ref,
                blocking=False,
            )
            for issue in sorted_issues
        )
    # AUDIT is observation only. SHADOW permits the legacy candidate start while
    # retaining every issue as non-blocking evidence. The three stricter stages
    # grant start authority only after their complete gate passes.
    start_allowed = (
        normalized_mode is CatalogMode.SHADOW
        or (
            normalized_mode
            in {CatalogMode.PREFLIGHT, CatalogMode.CANARY, CatalogMode.ENFORCE}
            and not sorted_issues
        )
    )
    return PreflightDecision(
        mode=normalized_mode,
        snapshot_digest=snapshot_digest,
        start_allowed=start_allowed,
        issues=sorted_issues,
        reservation_ref=(reservation.reservation_ref if reservation is not None else None),
        issued_at_ms=(reservation.issued_at_ms if reservation is not None else None),
        expires_at_ms=(reservation.expires_at_ms if reservation is not None else None),
        reservation=reservation,
    )


__all__ = [
    "ISSUE_BIND_INVALID",
    "ISSUE_BIND_PROBE_FAILED",
    "ISSUE_BIND_UNAVAILABLE",
    "ISSUE_CLAIM_SELECTION_INVALID",
    "ISSUE_DUPLICATE_ENDPOINT_ID",
    "ISSUE_ENDPOINT_ID_REQUIRED",
    "ISSUE_HEALTH_IDENTITY_MISMATCH",
    "ISSUE_HEALTH_REQUIRED",
    "ISSUE_LISTENER_CONFLICT",
    "ISSUE_LISTENER_REQUIRED",
    "ISSUE_MODE_INVALID",
    "ISSUE_OWNER_REQUIRED",
    "ISSUE_PROTOCOL_REQUIRED",
    "ISSUE_PUBLIC_AUTH_REQUIRED",
    "ISSUE_RESERVATION_REQUIRED",
    "MODE_AUDIT",
    "MODE_ENFORCE",
    "MODE_PREFLIGHT",
    "preflight_catalog",
    "validate_catalog",
]
