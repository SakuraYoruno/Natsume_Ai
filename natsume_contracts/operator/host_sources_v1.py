"""Versioned Host-to-Observability read-source contracts.

The contracts are runtime independent.  Host owns the live objects, while the
standalone Observability process may only receive these bounded public read
models over an authenticated transport.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final


HOST_MODE_SOURCE_SCHEMA_VERSION: Final = "Natsume.host.obs_source.mode.v1"
HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION: Final = (
    "Natsume.host.obs_source.capability_forge.v1"
)
HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION: Final = (
    "Natsume.host.obs_source.governance.v1"
)
HOST_RUNTIME_SOURCE_SCHEMA_VERSION: Final = "Natsume.host.obs_source.runtime.v1"
HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION: Final = (
    "Natsume.host.obs_source.semantic_recall.v1"
)


class HostObsSourceContractError(ValueError):
    """Stable contract failure without reflecting private source payloads."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "HOST_OBS_SOURCE_CONTRACT_INVALID")
        super().__init__(self.reason_code)


def _require_schema(payload: Mapping[str, Any], expected: str) -> None:
    if str(payload.get("schema_version") or "") != expected:
        raise HostObsSourceContractError("HOST_OBS_SOURCE_SCHEMA_INVALID")


def _positive_timestamp(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HostObsSourceContractError("HOST_OBS_SOURCE_TIMESTAMP_INVALID")
    timestamp = value
    if timestamp <= 0:
        raise HostObsSourceContractError("HOST_OBS_SOURCE_TIMESTAMP_INVALID")
    return timestamp


def _nonnegative_timestamp(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HostObsSourceContractError(
            "HOST_OBS_SOURCE_EVIDENCE_TIMESTAMP_INVALID"
        )
    timestamp = value
    if timestamp < 0:
        raise HostObsSourceContractError("HOST_OBS_SOURCE_EVIDENCE_TIMESTAMP_INVALID")
    return timestamp


def _bounded_evidence_timestamp(value: Any, *, observed_at_ms: int) -> int:
    timestamp = _nonnegative_timestamp(value)
    if timestamp > observed_at_ms:
        raise HostObsSourceContractError(
            "HOST_OBS_SOURCE_EVIDENCE_TIMESTAMP_INVALID"
        )
    return timestamp


def _source_instance_id(value: Any) -> str:
    if not isinstance(value, str):
        raise HostObsSourceContractError("HOST_OBS_SOURCE_INSTANCE_INVALID")
    source_id = value.strip()
    if not 8 <= len(source_id) <= 128 or any(
        not (character.isalnum() or character in "-_.:")
        for character in source_id
    ):
        raise HostObsSourceContractError("HOST_OBS_SOURCE_INSTANCE_INVALID")
    return source_id


def _source_revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HostObsSourceContractError("HOST_OBS_SOURCE_REVISION_INVALID")
    revision = value
    if revision <= 0:
        raise HostObsSourceContractError("HOST_OBS_SOURCE_REVISION_INVALID")
    return revision


def _validate_known_timestamp(
    value: Mapping[str, Any],
    key: str,
    *,
    observed_at_ms: int,
    reason_code: str,
    required: bool = False,
) -> int | None:
    if key not in value or value.get(key) is None:
        if required:
            raise HostObsSourceContractError(reason_code)
        return None
    raw_timestamp = value.get(key)
    if not isinstance(raw_timestamp, int) or isinstance(raw_timestamp, bool):
        raise HostObsSourceContractError(reason_code)
    timestamp = raw_timestamp
    if timestamp < 0 or (required and timestamp == 0) or timestamp > observed_at_ms:
        raise HostObsSourceContractError(reason_code)
    return timestamp


def _validate_timestamp_tree(
    value: Any,
    *,
    observed_at_ms: int,
    reason_code: str,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key or "").strip().lower()
            if normalized == "timestamp" or normalized.endswith(
                ("_ts_ms", "_at_ms")
            ):
                if item is not None:
                    # Expiry, retry and scheduling fields describe a future
                    # boundary rather than already-observed evidence.  They
                    # must remain typed/non-negative, but rejecting values
                    # after ``observed_at_ms`` makes a healthy live scheduler
                    # fail its source contract by construction.
                    if _is_future_boundary_timestamp(normalized):
                        if (
                            not isinstance(item, int)
                            or isinstance(item, bool)
                            or item < 0
                        ):
                            raise HostObsSourceContractError(reason_code)
                    else:
                        _validate_known_timestamp(
                            value,
                            str(key),
                            observed_at_ms=observed_at_ms,
                            reason_code=reason_code,
                        )
            else:
                _validate_timestamp_tree(
                    item,
                    observed_at_ms=observed_at_ms,
                    reason_code=reason_code,
                )
        return
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            _validate_timestamp_tree(
                item,
                observed_at_ms=observed_at_ms,
                reason_code=reason_code,
            )


def _is_future_boundary_timestamp(field_name: str) -> bool:
    """Return whether a timestamp names a planned future boundary.

    Observation, creation and update timestamps stay bounded by the source
    observation clock.  This narrow classifier only covers values whose
    semantics explicitly require permission to point into the future.
    """

    normalized = str(field_name or "").strip().lower()
    return (
        normalized.startswith("next_")
        or "expires_" in normalized
        or "expiry_" in normalized
        or "deadline_" in normalized
        or "not_before_" in normalized
        or "renew_after_" in normalized
    )


def _mapping(value: Any, reason_code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HostObsSourceContractError(reason_code)
    return dict(value)


def _mapping_items(
    value: Any,
    *,
    reason_code: str,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise HostObsSourceContractError(reason_code)
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise HostObsSourceContractError(reason_code)
        rows.append(dict(item))
        if len(rows) > limit:
            raise HostObsSourceContractError(reason_code)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class HostModeSourceSnapshot:
    observed_at_ms: int
    evidence_ts_ms: int
    source_instance_id: str
    source_revision: int
    status: Mapping[str, Any]
    history: tuple[Mapping[str, Any], ...]
    schema_version: str = HOST_MODE_SOURCE_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HostModeSourceSnapshot":
        _require_schema(payload, HOST_MODE_SOURCE_SCHEMA_VERSION)
        observed_at_ms = _positive_timestamp(payload.get("observed_at_ms"))
        status = _mapping(payload.get("status"), "HOST_OBS_MODE_STATUS_INVALID")
        if not str(status.get("sys_mode") or "").strip():
            raise HostObsSourceContractError("HOST_OBS_MODE_STATUS_INVALID")
        _validate_known_timestamp(
            status,
            "ts_ms",
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_MODE_STATUS_INVALID",
            required=True,
        )
        _validate_known_timestamp(
            status,
            "last_interaction_ts_ms",
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_MODE_STATUS_INVALID",
        )
        history = _mapping_items(
            payload.get("history"),
            reason_code="HOST_OBS_MODE_HISTORY_INVALID",
            limit=100,
        )
        for item in history:
            _validate_known_timestamp(
                item,
                "ts_ms",
                observed_at_ms=observed_at_ms,
                reason_code="HOST_OBS_MODE_HISTORY_INVALID",
                required=True,
            )
        _validate_timestamp_tree(
            status,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_MODE_STATUS_INVALID",
        )
        _validate_timestamp_tree(
            history,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_MODE_HISTORY_INVALID",
        )
        return cls(
            observed_at_ms=observed_at_ms,
            evidence_ts_ms=_bounded_evidence_timestamp(
                payload.get("evidence_ts_ms"),
                observed_at_ms=observed_at_ms,
            ),
            source_instance_id=_source_instance_id(
                payload.get("source_instance_id")
            ),
            source_revision=_source_revision(payload.get("source_revision")),
            status=status,
            history=history,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at_ms": self.observed_at_ms,
            "evidence_ts_ms": self.evidence_ts_ms,
            "source_instance_id": self.source_instance_id,
            "source_revision": self.source_revision,
            "status": dict(self.status),
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True, slots=True)
class HostCapabilityForgeSourceSnapshot:
    observed_at_ms: int
    evidence_ts_ms: int
    source_instance_id: str
    source_revision: int
    gap: Mapping[str, Any]
    proposals: tuple[Mapping[str, Any], ...]
    runtime_stats: Mapping[str, Any]
    runtime_tuning: Mapping[str, Any]
    runtime_quality: Mapping[str, Any]
    semantic_recall: Mapping[str, Any]
    governance: Mapping[str, Any]
    schema_version: str = HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "HostCapabilityForgeSourceSnapshot":
        _require_schema(payload, HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION)
        observed_at_ms = _positive_timestamp(payload.get("observed_at_ms"))
        gap = _mapping(payload.get("gap"), "HOST_OBS_CAPABILITY_GAP_INVALID")
        proposals = _mapping_items(
            payload.get("proposals"),
            reason_code="HOST_OBS_CAPABILITY_PROPOSALS_INVALID",
            limit=200,
        )
        runtime_stats = _mapping(
            payload.get("runtime_stats"),
            "HOST_OBS_CAPABILITY_RUNTIME_STATS_INVALID",
        )
        runtime_tuning = _mapping(
            payload.get("runtime_tuning"),
            "HOST_OBS_CAPABILITY_RUNTIME_TUNING_INVALID",
        )
        runtime_quality = _mapping(
            payload.get("runtime_quality"),
            "HOST_OBS_CAPABILITY_RUNTIME_QUALITY_INVALID",
        )
        semantic_recall = _mapping(
            payload.get("semantic_recall"),
            "HOST_OBS_SEMANTIC_RECALL_INVALID",
        )
        governance = _mapping(
            payload.get("governance"),
            "HOST_OBS_GOVERNANCE_INVALID",
        )
        for item, reason_code in (
            (gap, "HOST_OBS_CAPABILITY_GAP_INVALID"),
            (runtime_stats, "HOST_OBS_CAPABILITY_RUNTIME_STATS_INVALID"),
            (runtime_tuning, "HOST_OBS_CAPABILITY_RUNTIME_TUNING_INVALID"),
            (runtime_quality, "HOST_OBS_CAPABILITY_RUNTIME_QUALITY_INVALID"),
            (semantic_recall, "HOST_OBS_SEMANTIC_RECALL_INVALID"),
            (governance, "HOST_OBS_GOVERNANCE_INVALID"),
        ):
            _validate_known_timestamp(
                item,
                "timestamp",
                observed_at_ms=observed_at_ms,
                reason_code=reason_code,
            )
        for proposal in proposals:
            for key in ("created_ts_ms", "updated_ts_ms"):
                _validate_known_timestamp(
                    proposal,
                    key,
                    observed_at_ms=observed_at_ms,
                    reason_code="HOST_OBS_CAPABILITY_PROPOSALS_INVALID",
                )
        for item, reason_code in (
            (gap, "HOST_OBS_CAPABILITY_GAP_INVALID"),
            (proposals, "HOST_OBS_CAPABILITY_PROPOSALS_INVALID"),
            (runtime_stats, "HOST_OBS_CAPABILITY_RUNTIME_STATS_INVALID"),
            (runtime_tuning, "HOST_OBS_CAPABILITY_RUNTIME_TUNING_INVALID"),
            (runtime_quality, "HOST_OBS_CAPABILITY_RUNTIME_QUALITY_INVALID"),
            (semantic_recall, "HOST_OBS_SEMANTIC_RECALL_INVALID"),
            (governance, "HOST_OBS_GOVERNANCE_INVALID"),
        ):
            _validate_timestamp_tree(
                item,
                observed_at_ms=observed_at_ms,
                reason_code=reason_code,
            )
        return cls(
            observed_at_ms=observed_at_ms,
            evidence_ts_ms=_bounded_evidence_timestamp(
                payload.get("evidence_ts_ms"),
                observed_at_ms=observed_at_ms,
            ),
            source_instance_id=_source_instance_id(
                payload.get("source_instance_id")
            ),
            source_revision=_source_revision(payload.get("source_revision")),
            gap=gap,
            proposals=proposals,
            runtime_stats=runtime_stats,
            runtime_tuning=runtime_tuning,
            runtime_quality=runtime_quality,
            semantic_recall=semantic_recall,
            governance=governance,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at_ms": self.observed_at_ms,
            "evidence_ts_ms": self.evidence_ts_ms,
            "source_instance_id": self.source_instance_id,
            "source_revision": self.source_revision,
            "gap": dict(self.gap),
            "proposals": [dict(item) for item in self.proposals],
            "runtime_stats": dict(self.runtime_stats),
            "runtime_tuning": dict(self.runtime_tuning),
            "runtime_quality": dict(self.runtime_quality),
            "semantic_recall": dict(self.semantic_recall),
            "governance": dict(self.governance),
        }

    def as_projection_payload(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.pop("schema_version", None)
        payload["timestamp"] = payload["observed_at_ms"]
        return payload


@dataclass(frozen=True, slots=True)
class HostSemanticRecallSourceSnapshot:
    observed_at_ms: int
    evidence_ts_ms: int
    source_instance_id: str
    source_revision: int
    stats: Mapping[str, Any]
    schema_version: str = HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "HostSemanticRecallSourceSnapshot":
        _require_schema(payload, HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION)
        observed_at_ms = _positive_timestamp(payload.get("observed_at_ms"))
        stats = _mapping(
            payload.get("stats"),
            "HOST_OBS_SEMANTIC_RECALL_INVALID",
        )
        _validate_known_timestamp(
            stats,
            "timestamp",
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_SEMANTIC_RECALL_INVALID",
            required=True,
        )
        _validate_timestamp_tree(
            stats,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_SEMANTIC_RECALL_INVALID",
        )
        return cls(
            observed_at_ms=observed_at_ms,
            evidence_ts_ms=_bounded_evidence_timestamp(
                payload.get("evidence_ts_ms"),
                observed_at_ms=observed_at_ms,
            ),
            source_instance_id=_source_instance_id(
                payload.get("source_instance_id")
            ),
            source_revision=_source_revision(payload.get("source_revision")),
            stats=stats,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at_ms": self.observed_at_ms,
            "evidence_ts_ms": self.evidence_ts_ms,
            "source_instance_id": self.source_instance_id,
            "source_revision": self.source_revision,
            "stats": dict(self.stats),
        }

    def as_projection_payload(self) -> dict[str, Any]:
        return dict(self.stats)


@dataclass(frozen=True, slots=True)
class HostGovernanceSourceSnapshot:
    observed_at_ms: int
    evidence_ts_ms: int
    source_instance_id: str
    source_revision: int
    governance: Mapping[str, Any]
    schema_version: str = HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "HostGovernanceSourceSnapshot":
        _require_schema(payload, HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION)
        observed_at_ms = _positive_timestamp(payload.get("observed_at_ms"))
        governance = _mapping(
            payload.get("governance"),
            "HOST_OBS_GOVERNANCE_INVALID",
        )
        if (
            str(governance.get("schema_version") or "")
            != "Natsume.governance.snapshot.v1"
        ):
            raise HostObsSourceContractError("HOST_OBS_GOVERNANCE_INVALID")
        _validate_known_timestamp(
            governance,
            "generated_at_ms",
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_GOVERNANCE_INVALID",
            required=True,
        )
        _validate_timestamp_tree(
            governance,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_GOVERNANCE_INVALID",
        )
        return cls(
            observed_at_ms=observed_at_ms,
            evidence_ts_ms=_bounded_evidence_timestamp(
                payload.get("evidence_ts_ms"),
                observed_at_ms=observed_at_ms,
            ),
            source_instance_id=_source_instance_id(
                payload.get("source_instance_id")
            ),
            source_revision=_source_revision(payload.get("source_revision")),
            governance=governance,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at_ms": self.observed_at_ms,
            "evidence_ts_ms": self.evidence_ts_ms,
            "source_instance_id": self.source_instance_id,
            "source_revision": self.source_revision,
            "governance": dict(self.governance),
        }

    def as_projection_payload(self) -> dict[str, Any]:
        return dict(self.governance)


@dataclass(frozen=True, slots=True)
class HostRuntimeSourceSnapshot:
    observed_at_ms: int
    evidence_ts_ms: int
    source_instance_id: str
    source_revision: int
    organ_tribe: Mapping[str, Any]
    scheduler: Mapping[str, Any]
    schema_version: str = HOST_RUNTIME_SOURCE_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HostRuntimeSourceSnapshot":
        _require_schema(payload, HOST_RUNTIME_SOURCE_SCHEMA_VERSION)
        organ_tribe = _mapping(
            payload.get("organ_tribe"),
            "HOST_OBS_ORGAN_TRIBE_INVALID",
        )
        agents = organ_tribe.get("agents")
        agent_rows = _mapping_items(
            agents,
            reason_code="HOST_OBS_ORGAN_TRIBE_INVALID",
            limit=256,
        )
        observed_at_ms = _positive_timestamp(payload.get("observed_at_ms"))
        for key in ("observed_at_ms", "timestamp", "evidence_ts_ms"):
            _validate_known_timestamp(
                organ_tribe,
                key,
                observed_at_ms=observed_at_ms,
                reason_code="HOST_OBS_ORGAN_TRIBE_INVALID",
            )
        for agent in agent_rows:
            _validate_known_timestamp(
                agent,
                "last_updated_ts_ms",
                observed_at_ms=observed_at_ms,
                reason_code="HOST_OBS_ORGAN_TRIBE_INVALID",
            )
        scheduler = _mapping(
            payload.get("scheduler"),
            "HOST_OBS_SCHEDULER_SOURCE_INVALID",
        )
        for key in (
            "evidence_ts_ms",
            "updated_at_ms",
            "last_updated_ts_ms",
            "timestamp",
            "ts_ms",
        ):
            _validate_known_timestamp(
                scheduler,
                key,
                observed_at_ms=observed_at_ms,
                reason_code="HOST_OBS_SCHEDULER_SOURCE_INVALID",
            )
        clock = scheduler.get("clock")
        if clock is not None:
            clock_payload = _mapping(
                clock,
                "HOST_OBS_SCHEDULER_SOURCE_INVALID",
            )
            _validate_known_timestamp(
                clock_payload,
                "last_tick_ms",
                observed_at_ms=observed_at_ms,
                reason_code="HOST_OBS_SCHEDULER_SOURCE_INVALID",
            )
        _validate_timestamp_tree(
            organ_tribe,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_ORGAN_TRIBE_INVALID",
        )
        _validate_timestamp_tree(
            scheduler,
            observed_at_ms=observed_at_ms,
            reason_code="HOST_OBS_SCHEDULER_SOURCE_INVALID",
        )
        return cls(
            observed_at_ms=observed_at_ms,
            evidence_ts_ms=_bounded_evidence_timestamp(
                payload.get("evidence_ts_ms"),
                observed_at_ms=observed_at_ms,
            ),
            source_instance_id=_source_instance_id(
                payload.get("source_instance_id")
            ),
            source_revision=_source_revision(payload.get("source_revision")),
            organ_tribe=organ_tribe,
            scheduler=scheduler,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at_ms": self.observed_at_ms,
            "evidence_ts_ms": self.evidence_ts_ms,
            "source_instance_id": self.source_instance_id,
            "source_revision": self.source_revision,
            "organ_tribe": dict(self.organ_tribe),
            "scheduler": dict(self.scheduler),
        }


__all__ = [
    "HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION",
    "HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION",
    "HOST_MODE_SOURCE_SCHEMA_VERSION",
    "HOST_RUNTIME_SOURCE_SCHEMA_VERSION",
    "HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION",
    "HostCapabilityForgeSourceSnapshot",
    "HostGovernanceSourceSnapshot",
    "HostModeSourceSnapshot",
    "HostObsSourceContractError",
    "HostRuntimeSourceSnapshot",
    "HostSemanticRecallSourceSnapshot",
]
