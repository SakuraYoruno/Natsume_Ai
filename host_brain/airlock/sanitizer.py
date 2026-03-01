"""AIRLOCK egress sanitization and report enforcement."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import AIRLOCK_BLOCKED_EGRESS
from host_brain.core.ids import now_ms


DEFAULT_REMOVED_FIELDS = frozenset(
    {
        "hard_state",
        "governor_state",
        "governor_snapshot",
        "rule_trace",
        "tokens",
        "snapshot_refs",
        "constitution_full_text",
        "constitution_text",
        "internal_policies",
    }
)

DEFAULT_REDACTED_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "session_id",
        "nonce",
    }
)


@dataclass(frozen=True)
class EgressSanitization:
    """Result of payload sanitization for outbound transmission."""

    sanitized_payload: dict[str, Any]
    sanitization_report_json: dict[str, Any]


@dataclass(frozen=True)
class EgressGateResult:
    """Decision returned by AIRLOCK egress gate."""

    allowed: bool
    reason_code: str | None
    payload: dict[str, Any]
    sanitization_report_json: dict[str, Any] | None
    detail: str


def build_sanitization_report(
    payload: dict[str, Any],
    *,
    remove_fields: set[str] | None = None,
    redact_fields: set[str] | None = None,
) -> EgressSanitization:
    """Sanitize payload and produce a structured report."""

    remove_keys = {x.strip().lower() for x in (remove_fields or DEFAULT_REMOVED_FIELDS) if str(x).strip()}
    redact_keys = {x.strip().lower() for x in (redact_fields or DEFAULT_REDACTED_FIELDS) if str(x).strip()}
    removed_paths: list[str] = []
    redaction_paths: list[str] = []

    sanitized = _sanitize_node(
        value=payload,
        path="",
        remove_keys=remove_keys,
        redact_keys=redact_keys,
        removed_paths=removed_paths,
        redaction_paths=redaction_paths,
    )
    assert isinstance(sanitized, dict)

    report = {
        "removed_fields": _dedupe(removed_paths),
        "redactions": _dedupe(redaction_paths),
        "payload_digest": _digest_json(sanitized),
    }
    return EgressSanitization(sanitized_payload=sanitized, sanitization_report_json=report)


def gate_egress_payload(
    payload: dict[str, Any],
    *,
    sanitization_report_json: dict[str, Any] | None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> EgressGateResult:
    """Enforce that outbound payload carries a valid sanitization report."""

    payload_digest = _digest_json(payload)
    report_valid, detail = _validate_report(sanitization_report_json, payload_digest=payload_digest)
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    if not report_valid:
        if ledger is not None:
            ledger.log_airlock(
                {
                    "request_id": request_id,
                    "direction": "EGRESS",
                    "reason_code": AIRLOCK_BLOCKED_EGRESS,
                    "removed_fields": [],
                    "redactions": [],
                    "payload_digest": payload_digest,
                    "quarantine_digest": None,
                    "ingress_classification": None,
                    "blocked": 1,
                    "detail": detail,
                    "ts_ms": now_value,
                }
            )
        return EgressGateResult(
            allowed=False,
            reason_code=AIRLOCK_BLOCKED_EGRESS,
            payload=payload,
            sanitization_report_json=sanitization_report_json,
            detail=detail,
        )

    report = sanitization_report_json or {}
    if ledger is not None:
        ledger.log_airlock(
            {
                "request_id": request_id,
                "direction": "EGRESS",
                "reason_code": None,
                "removed_fields": report.get("removed_fields") or [],
                "redactions": report.get("redactions") or [],
                "payload_digest": report.get("payload_digest"),
                "quarantine_digest": None,
                "ingress_classification": None,
                "blocked": 0,
                "detail": "egress allowed",
                "ts_ms": now_value,
            }
        )

    return EgressGateResult(
        allowed=True,
        reason_code=None,
        payload=payload,
        sanitization_report_json=report,
        detail="egress allowed",
    )


def sanitize_and_gate_egress(
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
    remove_fields: set[str] | None = None,
    redact_fields: set[str] | None = None,
) -> EgressGateResult:
    """Convenience helper used by outbound gateways."""

    result = build_sanitization_report(
        payload=payload,
        remove_fields=remove_fields,
        redact_fields=redact_fields,
    )
    return gate_egress_payload(
        payload=result.sanitized_payload,
        sanitization_report_json=result.sanitization_report_json,
        request_id=request_id,
        ledger=ledger,
        ts_ms=ts_ms,
    )


def _validate_report(report: dict[str, Any] | None, *, payload_digest: str) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "missing sanitization_report_json"

    removed_fields = report.get("removed_fields")
    redactions = report.get("redactions")
    report_digest = report.get("payload_digest")

    if not isinstance(removed_fields, list):
        return False, "sanitization report missing removed_fields list"
    if not isinstance(redactions, list):
        return False, "sanitization report missing redactions list"
    if not isinstance(report_digest, str) or not report_digest:
        return False, "sanitization report missing payload_digest"
    if report_digest != payload_digest:
        return False, "sanitization report payload_digest mismatch"

    return True, "ok"


def _sanitize_node(
    *,
    value: Any,
    path: str,
    remove_keys: set[str],
    redact_keys: set[str],
    removed_paths: list[str],
    redaction_paths: list[str],
) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            key_name = str(key)
            key_lower = key_name.lower()
            child_path = f"{path}.{key_name}" if path else key_name
            if key_lower in remove_keys:
                removed_paths.append(child_path)
                continue
            if key_lower in redact_keys:
                redaction_paths.append(child_path)
                out[key_name] = "[REDACTED]"
                continue
            out[key_name] = _sanitize_node(
                value=child,
                path=child_path,
                remove_keys=remove_keys,
                redact_keys=redact_keys,
                removed_paths=removed_paths,
                redaction_paths=redaction_paths,
            )
        return out

    if isinstance(value, list):
        out_list: list[Any] = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            out_list.append(
                _sanitize_node(
                    value=item,
                    path=child_path,
                    remove_keys=remove_keys,
                    redact_keys=redact_keys,
                    removed_paths=removed_paths,
                    redaction_paths=redaction_paths,
                )
            )
        return out_list

    return value


def _digest_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
