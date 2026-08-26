"""Neutral error and validation contracts shared by governed Operator runtime layers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

GOVERNED_OPERATOR_RUNTIME_INVALID = "GOVERNED_OPERATOR_RUNTIME_INVALID"
GOVERNED_OPERATOR_GOVERNOR_REJECTED = "GOVERNED_OPERATOR_GOVERNOR_REJECTED"


class GovernedOperatorRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_OPERATOR_RUNTIME_INVALID)
        self.detail = str(detail or "governed Operator command is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _invalid_operator_runtime(detail: str) -> GovernedOperatorRuntimeError:
    return GovernedOperatorRuntimeError(
        GOVERNED_OPERATOR_RUNTIME_INVALID,
        detail,
    )


def _operator_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text.encode("utf-8")) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise _invalid_operator_runtime(f"{field} is invalid")
    return text


def _operator_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise _invalid_operator_runtime(f"{field} must be a positive integer")
    return value


def _operator_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise _invalid_operator_runtime(f"{field} must be a non-negative integer")
    return value


def _require_operator_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract and normalize the authority committed by Secure Ingress."""

    raw = payload.get("_operator_authority")
    authority = dict(raw) if isinstance(raw, Mapping) else {}
    required = (
        "request_id",
        "principal_id",
        "tenant_id",
        "claim_ref",
        "audit_ref",
        "body_sha256",
        "action",
        "ingress_surface",
        "method",
        "normalized_path",
        "operator_command_id",
    )
    if authority.get("allowed") is not True or any(
        not str(authority.get(key) or "").strip() for key in required
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "admissible Secure Ingress Operator authority is required",
        )
    body_sha = str(authority.get("body_sha256") or "").strip().lower()
    if body_sha.startswith("sha256:"):
        body_sha = body_sha.removeprefix("sha256:")
    if len(body_sha) != 64 or any(ch not in "0123456789abcdef" for ch in body_sha):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator body_sha256 is invalid",
        )
    return {
        **authority,
        "body_sha256": body_sha,
        "ingress_claim_ref": str(authority["claim_ref"]),
        "ingress_audit_ref": str(authority["audit_ref"]),
    }


__all__ = [
    "GOVERNED_OPERATOR_GOVERNOR_REJECTED",
    "GOVERNED_OPERATOR_RUNTIME_INVALID",
    "GovernedOperatorRuntimeError",
]
