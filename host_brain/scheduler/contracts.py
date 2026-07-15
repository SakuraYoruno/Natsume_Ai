"""Immutable contracts for the governed scheduler command lane.

The clock, HTTP adapters and workers exchange only durable scheduler
identities.  Definition policy is always reloaded from the scheduler store;
caller supplied priority, mode or resource metadata is never authoritative.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import normalize_task_retry_policy


SCHEDULER_OCCURRENCE_SCHEMA_VERSION = "Natsume.scheduler.occurrence.v1"
SCHEDULER_DEFINITION_SCHEMA_VERSION = "Natsume.scheduler.definition.v1"
SCHEDULER_WORKER_ROUTE = "scheduler_production"

SCHEDULER_CONTRACT_INVALID = "SCHEDULER_CONTRACT_INVALID"
SCHEDULER_DEFINITION_INVALID = "SCHEDULER_DEFINITION_INVALID"
SCHEDULER_OCCURRENCE_INVALID = "SCHEDULER_OCCURRENCE_INVALID"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,475}$"
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|"
    r"raw_?input|secret|signature|(?:access|refresh|auth|raw)_?token)(?:$|_)|^token$",
    re.IGNORECASE,
)


class SchedulerContractError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_CONTRACT_INVALID)
        self.detail = str(detail or "scheduler contract is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerOccurrenceSource(StrEnum):
    CLOCK = "clock"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SchedulerOccurrence:
    """One occurrence durably admitted before governed execution."""

    occurrence_id: str
    task_id: str
    definition_revision: int
    source: SchedulerOccurrenceSource
    scheduled_at_ms: int
    payload: dict[str, Any]
    request_id: str
    principal_id: str
    claim_ref: str
    audit_ref: str
    body_sha256: str
    schema_version: str = SCHEDULER_OCCURRENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEDULER_OCCURRENCE_SCHEMA_VERSION:
            raise SchedulerContractError(
                SCHEDULER_OCCURRENCE_INVALID,
                "occurrence schema version is unsupported",
            )
        _identifier(self.task_id, "task_id")
        _reference(self.occurrence_id, "occurrence_id")
        _positive_int(self.definition_revision, "definition_revision")
        _non_negative_int(self.scheduled_at_ms, "scheduled_at_ms")
        if not isinstance(self.source, SchedulerOccurrenceSource):
            raise SchedulerContractError(
                SCHEDULER_OCCURRENCE_INVALID,
                "source must be clock or manual",
            )
        _identifier(self.request_id, "request_id")
        _identifier(self.principal_id, "principal_id")
        _reference(self.claim_ref, "claim_ref")
        _reference(self.audit_ref, "audit_ref")
        _canonical_sha256(self.body_sha256, "body_sha256")
        _finite_mapping(self.payload, "payload")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SchedulerOccurrence":
        if not isinstance(payload, Mapping):
            raise SchedulerContractError(
                SCHEDULER_OCCURRENCE_INVALID,
                "occurrence must be an object",
            )
        data = dict(payload)
        raw_source = str(data.get("source") or "").strip().lower()
        try:
            source = SchedulerOccurrenceSource(raw_source)
        except ValueError as exc:
            raise SchedulerContractError(
                SCHEDULER_OCCURRENCE_INVALID,
                "occurrence source must be clock or manual",
            ) from exc
        return cls(
            occurrence_id=str(data.get("occurrence_id") or "").strip(),
            task_id=str(data.get("task_id") or "").strip(),
            definition_revision=_positive_int(
                data.get("definition_revision"),
                "definition_revision",
            ),
            source=source,
            scheduled_at_ms=_non_negative_int(
                data.get("scheduled_at_ms"),
                "scheduled_at_ms",
            ),
            payload=_finite_mapping(data.get("payload"), "payload"),
            request_id=str(data.get("request_id") or "").strip(),
            principal_id=str(data.get("principal_id") or "").strip(),
            claim_ref=str(
                data.get("claim_ref") or data.get("ingress_claim_ref") or ""
            ).strip(),
            audit_ref=str(
                data.get("audit_ref") or data.get("ingress_audit_ref") or ""
            ).strip(),
            body_sha256=_canonical_sha256(
                data.get("body_sha256"),
                "body_sha256",
            ),
            schema_version=str(
                data.get("schema_version") or SCHEDULER_OCCURRENCE_SCHEMA_VERSION
            ),
        )

    @property
    def payload_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "definition_revision": self.definition_revision,
                "occurrence_id": self.occurrence_id,
                "payload": self.payload,
                "scheduled_at_ms": self.scheduled_at_ms,
                "schema_version": self.schema_version,
                "source": self.source.value,
                "task_id": self.task_id,
                "request_id": self.request_id,
                "principal_id": self.principal_id,
                "claim_ref": self.claim_ref,
                "audit_ref": self.audit_ref,
                "body_sha256": self.body_sha256,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "occurrence_id": self.occurrence_id,
            "task_id": self.task_id,
            "definition_revision": self.definition_revision,
            "source": self.source.value,
            "request_id": self.request_id,
            "principal_id": self.principal_id,
            "claim_ref": self.claim_ref,
            "audit_ref": self.audit_ref,
            "body_sha256": self.body_sha256,
            "scheduled_at_ms": self.scheduled_at_ms,
            "payload": dict(self.payload),
        }

    def authority_dict(self, *, duplicate: bool = False) -> dict[str, Any]:
        return {
            "allowed": not duplicate,
            "duplicate": bool(duplicate),
            "reason_code": "SCHEDULER_DURABLE_OCCURRENCE_AUTHORITY",
            "request_id": self.request_id,
            "principal_id": self.principal_id,
            "claim_ref": self.claim_ref,
            "audit_ref": self.audit_ref,
            "body_sha256": self.body_sha256.removeprefix("sha256:"),
            "existing_result_ref": None,
        }


@dataclass(frozen=True, slots=True)
class SchedulerDefinitionSnapshot:
    """Server-owned definition revision and current head policy."""

    task_id: str
    definition_revision: int
    head_version: int
    enabled: bool
    definition: dict[str, Any]
    next_scheduled_at_ms: int | None = None
    schema_version: str = SCHEDULER_DEFINITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEDULER_DEFINITION_SCHEMA_VERSION:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "definition schema version is unsupported",
            )
        _identifier(self.task_id, "task_id")
        _positive_int(self.definition_revision, "definition_revision")
        _positive_int(self.head_version, "head_version")
        if type(self.enabled) is not bool:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "enabled must be boolean",
            )
        if self.next_scheduled_at_ms is not None:
            _non_negative_int(self.next_scheduled_at_ms, "next_scheduled_at_ms")
        definition = _finite_mapping(self.definition, "definition")
        embedded_task = str(definition.get("task_id") or self.task_id).strip()
        if embedded_task != self.task_id:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "definition task_id differs from durable head",
            )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SchedulerDefinitionSnapshot":
        if not isinstance(payload, Mapping):
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "definition record must be an object",
            )
        data = dict(payload)
        definition = _finite_mapping(data.get("definition"), "definition")
        revision = data.get("definition_revision", data.get("revision"))
        return cls(
            task_id=str(data.get("task_id") or definition.get("task_id") or "").strip(),
            definition_revision=_positive_int(revision, "definition_revision"),
            head_version=_positive_int(
                data.get("head_version", data.get("version", revision)),
                "head_version",
            ),
            enabled=_definition_enabled(data, definition),
            definition=definition,
            next_scheduled_at_ms=_optional_non_negative_int(
                data.get("next_scheduled_at_ms"),
                "next_scheduled_at_ms",
            ),
            schema_version=str(
                data.get("schema_version") or SCHEDULER_DEFINITION_SCHEMA_VERSION
            ),
        )

    @property
    def priority(self) -> str:
        value = str(self.definition.get("priority") or "MEDIUM").strip().upper()
        normalized = {
            "P0": "HIGH",
            "P1": "HIGH",
            "P2": "MEDIUM",
            "P3": "LOW",
        }.get(value, value)
        if normalized not in {"HIGH", "MEDIUM", "LOW"}:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "priority must be HIGH/MEDIUM/LOW or P0/P1/P2/P3",
            )
        return normalized

    @property
    def mode_allowlist(self) -> tuple[str, ...] | None:
        raw = self.definition.get("mode_allowlist")
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)):
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "mode_allowlist must be an array",
            )
        values = tuple(str(item or "").strip().lower() for item in raw)
        if not values or any(not value for value in values):
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "mode_allowlist must contain non-empty modes",
            )
        return values

    @property
    def resource_profile(self) -> dict[str, Any]:
        value = self.definition.get("resource_profile")
        if value is None:
            return {}
        return _finite_mapping(value, "resource_profile")

    @property
    def retry_policy(self) -> dict[str, Any]:
        raw = self.definition.get("retry_policy")
        if raw is None:
            trigger = self.definition.get("trigger_config")
            raw = dict(trigger).get("retry_policy") if isinstance(trigger, Mapping) else None
        policy = _finite_mapping(
            raw if raw is not None else {"max_attempts": 1},
            "retry_policy",
        )
        try:
            return normalize_task_retry_policy(
                policy,
                field_name="retry_policy",
            )
        except ContractViolation as exc:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                exc.detail,
            ) from exc

    @property
    def intent_template(self) -> dict[str, Any]:
        raw = self.definition.get("intent_template")
        if raw is None:
            raw = self.definition.get("payload")
        return _finite_mapping(raw or {}, "intent_template")

    @property
    def worker_key(self) -> str:
        value = str(self.definition.get("worker_key") or "").strip().lower()
        if not value or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
            raise SchedulerContractError(
                SCHEDULER_DEFINITION_INVALID,
                "definition.worker_key must be a bounded lower-case worker key",
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "definition_revision": self.definition_revision,
            "head_version": self.head_version,
            "enabled": self.enabled,
            "next_scheduled_at_ms": self.next_scheduled_at_ms,
            "definition": dict(self.definition),
        }


def occurrence_work_order_id(occurrence_id: str) -> str:
    _reference(occurrence_id, "occurrence_id")
    digest = hashlib.sha256(occurrence_id.encode("utf-8")).hexdigest()
    return f"scheduler-work-{digest[:48]}"


def validate_definition_payload(
    *,
    task_id: str,
    definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a publish candidate without inventing durable revision data."""

    task_ref = _identifier(str(task_id or "").strip(), "task_id")
    payload = _finite_mapping(definition, "definition")
    embedded_task = str(payload.get("task_id") or task_ref).strip()
    if embedded_task != task_ref:
        raise SchedulerContractError(
            SCHEDULER_DEFINITION_INVALID,
            "definition task_id differs from the publish target",
        )
    candidate = SchedulerDefinitionSnapshot(
        task_id=task_ref,
        definition_revision=1,
        head_version=1,
        enabled=_definition_enabled({}, payload),
        definition=payload,
    )
    # Force validation of policy-bearing fields at the write boundary.
    candidate.priority
    candidate.mode_allowlist
    candidate.resource_profile
    candidate.retry_policy
    candidate.intent_template
    candidate.worker_key
    return payload


def _definition_enabled(data: Mapping[str, Any], definition: Mapping[str, Any]) -> bool:
    value = data.get("enabled", definition.get("enabled", True))
    if type(value) is not bool:
        raise SchedulerContractError(
            SCHEDULER_DEFINITION_INVALID,
            "enabled must be boolean",
        )
    return value


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            "payload must contain finite JSON values",
        ) from exc


def _finite_mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be an object with string keys",
        )
    data = dict(value)
    _assert_secret_free(data, field_name)
    _canonical_json(data)
    return data


def _assert_secret_free(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            normalized = key_text.strip().lower()
            if _FORBIDDEN_KEY_RE.search(normalized) and not normalized.endswith("_ref"):
                raise SchedulerContractError(
                    SCHEDULER_CONTRACT_INVALID,
                    f"{path}.{key_text} contains forbidden raw secret material",
                )
            _assert_secret_free(child, f"{path}.{key_text}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(child, f"{path}[{index}]")


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be a bounded identifier",
        )
    return value


def _reference(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _REFERENCE_RE.fullmatch(value) is None:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be a durable reference",
        )
    return value


def _canonical_sha256(value: object, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", text) is None:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be a SHA-256 digest",
        )
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be an integer >= 1",
        )
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise SchedulerContractError(
            SCHEDULER_CONTRACT_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    return value


def _optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, field_name)


__all__ = [
    "SCHEDULER_CONTRACT_INVALID",
    "SCHEDULER_DEFINITION_INVALID",
    "SCHEDULER_DEFINITION_SCHEMA_VERSION",
    "SCHEDULER_OCCURRENCE_INVALID",
    "SCHEDULER_OCCURRENCE_SCHEMA_VERSION",
    "SCHEDULER_WORKER_ROUTE",
    "SchedulerContractError",
    "SchedulerDefinitionSnapshot",
    "SchedulerOccurrence",
    "SchedulerOccurrenceSource",
    "occurrence_work_order_id",
    "validate_definition_payload",
]
