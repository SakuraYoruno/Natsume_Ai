"""Pure contracts and deterministic rules for trusted real-trace redaction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    LabDatasetV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import RedactionReceiptV1


REDACTION_RULES_VERSION = "natsume.real-trace-redaction.v1"
_SAFE_BOOLEAN_FIELDS = frozenset(
    {
        "has_provenance",
        "maintenance",
        "success",
        "user_annoyed",
        "user_consent",
        "verified",
    }
)
_SAFE_NUMERIC_FIELDS = frozenset(
    {
        "attempt_no",
        "duration_ms",
        "graph_revision",
        "hour",
        "latency_ms",
        "token_count",
    }
)
_SAFE_IDENTIFIER_FIELDS = frozenset(
    {
        "action",
        "capability_id",
        "event_type",
        "game_mode",
        "outcome",
        "reason_code",
        "risk_level",
        "route",
        "scene",
        "sensitivity",
        "status",
        "surface",
        "tool_name",
    }
)
_SAFE_SCALAR_FIELDS = (
    _SAFE_BOOLEAN_FIELDS | _SAFE_NUMERIC_FIELDS | _SAFE_IDENTIFIER_FIELDS
)
_SAFE_LIST_FIELDS = frozenset(
    {
        "forbidden_steps",
        "reason_codes",
        "required_steps",
        "tags",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\b(?:\+?\d[\d ()-]{7,}\d)\b|"
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|bearer\s+\S+|password\s*[:=])"
    r")"
)
_SAFE_IDENTIFIER_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_LONG_HEX_OR_TOKEN = re.compile(
    r"(?i)^(?:[a-f0-9]{24,}|[a-z0-9_-]{48,})$"
)
_RULES_DIGEST = canonical_evolution_lab_digest(
    {
        "schema": REDACTION_RULES_VERSION,
        "boolean_fields": sorted(_SAFE_BOOLEAN_FIELDS),
        "identifier_fields": sorted(_SAFE_IDENTIFIER_FIELDS),
        "identifier_policy": "bounded-token-no-secret-pattern",
        "numeric_fields": sorted(_SAFE_NUMERIC_FIELDS),
        "safe_list_fields": sorted(_SAFE_LIST_FIELDS),
        "safe_scalar_fields": sorted(_SAFE_SCALAR_FIELDS),
        "string_max_length": 160,
        "sensitive_text_patterns": "email|phone|api-key|bearer|password",
        "unknown_fields": "drop",
        "record_identity": "sha256-only",
    }
)


class TrustedTraceRedactionError(RuntimeError):
    """A real trace or its durable projection failed the privacy contract."""


@dataclass(frozen=True, slots=True)
class TrustedRedactedTraceDataset:
    """Independently reopened sanitized dataset and immutable bindings."""

    receipt: RedactionReceiptV1
    manifest: DatasetManifestV1
    dataset: LabDatasetV1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.receipt, RedactionReceiptV1)
            or not isinstance(self.manifest, DatasetManifestV1)
            or not isinstance(self.dataset, LabDatasetV1)
        ):
            raise TrustedTraceRedactionError(
                "trusted redacted dataset contracts are invalid"
            )


def trusted_redaction_rules_digest() -> str:
    return _RULES_DIGEST


def trusted_redaction_identity_digest(
    workspace_id: str,
    source_digest: str,
) -> str:
    return canonical_evolution_lab_digest(
        {
            "workspace_id": workspace_id,
            "source_digest": source_digest,
            "rules_digest": _RULES_DIGEST,
        }
    )


def trusted_redacted_dataset_schema_digest(
    dataset: LabDatasetV1,
) -> str:
    fields = sorted(
        {
            key
            for record in dataset.records
            for key in record
        }
    )
    return canonical_evolution_lab_digest(
        {
            "schema": "natsume.evolution-lab.redacted-trace-schema.v1",
            "fields": fields,
            "rules_digest": _RULES_DIGEST,
        }
    )


def project_trusted_trace_records(
    records: tuple[Mapping[str, Any], ...],
    *,
    source_digest: str,
) -> tuple[tuple[Mapping[str, Any], ...], int]:
    """Return a deterministic allowlisted projection with digest-only identity."""

    projected_records: list[Mapping[str, Any]] = []
    removed = 0
    for record in records:
        projected, removed_count = _project_record(record)
        projected["record_ref"] = canonical_evolution_lab_digest(
            {
                "source_digest": source_digest,
                "record": record,
            }
        )
        projected_records.append(projected)
        removed += removed_count
    return tuple(projected_records), removed


def _project_record(
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    removed = 0
    for raw_key, value in record.items():
        key = str(raw_key or "").strip().lower()
        if key in _SAFE_SCALAR_FIELDS:
            sanitized = _safe_scalar(key, value)
            if sanitized is None:
                removed += 1
            else:
                result[key] = sanitized
        elif key in _SAFE_LIST_FIELDS and isinstance(value, list):
            values = [
                sanitized
                for item in value[:100]
                if (sanitized := _safe_identifier(item)) is not None
            ]
            removed += max(0, len(value) - len(values))
            result[key] = values
        else:
            removed += 1
    return dict(sorted(result.items())), removed


def _safe_scalar(
    field: str,
    value: Any,
) -> str | int | float | bool | None:
    if field in _SAFE_BOOLEAN_FIELDS:
        return value if isinstance(value, bool) else None
    if field in _SAFE_NUMERIC_FIELDS:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return (
                value
                if value == value and abs(value) != float("inf")
                else None
            )
        return None
    return _safe_identifier(value)


def _safe_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or _SAFE_IDENTIFIER_TEXT.fullmatch(text) is None
        or _LONG_HEX_OR_TOKEN.fullmatch(text) is not None
        or _SENSITIVE_TEXT.search(text)
    ):
        return None
    return text


__all__ = [
    "REDACTION_RULES_VERSION",
    "TrustedRedactedTraceDataset",
    "TrustedTraceRedactionError",
    "project_trusted_trace_records",
    "trusted_redacted_dataset_schema_digest",
    "trusted_redaction_identity_digest",
    "trusted_redaction_rules_digest",
]
