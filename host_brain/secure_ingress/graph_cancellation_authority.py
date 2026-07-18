"""Production authority adapter for Operator TaskGraph cancellation.

The Operator Command Gateway authenticates a canonical command envelope.  This
adapter re-resolves its durable Secure Ingress audit/claim pair and binds it to
one exact Graph revision before the Task Runtime may install a cancellation
intent.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
import re
from typing import Any, Protocol

from host_brain.observability.command_ports import OBS_COMMAND_ENVELOPE_SCHEMA


TASK_GRAPH_CANCEL_COMMAND_ID = "task_graph.cancel"
TASK_GRAPH_CANCEL_ACTION = "obs:command:task_graph.cancel"
TASK_GRAPH_CANCEL_REQUIRED_SCOPES = ("obs:write", "task-runtime:cancel")
TASK_GRAPH_CANCEL_PATH = "/v1/host/obs-commands/task_graph.cancel"
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


class CommittedSecureIngressAuthorityResolverPort(Protocol):
    def resolve_committed_authority_by_audit(
        self,
        *,
        audit_ref: str,
    ) -> Mapping[str, Any] | None: ...


def canonical_task_graph_cancel_body(
    *,
    graph_id: str,
    graph_revision: int,
    reason_code: str,
) -> bytes:
    """Return the only raw OBS envelope accepted as cancellation authority."""

    graph_ref = str(graph_id or "").strip()
    reason = str(reason_code or "").strip().upper()
    if (
        not graph_ref
        or len(graph_ref) > 240
        or type(graph_revision) is not int
        or graph_revision < 1
        or _REASON_CODE.fullmatch(reason) is None
    ):
        raise ValueError("TASK_GRAPH_CANCELLATION_COMMAND_INVALID")
    return json.dumps(
        {
            "command_id": TASK_GRAPH_CANCEL_COMMAND_ID,
            "payload": {
                "graph_id": graph_ref,
                "graph_revision": graph_revision,
                "reason_code": reason,
            },
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class SecureIngressGraphCancellationAuthorityVerifier:
    """Verify a server-owned Operator authority against durable ingress facts."""

    def __init__(
        self,
        resolver: CommittedSecureIngressAuthorityResolverPort,
    ) -> None:
        if not callable(
            getattr(resolver, "resolve_committed_authority_by_audit", None)
        ):
            raise TypeError("committed Secure Ingress authority resolver is required")
        self._resolver = resolver

    def verify_graph_cancellation(
        self,
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any]:
        server_authority = dict(authority or {})
        graph_ref = str(graph_id or "").strip()
        reason = str(reason_code or "").strip().upper()
        if str(source or "").strip().upper() != "OPERATOR":
            raise ValueError("TASK_GRAPH_CANCELLATION_SOURCE_DENIED")
        expected_body = canonical_task_graph_cancel_body(
            graph_id=graph_ref,
            graph_revision=graph_revision,
            reason_code=reason,
        )
        expected_body_sha256 = hashlib.sha256(expected_body).hexdigest()
        audit_ref = _required(server_authority, "audit_ref")
        record_raw = self._resolver.resolve_committed_authority_by_audit(
            audit_ref=audit_ref,
        )
        if not isinstance(record_raw, Mapping):
            raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_NOT_DURABLE")
        record = dict(record_raw)
        if record.get("claim_result_ref") is not None:
            # Secure Ingress completes the claim only after the command port
            # has returned a terminal outcome.  Such an authority is no longer
            # an in-flight admission and cannot become a reusable bearer for a
            # new cancellation.  Public duplicates replay the original
            # outcome before this verifier is invoked.
            raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_ALREADY_SETTLED")

        authority_scopes = _scopes(server_authority.get("required_scopes"))
        durable_scopes = _scopes(record.get("required_scopes"))
        requested_scopes = _scopes(record.get("requested_scopes"))
        expected_scopes = tuple(sorted(TASK_GRAPH_CANCEL_REQUIRED_SCOPES))
        durable_matches = (
            server_authority.get("allowed") is True
            and server_authority.get("duplicate") is False
            and _required(server_authority, "claim_ref")
            == _required(record, "claim_ref")
            and _required(server_authority, "request_id")
            == _required(record, "request_id")
            and _required(server_authority, "principal_id")
            == _required(record, "principal_id")
            and _required(server_authority, "tenant_id")
            == _required(record, "tenant_id")
            and _required(server_authority, "body_sha256")
            == expected_body_sha256
            and _required(record, "body_sha256") == expected_body_sha256
            and _required(server_authority, "action") == TASK_GRAPH_CANCEL_ACTION
            and _required(record, "action") == TASK_GRAPH_CANCEL_ACTION
            and authority_scopes == expected_scopes
            and durable_scopes == expected_scopes
            and set(expected_scopes).issubset(requested_scopes)
            and _required(record, "audit_ref") == audit_ref
            and _required(record, "surface") == "obs.command"
            and _required(record, "method").upper() == "POST"
            and _required(record, "normalized_path") == TASK_GRAPH_CANCEL_PATH
            and _required(record, "decision").upper() == "ALLOW"
            and _required(record, "reason_code") == "SECURE_INGRESS_ALLOWED"
            and _required(record, "stage").upper() == "COMPLETE"
            and record.get("side_effects_executed") is False
            and _required(record, "claim_status").upper() == "COMMITTED"
        )
        if not durable_matches:
            raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_BINDING_DRIFT")
        # Avoid a future refactor accidentally changing equality semantics for
        # the authenticated body digest.
        if not hmac.compare_digest(
            _required(server_authority, "body_sha256"),
            _required(record, "body_sha256"),
        ):
            raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_BODY_DRIFT")
        return {
            "request_id": _required(record, "request_id"),
            "request_identity": audit_ref,
            "principal_id": _required(record, "principal_id"),
            "source": "OPERATOR",
            "reason_code": reason,
            "graph_id": graph_ref,
            "graph_revision": graph_revision,
        }


def _required(value: Mapping[str, Any], field: str) -> str:
    text = str(value.get(field) or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_INVALID")
    return text


def _scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("TASK_GRAPH_CANCELLATION_AUTHORITY_SCOPES_INVALID")
    return tuple(sorted(set(str(item).strip() for item in value)))


__all__ = [
    "CommittedSecureIngressAuthorityResolverPort",
    "SecureIngressGraphCancellationAuthorityVerifier",
    "TASK_GRAPH_CANCEL_ACTION",
    "TASK_GRAPH_CANCEL_COMMAND_ID",
    "TASK_GRAPH_CANCEL_PATH",
    "TASK_GRAPH_CANCEL_REQUIRED_SCOPES",
    "canonical_task_graph_cancel_body",
]
