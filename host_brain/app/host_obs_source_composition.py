"""Explicit Host-owned sources for the standalone Observability process.

This module is the Host-side composition adapter.  It receives already-owned
runtime providers, builds bounded public snapshots, and never constructs a
service while serving a read request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
import threading
from typing import Any
import uuid

from host_brain.app.host_obs_read_routes import (
    HostObsReadPorts,
    HostObsReadSourceUnavailable,
)
from host_brain.app.observability_runtime.health_device_projection import (
    build_organ_tribe_public_snapshot,
)
from host_brain.observability.projection_store import public_projection_payload
from host_brain.observability.governance_view import build_governance_view
from natsume_contracts.operator import (
    HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
    HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
    HOST_MODE_SOURCE_SCHEMA_VERSION,
    HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
    HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostModeSourceSnapshot,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)


logger = logging.getLogger("Natsume.host.obs_sources")


@dataclass(frozen=True, slots=True)
class HostObsSourceRuntimePorts:
    """Read-only providers captured by one Host application composition."""

    mode_service_provider: Callable[[], Any | None]
    capability_gap_telemetry_provider: Callable[[], Any | None]
    capability_forge_provider: Callable[[], Any | None]
    semantic_recaller_provider: Callable[[], Any | None]
    capability_scorecard_provider: Callable[[], Any | None]
    degradation_state_provider: Callable[[], Mapping[str, Any] | None]
    pentest_environment_provider: Callable[[], Mapping[str, Any]]
    ledger_provider: Callable[[], Any | None]
    scheduler_container_provider: Callable[[], Any | None]
    tribe_state_reader: Callable[[], Mapping[str, Any]]
    source_epoch_provider: Callable[[], "HostObsSourceEpoch | None"]
    clock_ms: Callable[[], int]

    def __post_init__(self) -> None:
        for name in (
            "mode_service_provider",
            "capability_gap_telemetry_provider",
            "capability_forge_provider",
            "semantic_recaller_provider",
            "capability_scorecard_provider",
            "degradation_state_provider",
            "pentest_environment_provider",
            "ledger_provider",
            "scheduler_container_provider",
            "tribe_state_reader",
            "source_epoch_provider",
            "clock_ms",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} must be callable")


@dataclass(slots=True)
class HostObsSourceEpoch:
    """One Host lifespan epoch with monotonic per-contract revisions."""

    source_instance_id: str
    _revisions: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    @classmethod
    def create(cls) -> "HostObsSourceEpoch":
        return cls(source_instance_id=f"host-{uuid.uuid4().hex}")

    def __post_init__(self) -> None:
        source_id = str(self.source_instance_id or "").strip()
        if not source_id:
            raise ValueError("source_instance_id is required")
        self.source_instance_id = source_id

    def next_revision(self, source_name: str) -> int:
        name = str(source_name or "").strip()
        if not name:
            raise ValueError("source_name is required")
        with self._lock:
            revision = self._revisions.get(name, 0) + 1
            self._revisions[name] = revision
            return revision


def build_host_obs_read_ports(
    runtime: HostObsSourceRuntimePorts,
    *,
    environ: Mapping[str, str] | None = None,
) -> HostObsReadPorts:
    """Build the authenticated route ports without resolving runtime services."""

    if not isinstance(runtime, HostObsSourceRuntimePorts):
        raise TypeError("runtime must be HostObsSourceRuntimePorts")
    return HostObsReadPorts.from_environment(
        mode_source_reader=lambda: _read_mode(runtime),
        capability_forge_source_reader=lambda: _read_capability_forge(runtime),
        semantic_recall_source_reader=lambda: _read_semantic_recall(runtime),
        governance_source_reader=lambda: _read_governance(runtime),
        runtime_source_reader=lambda: _read_runtime(runtime),
        environ=environ,
    )


def _read_mode(runtime: HostObsSourceRuntimePorts) -> HostModeSourceSnapshot:
    try:
        service = runtime.mode_service_provider()
        if service is None:
            raise RuntimeError("mode transition service is not active")
        observed_at_ms = _positive_clock(runtime)
        status = dict(service.get_current_status() or {})
        history = [
            dict(item)
            for item in list(service.get_history(limit=100) or [])
            if isinstance(item, Mapping)
        ]
        evidence_ts_ms = _latest_bounded_timestamp(
            observed_at_ms,
            status.get("ts_ms"),
            status.get("last_interaction_ts_ms"),
            *(item.get("ts_ms") for item in history),
        )
        payload = public_projection_payload(
            {
                "schema_version": HOST_MODE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": observed_at_ms,
                "evidence_ts_ms": evidence_ts_ms,
                **_next_source_position(runtime, "mode"),
                "status": status,
                "history": history,
            }
        )
        return HostModeSourceSnapshot.from_mapping(payload)
    except Exception:
        raise HostObsReadSourceUnavailable("HOST_OBS_MODE_RUNTIME_NOT_READY") from None


def _read_capability_forge(
    runtime: HostObsSourceRuntimePorts,
) -> HostCapabilityForgeSourceSnapshot:
    try:
        telemetry = runtime.capability_gap_telemetry_provider()
        forge = runtime.capability_forge_provider()
        ledger = runtime.ledger_provider()
        if telemetry is None or forge is None or ledger is None:
            raise RuntimeError("capability services are not active")
        gap = dict(
            telemetry.snapshot(
                limit_recent=200,
                limit_hotspots=50,
                limit_candidates=200,
                min_candidate_detected=1,
                min_candidate_sessions=1,
                min_candidate_failure_rate=0.0,
                min_candidate_failed=0,
            )
            or {}
        )
        observed_at_ms = _positive_clock(runtime)
        durable_rows = list(ledger.get_latest_capability_forge_proposals(limit=200))
        proposals: list[dict[str, Any]] = []
        for row in durable_rows:
            if not isinstance(row, Mapping):
                continue
            proposal = row.get("payload")
            if not isinstance(proposal, Mapping):
                continue
            proposal_id = str(proposal.get("proposal_id") or "").strip()
            if proposal_id:
                proposals.append(dict(proposal))
        runtime_stats = dict(forge.runtime_match_stats() or {})
        runtime_tuning = dict(forge.runtime_tuning_stats(limit=200) or {})
        runtime_quality = dict(forge.runtime_quality_stats(limit=200) or {})
        # Capability v1 retains these child mappings for wire compatibility only.
        # Their authorities and projection jobs use independent endpoints, so a
        # child failure must degrade its compatibility field without taking the
        # Capability Forge source down with it.
        try:
            semantic_recall = _semantic_recall_stats(
                runtime.semantic_recaller_provider(),
                observed_at_ms=observed_at_ms,
            )
        except Exception:
            semantic_recall = _compat_source_unavailable(
                "HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY",
                observed_at_ms=observed_at_ms,
            )
        # Governance has its own authenticated source and projection job.  The
        # capability projection discards this compatibility child before it is
        # materialized, so rebuilding the complete governance view here only
        # duplicated an expensive registry walk and could make an otherwise
        # healthy Capability Forge source miss the Obs transport deadline.
        governance = _compat_source_delegated(
            "/internal/obs-sources/governance",
            observed_at_ms=observed_at_ms,
        )
        # Some read-only providers timestamp the projection they build.  Take
        # the envelope clock after all providers have returned so their fresh
        # evidence cannot race a few milliseconds ahead of ``observed_at_ms``.
        observed_at_ms = _positive_clock(runtime)
        evidence_ts_ms = _latest_bounded_timestamp(
            observed_at_ms,
            gap.get("timestamp"),
            runtime_stats.get("timestamp"),
            runtime_tuning.get("timestamp"),
            runtime_quality.get("timestamp"),
            *(
                item.get("updated_ts_ms") or item.get("created_ts_ms")
                for item in proposals
            ),
        )
        payload = public_projection_payload(
            {
                "schema_version": HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": observed_at_ms,
                "evidence_ts_ms": evidence_ts_ms,
                **_next_source_position(runtime, "capability-forge"),
                "gap": gap,
                "proposals": proposals,
                "runtime_stats": runtime_stats,
                "runtime_tuning": runtime_tuning,
                "runtime_quality": runtime_quality,
                "semantic_recall": semantic_recall,
                "governance": governance,
            }
        )
        return HostCapabilityForgeSourceSnapshot.from_mapping(payload)
    except Exception:
        logger.warning("HOST_OBS_CAPABILITY_SOURCE_READ_FAILED", exc_info=True)
        raise HostObsReadSourceUnavailable(
            "HOST_OBS_CAPABILITY_RUNTIME_NOT_READY"
        ) from None


def _read_semantic_recall(
    runtime: HostObsSourceRuntimePorts,
) -> HostSemanticRecallSourceSnapshot:
    try:
        observed_at_ms = _positive_clock(runtime)
        stats = _semantic_recall_stats(
            runtime.semantic_recaller_provider(),
            observed_at_ms=observed_at_ms,
        )
        payload = public_projection_payload(
            {
                "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": observed_at_ms,
                "evidence_ts_ms": observed_at_ms,
                **_next_source_position(runtime, "semantic-recall"),
                "stats": stats,
            }
        )
        return HostSemanticRecallSourceSnapshot.from_mapping(payload)
    except Exception:
        raise HostObsReadSourceUnavailable(
            "HOST_OBS_SEMANTIC_RECALL_RUNTIME_NOT_READY"
        ) from None


def _read_governance(
    runtime: HostObsSourceRuntimePorts,
) -> HostGovernanceSourceSnapshot:
    try:
        projection_started_at_ms = _positive_clock(runtime)
        governance = _governance_snapshot(
            runtime,
            observed_at_ms=projection_started_at_ms,
        )
        observed_at_ms = _positive_clock(runtime)
        payload = public_projection_payload(
            {
                "schema_version": HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": observed_at_ms,
                "evidence_ts_ms": observed_at_ms,
                **_next_source_position(runtime, "governance"),
                "governance": governance,
            }
        )
        return HostGovernanceSourceSnapshot.from_mapping(payload)
    except Exception:
        logger.warning("HOST_OBS_GOVERNANCE_SOURCE_READ_FAILED", exc_info=True)
        raise HostObsReadSourceUnavailable(
            "HOST_OBS_GOVERNANCE_RUNTIME_NOT_READY"
        ) from None


def _read_runtime(runtime: HostObsSourceRuntimePorts) -> HostRuntimeSourceSnapshot:
    try:
        projection_started_at_ms = _positive_clock(runtime)
        scheduler = runtime.scheduler_container_provider()
        if scheduler is None:
            scheduler_snapshot: dict[str, Any] = {
                "available": False,
                "reason_code": "HOST_SCHEDULER_RUNTIME_NOT_READY",
            }
        else:
            scheduler_snapshot = {"available": True, **dict(scheduler.health() or {})}
            clock = getattr(scheduler, "clock", None)
            clock_health = (
                dict(clock.health() or {})
                if clock is not None and callable(getattr(clock, "health", None))
                else {}
            )
            scheduler_snapshot["clock"] = clock_health
            scheduler_snapshot["evidence_ts_ms"] = _latest_bounded_timestamp(
                projection_started_at_ms,
                clock_health.get("last_tick_ms"),
                scheduler_snapshot.get("updated_at_ms"),
                scheduler_snapshot.get("timestamp"),
            )
        observed_at_ms = _positive_clock(runtime)
        # Re-evaluate the bounded scheduler evidence against the final
        # envelope clock in case a tick landed while health was sampled.
        if scheduler is not None:
            scheduler_snapshot["evidence_ts_ms"] = _latest_bounded_timestamp(
                observed_at_ms,
                dict(scheduler_snapshot.get("clock") or {}).get("last_tick_ms"),
                scheduler_snapshot.get("updated_at_ms"),
                scheduler_snapshot.get("timestamp"),
            )
        organ_tribe, evidence_ts_ms = build_organ_tribe_public_snapshot(
            registry_snapshots=dict(runtime.tribe_state_reader() or {}),
            scheduler_snapshot=scheduler_snapshot,
            observed_at_ms=observed_at_ms,
        )
        payload = public_projection_payload(
            {
                "schema_version": HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": observed_at_ms,
                "evidence_ts_ms": evidence_ts_ms,
                **_next_source_position(runtime, "runtime"),
                "organ_tribe": organ_tribe,
                "scheduler": scheduler_snapshot,
            }
        )
        return HostRuntimeSourceSnapshot.from_mapping(payload)
    except Exception:
        logger.warning("HOST_OBS_RUNTIME_SOURCE_READ_FAILED", exc_info=True)
        raise HostObsReadSourceUnavailable("HOST_OBS_RUNTIME_NOT_READY") from None


def _positive_clock(runtime: HostObsSourceRuntimePorts) -> int:
    value = int(runtime.clock_ms())
    if value <= 0:
        raise ValueError("clock must return a positive epoch timestamp")
    return value


def _semantic_recall_stats(
    recaller: Any | None,
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    reader = getattr(recaller, "public_runtime_stats", None)
    if not callable(reader):
        return {
            "available": False,
            "reason_code": "HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY",
            "status": "SOURCE_UNAVAILABLE",
            "timestamp": observed_at_ms,
        }
    payload = reader(observed_at_ms=observed_at_ms)
    if not isinstance(payload, Mapping):
        raise TypeError("semantic recall stats must be a mapping")
    return {"available": True, **dict(payload)}


def _compat_source_unavailable(
    reason_code: str,
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason_code": reason_code,
        "status": "SOURCE_UNAVAILABLE",
        "timestamp": observed_at_ms,
    }


def _compat_source_delegated(
    source_path: str,
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    path = str(source_path or "").strip()
    if not path.startswith("/internal/obs-sources/"):
        raise ValueError("delegated Host Obs source path is invalid")
    return {
        "available": True,
        "status": "DELEGATED",
        "source_path": path,
        "timestamp": observed_at_ms,
    }


def _governance_snapshot(
    runtime: HostObsSourceRuntimePorts,
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    try:
        scorecard_provider = runtime.capability_scorecard_provider()
        list_scorecards = getattr(scorecard_provider, "list_all", None)
        scorecards = list(list_scorecards()) if callable(list_scorecards) else None
    except Exception:
        scorecards = None
    try:
        degradation = runtime.degradation_state_provider()
        degradation = dict(degradation) if isinstance(degradation, Mapping) else None
    except Exception:
        degradation = None
    try:
        pentest_environment = dict(runtime.pentest_environment_provider() or {})
    except Exception:
        pentest_environment = {}
    return build_governance_view(
        capability_scorecards=scorecards,
        degradation_state=degradation,
        identity=None,
        pentest_environment=pentest_environment,
        generated_at_ms=observed_at_ms,
    )


def _next_source_position(
    runtime: HostObsSourceRuntimePorts,
    source_name: str,
) -> dict[str, Any]:
    epoch = runtime.source_epoch_provider()
    if not isinstance(epoch, HostObsSourceEpoch):
        raise RuntimeError("Host observability source epoch is not active")
    return {
        "source_instance_id": epoch.source_instance_id,
        "source_revision": epoch.next_revision(source_name),
    }


def _latest_bounded_timestamp(observed_at_ms: int, *values: Any) -> int:
    evidence = 0
    for value in values:
        try:
            candidate = int(value or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < candidate <= observed_at_ms:
            evidence = max(evidence, candidate)
    return evidence


__all__ = [
    "HostObsSourceEpoch",
    "HostObsSourceRuntimePorts",
    "build_host_obs_read_ports",
]
