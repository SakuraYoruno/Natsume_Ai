"""Immutable policy for one production-governed Delegate target."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any


GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION = (
    "Natsume.host.governed_delegate_target_policy.v1"
)
GOVERNED_DELEGATE_EXECUTION_MODE = "PORT_FREE_READ_ONLY"
GOVERNED_DELEGATE_REVIEW_MODE = "DURABLE_PRE_EXECUTION_REQUIRED"
GOVERNED_DELEGATE_BUDGET_MODE = "PORT_FREE_READ_ONLY_ATTESTED_ZERO"
GOVERNED_DELEGATE_EFFECT_MODE = "GRAPH_ACCEPTANCE_RESULT_ONLY"
GOVERNED_DELEGATE_POLICY_INVALID = "GOVERNED_DELEGATE_POLICY_INVALID"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_POLICY_REF_RE = re.compile(
    r"^governed-delegate-policy://sha256/(?P<digest>[0-9a-f]{64})$"
)
_BASE_KEYS = frozenset(
    {
        "schema_version",
        "target_id",
        "adapter_id",
        "execution_mode",
        "review_mode",
        "budget_mode",
        "effect_mode",
    }
)


class GovernedDelegateTargetPolicyError(ValueError):
    """Stable fail-closed error for a missing or drifted target policy."""

    def __init__(self, detail: str) -> None:
        self.reason_code = GOVERNED_DELEGATE_POLICY_INVALID
        self.detail = str(detail or "governed Delegate target policy is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def freeze_governed_delegate_target_policy(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and content-address one target policy."""

    base = _normalized_base(payload)
    return {
        **base,
        "policy_ref": (
            "governed-delegate-policy://sha256/" + _sha256_json(base)
        ),
    }


def validate_governed_delegate_target_policy(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen one persisted policy and verify its exact content address."""

    raw = _mapping(payload)
    if set(raw) != {*_BASE_KEYS, "policy_ref"}:
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy has missing or unknown fields"
        )
    policy_ref = _text(raw.get("policy_ref"), field_name="policy_ref")
    match = _POLICY_REF_RE.fullmatch(policy_ref)
    if match is None:
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy ref is not content-addressed"
        )
    base = _normalized_base({key: raw[key] for key in _BASE_KEYS})
    if match.group("digest") != _sha256_json(base):
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy digest does not match its payload"
        )
    return {**base, "policy_ref": policy_ref}


def _normalized_base(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload)
    if set(raw) != _BASE_KEYS:
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy payload has missing or unknown fields"
        )
    expected = {
        "schema_version": GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION,
        "execution_mode": GOVERNED_DELEGATE_EXECUTION_MODE,
        "review_mode": GOVERNED_DELEGATE_REVIEW_MODE,
        "budget_mode": GOVERNED_DELEGATE_BUDGET_MODE,
        "effect_mode": GOVERNED_DELEGATE_EFFECT_MODE,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy declares an unsupported execution mode"
        )
    return {
        "schema_version": GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION,
        "target_id": _identifier(raw.get("target_id"), field_name="target_id"),
        "adapter_id": _identifier(raw.get("adapter_id"), field_name="adapter_id"),
        "execution_mode": GOVERNED_DELEGATE_EXECUTION_MODE,
        "review_mode": GOVERNED_DELEGATE_REVIEW_MODE,
        "budget_mode": GOVERNED_DELEGATE_BUDGET_MODE,
        "effect_mode": GOVERNED_DELEGATE_EFFECT_MODE,
    }


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernedDelegateTargetPolicyError(
            "Delegate target policy must be an object"
        )
    return dict(value)


def _text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernedDelegateTargetPolicyError(f"{field_name} is required")
    return text


def _identifier(value: Any, *, field_name: str) -> str:
    text = _text(value, field_name=field_name)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise GovernedDelegateTargetPolicyError(
            f"{field_name} is not a canonical identifier"
        )
    return text


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GOVERNED_DELEGATE_BUDGET_MODE",
    "GOVERNED_DELEGATE_EFFECT_MODE",
    "GOVERNED_DELEGATE_EXECUTION_MODE",
    "GOVERNED_DELEGATE_POLICY_INVALID",
    "GOVERNED_DELEGATE_POLICY_SCHEMA_VERSION",
    "GOVERNED_DELEGATE_REVIEW_MODE",
    "GovernedDelegateTargetPolicyError",
    "freeze_governed_delegate_target_policy",
    "validate_governed_delegate_target_policy",
]
