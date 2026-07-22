"""Signed, durable feature-canary contracts for the governed Task Runtime.

An environment variable may *request* a guarded runtime feature, but it is
never evidence that the feature was exercised safely.  This module defines the
small immutable receipt that a deployment must present before the bounded
fan-out scheduler can be enabled.  The receipt carries no private key and no
raw test payload; it binds a signed evidence digest to the exact build,
migration head and scheduler configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol


TASK_RUNTIME_FEATURE_CANARY_INVALID = "TASK_RUNTIME_FEATURE_CANARY_INVALID"
TASK_RUNTIME_FEATURE_CANARY_REQUIRED = "TASK_RUNTIME_FEATURE_CANARY_REQUIRED"
TASK_RUNTIME_FEATURE_CANARY_UNATTESTED = (
    "TASK_RUNTIME_FEATURE_CANARY_UNATTESTED"
)
TASK_RUNTIME_FEATURE_CANARY_MISMATCH = "TASK_RUNTIME_FEATURE_CANARY_MISMATCH"
TASK_RUNTIME_FEATURE_CANARY_EXPIRED = "TASK_RUNTIME_FEATURE_CANARY_EXPIRED"

TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION = (
    "Natsume.task_runtime.feature_canary_receipt.v1"
)
BOUNDED_FANOUT_FEATURE_KEY = "bounded_fanout"
_SUPPORTED_FEATURES = frozenset({BOUNDED_FANOUT_FEATURE_KEY})
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RECEIPT_REF_RE = re.compile(r"^task-runtime-canary://sha256/[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TaskRuntimeFeatureCanaryError(RuntimeError):
    """Structured failure emitted by the feature-canary authority boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_RUNTIME_FEATURE_CANARY_INVALID)
        self.detail = str(detail or "task runtime feature canary is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class FeatureCanarySignatureVerifier(Protocol):
    """Verification-only signature port; issuing a receipt stays external."""

    def has_key(self, key_id: str) -> bool: ...

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool: ...


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_ref(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _required_text(
    value: Any,
    *,
    field_name: str,
    max_length: int = 512,
) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            f"{field_name} is required",
        )
    return normalized


def _sha256_ref_value(value: Any, *, field_name: str) -> str:
    normalized = _required_text(value, field_name=field_name, max_length=71)
    if _SHA256_REF_RE.fullmatch(normalized) is None:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            f"{field_name} must be a canonical sha256 reference",
        )
    return normalized


def _commit_sha(value: Any) -> str:
    normalized = _required_text(value, field_name="build_commit_sha", max_length=40)
    if _COMMIT_SHA_RE.fullmatch(normalized) is None:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "build_commit_sha must be a full lowercase Git SHA",
        )
    return normalized


def _runtime_migration_version(value: Any) -> int:
    if isinstance(value, bool):
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "runtime_migration_version is invalid",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "runtime_migration_version is invalid",
        ) from exc
    if normalized < 1:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "runtime_migration_version must be positive",
        )
    return normalized


def _feature_key(value: Any) -> str:
    normalized = _required_text(value, field_name="feature_key", max_length=80)
    if normalized not in _SUPPORTED_FEATURES:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "feature_key is unsupported",
        )
    return normalized


def _key_id(value: Any) -> str:
    normalized = _required_text(value, field_name="attestation_key_id", max_length=128)
    if _KEY_ID_RE.fullmatch(normalized) is None:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "attestation_key_id is invalid",
        )
    return normalized


def _timestamp(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            f"{field_name} is invalid",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            f"{field_name} is invalid",
        ) from exc
    if normalized < 0:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            f"{field_name} must be non-negative",
        )
    return normalized


def _unsigned_payload(
    *,
    feature_key: str,
    build_commit_sha: str,
    runtime_migration_version: int,
    configuration_digest: str,
    evidence_digest: str,
    attestation_key_id: str,
    issued_at_ms: int,
    expires_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION,
        "feature_key": feature_key,
        "build_commit_sha": build_commit_sha,
        "runtime_migration_version": runtime_migration_version,
        "configuration_digest": configuration_digest,
        "evidence_digest": evidence_digest,
        "attestation_key_id": attestation_key_id,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
    }


def task_runtime_feature_canary_signing_message(
    *,
    feature_key: str,
    build_commit_sha: str,
    runtime_migration_version: int,
    configuration_digest: str,
    evidence_digest: str,
    attestation_key_id: str,
    issued_at_ms: int,
    expires_at_ms: int,
) -> tuple[str, bytes]:
    """Return the deterministic receipt reference and external signing bytes."""

    normalized_feature = _feature_key(feature_key)
    normalized_commit = _commit_sha(build_commit_sha)
    normalized_migration = _runtime_migration_version(runtime_migration_version)
    normalized_configuration = _sha256_ref_value(
        configuration_digest,
        field_name="configuration_digest",
    )
    normalized_evidence = _sha256_ref_value(
        evidence_digest,
        field_name="evidence_digest",
    )
    normalized_key_id = _key_id(attestation_key_id)
    issued = _timestamp(issued_at_ms, field_name="issued_at_ms")
    expires = _timestamp(expires_at_ms, field_name="expires_at_ms")
    if expires <= issued:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "expires_at_ms must follow issued_at_ms",
        )
    payload = _unsigned_payload(
        feature_key=normalized_feature,
        build_commit_sha=normalized_commit,
        runtime_migration_version=normalized_migration,
        configuration_digest=normalized_configuration,
        evidence_digest=normalized_evidence,
        attestation_key_id=normalized_key_id,
        issued_at_ms=issued,
        expires_at_ms=expires,
    )
    receipt_ref = "task-runtime-canary://sha256/" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    signed_payload = {
        "receipt_ref": receipt_ref,
        **payload,
    }
    return receipt_ref, _canonical_json(signed_payload).encode("utf-8")


@dataclass(frozen=True, slots=True)
class TaskRuntimeFeatureCanaryReceipt:
    """One append-only, signed authorization to enable a runtime feature."""

    receipt_ref: str
    feature_key: str
    build_commit_sha: str
    runtime_migration_version: int
    configuration_digest: str
    evidence_digest: str
    attestation_key_id: str
    attestation_signature: str
    issued_at_ms: int
    expires_at_ms: int
    schema_version: str = TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION:
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "schema_version is unsupported",
            )
        expected_ref, _message = task_runtime_feature_canary_signing_message(
            feature_key=self.feature_key,
            build_commit_sha=self.build_commit_sha,
            runtime_migration_version=self.runtime_migration_version,
            configuration_digest=self.configuration_digest,
            evidence_digest=self.evidence_digest,
            attestation_key_id=self.attestation_key_id,
            issued_at_ms=self.issued_at_ms,
            expires_at_ms=self.expires_at_ms,
        )
        if (
            _RECEIPT_REF_RE.fullmatch(str(self.receipt_ref or "").strip()) is None
            or self.receipt_ref != expected_ref
        ):
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "receipt_ref differs from immutable receipt fields",
            )
        signature = _required_text(
            self.attestation_signature,
            field_name="attestation_signature",
            max_length=512,
        )
        if not signature.startswith("ed25519:"):
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "attestation_signature must use ed25519",
            )

    @property
    def signing_message(self) -> bytes:
        _receipt_ref, message = task_runtime_feature_canary_signing_message(
            feature_key=self.feature_key,
            build_commit_sha=self.build_commit_sha,
            runtime_migration_version=self.runtime_migration_version,
            configuration_digest=self.configuration_digest,
            evidence_digest=self.evidence_digest,
            attestation_key_id=self.attestation_key_id,
            issued_at_ms=self.issued_at_ms,
            expires_at_ms=self.expires_at_ms,
        )
        return message

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_ref": self.receipt_ref,
            "schema_version": self.schema_version,
            "feature_key": self.feature_key,
            "build_commit_sha": self.build_commit_sha,
            "runtime_migration_version": self.runtime_migration_version,
            "configuration_digest": self.configuration_digest,
            "evidence_digest": self.evidence_digest,
            "attestation_key_id": self.attestation_key_id,
            "attestation_signature": self.attestation_signature,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TaskRuntimeFeatureCanaryReceipt":
        if not isinstance(raw, Mapping):
            raise TaskRuntimeFeatureCanaryError(
                TASK_RUNTIME_FEATURE_CANARY_INVALID,
                "feature canary receipt must be an object",
            )
        return cls(
            receipt_ref=str(raw.get("receipt_ref") or "").strip(),
            schema_version=str(raw.get("schema_version") or "").strip(),
            feature_key=str(raw.get("feature_key") or "").strip(),
            build_commit_sha=str(raw.get("build_commit_sha") or "").strip(),
            runtime_migration_version=raw.get("runtime_migration_version"),
            configuration_digest=str(raw.get("configuration_digest") or "").strip(),
            evidence_digest=str(raw.get("evidence_digest") or "").strip(),
            attestation_key_id=str(raw.get("attestation_key_id") or "").strip(),
            attestation_signature=str(raw.get("attestation_signature") or "").strip(),
            issued_at_ms=raw.get("issued_at_ms"),
            expires_at_ms=raw.get("expires_at_ms"),
        )


def bounded_fanout_configuration_digest(
    *,
    atomic_graph_success_enabled: bool,
    fanout_event_buffer_size: int,
) -> str:
    """Hash the exact scheduler knobs exercised by a bounded fan-out canary."""

    if isinstance(fanout_event_buffer_size, bool):
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "fanout_event_buffer_size is invalid",
        )
    try:
        buffer_size = int(fanout_event_buffer_size)
    except (TypeError, ValueError) as exc:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "fanout_event_buffer_size is invalid",
        ) from exc
    if not 1 <= buffer_size <= 4_096:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_INVALID,
            "fanout_event_buffer_size is outside the supported range",
        )
    return _sha256_ref(
        _canonical_json(
            {
                "feature_key": BOUNDED_FANOUT_FEATURE_KEY,
                "atomic_graph_success_enabled": bool(atomic_graph_success_enabled),
                "fanout_event_buffer_size": buffer_size,
            }
        )
    )


def require_bounded_fanout_canary(
    *,
    requested: bool,
    receipt_ref: str,
    build_commit_sha: str,
    runtime_migration_version: int,
    atomic_graph_success_enabled: bool,
    fanout_event_buffer_size: int,
    receipt_store: Any,
    signature_verifier: FeatureCanarySignatureVerifier | None,
    now_ms: int,
) -> bool:
    """Resolve a feature request only after reopening and verifying its receipt."""

    if not bool(requested):
        return False
    if receipt_store is None:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
            "bounded fan-out requires the durable feature-canary store",
        )
    if signature_verifier is None:
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
            "bounded fan-out requires a verification-only canary key ring",
        )
    require_active = getattr(receipt_store, "require_active", None)
    if not callable(require_active):
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
            "feature-canary store has no active receipt resolver",
        )
    configuration_digest = bounded_fanout_configuration_digest(
        atomic_graph_success_enabled=atomic_graph_success_enabled,
        fanout_event_buffer_size=fanout_event_buffer_size,
    )
    receipt = require_active(
        receipt_ref=str(receipt_ref or "").strip(),
        feature_key=BOUNDED_FANOUT_FEATURE_KEY,
        build_commit_sha=_commit_sha(build_commit_sha),
        runtime_migration_version=_runtime_migration_version(
            runtime_migration_version
        ),
        configuration_digest=configuration_digest,
        signature_verifier=signature_verifier,
        now_ms=_timestamp(now_ms, field_name="now_ms"),
    )
    if not isinstance(receipt, TaskRuntimeFeatureCanaryReceipt):
        raise TaskRuntimeFeatureCanaryError(
            TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
            "feature-canary resolver returned no typed receipt",
        )
    return True


__all__ = [
    "BOUNDED_FANOUT_FEATURE_KEY",
    "FeatureCanarySignatureVerifier",
    "TASK_RUNTIME_FEATURE_CANARY_EXPIRED",
    "TASK_RUNTIME_FEATURE_CANARY_INVALID",
    "TASK_RUNTIME_FEATURE_CANARY_MISMATCH",
    "TASK_RUNTIME_FEATURE_CANARY_REQUIRED",
    "TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION",
    "TASK_RUNTIME_FEATURE_CANARY_UNATTESTED",
    "TaskRuntimeFeatureCanaryError",
    "TaskRuntimeFeatureCanaryReceipt",
    "bounded_fanout_configuration_digest",
    "require_bounded_fanout_canary",
    "task_runtime_feature_canary_signing_message",
]
