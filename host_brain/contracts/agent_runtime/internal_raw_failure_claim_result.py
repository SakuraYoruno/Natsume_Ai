"""Deterministic Secure Ingress result for an exact Internal raw failure."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


INTERNAL_RAW_FAILURE_RESULT_REF_PREFIX = (
    "task-terminal://internal-raw-failure/sha256/"
)
_SCHEMA_VERSION = "Natsume.internal_raw_failure_claim_result.v1"
_REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,119}$")


def build_internal_raw_failure_result_ref(
    *,
    command_id: str,
    ingress_claim_ref: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    lease_id: str,
    reservation_id: str,
    reconciliation_proof_ref: str,
    reconciliation_proof_digest: str,
    failure_reason_code: str,
    error_ref: str,
) -> str:
    """Bind one signed no-side-effect RELEASE to its failed Task lineage."""

    reason = _required_text(
        failure_reason_code,
        field_name="failure_reason_code",
    ).upper()
    if not _REASON_PATTERN.fullmatch(reason):
        raise ValueError("failure_reason_code is invalid")
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "command_id": _required_text(
            command_id,
            field_name="command_id",
        ),
        "ingress_claim_ref": _required_text(
            ingress_claim_ref,
            field_name="ingress_claim_ref",
        ),
        "graph_id": _required_text(graph_id, field_name="graph_id"),
        "graph_revision": _positive_int(
            graph_revision,
            field_name="graph_revision",
        ),
        "node_id": _required_text(node_id, field_name="node_id"),
        "attempt_id": _required_text(
            attempt_id,
            field_name="attempt_id",
        ),
        "lease_id": _required_text(lease_id, field_name="lease_id"),
        "reservation_id": _required_text(
            reservation_id,
            field_name="reservation_id",
        ),
        "reconciliation_proof_ref": _required_text(
            reconciliation_proof_ref,
            field_name="reconciliation_proof_ref",
        ),
        "reconciliation_proof_digest": _sha256_digest(
            reconciliation_proof_digest,
            field_name="reconciliation_proof_digest",
        ),
        "failure_reason_code": reason,
        "error_ref": _required_text(error_ref, field_name="error_ref"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return (
        INTERNAL_RAW_FAILURE_RESULT_REF_PREFIX
        + hashlib.sha256(encoded).hexdigest()
    )


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ValueError(f"{field_name} is invalid")
    text = value.strip()
    if not text or len(text) > 1_024:
        raise ValueError(f"{field_name} is invalid")
    return text


def _sha256_digest(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name).lower()
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in text[7:])
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return text


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


__all__ = [
    "INTERNAL_RAW_FAILURE_RESULT_REF_PREFIX",
    "build_internal_raw_failure_result_ref",
]
