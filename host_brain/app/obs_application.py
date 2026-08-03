"""Explicit, application-scoped composition for the standalone Obs app.

The observability process is a query/projection sidecar.  It must not install
Host application ports or borrow Host-owned mutable providers merely because
both ASGI applications happen to run in one interpreter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from host_brain.app.observability_runtime.domain_state_projection import (
    refresh_life_evolution_projection,
)
from host_brain.app.observability_runtime.projection_daemon import (
    ProjectionJobDaemon,
)
from host_brain.app.observability_runtime.projection_ledger_view import (
    refresh_ledger_stats_projection,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.digital_life.application_boundary import read_life_snapshot
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.observability.metrics import MetricsCollector
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.app.obs_projection_jobs import (
    DURABLE_READ,
    TYPED_UNAVAILABLE,
    build_explicit_obs_projection_callbacks,
)
from host_brain.app.observability_runtime.host_read_transport import HostReadTransport


OBS_PROJECTION_JOBS_MIGRATED_TO_EXPLICIT_PORTS = (
    "bio-ops-metadata",
    "capability-forge-operator",
    "device-status",
    "evolution-lab-operator",
    "governance-snapshot",
    "health-passive",
    "operator-live-state",
    "organ-tribe",
    "remaining-operator-queries",
    "resource-manager",
    "runtime-snapshot",
    "security-immune",
    "semantic-recall-stats",
    "sessions",
    "skill-pack-catalog",
    "digital-life-overview",
    "insights-summary",
    "life-body",
    "task-runtime",
    "trace",
)

@dataclass(frozen=True, slots=True)
class ObsProjectionJob:
    """One explicitly injected projection callback."""

    name: str
    callback: Callable[[], object]
    source_mode: str = "CALLER_DEFINED"
    min_interval_ms: int = 0
    unavailable_reason_code: str | None = None

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("projection job name is required")
        if not callable(self.callback):
            raise TypeError("projection job callback must be callable")
        if not str(self.source_mode or "").strip():
            raise ValueError("projection job source_mode is required")
        if int(self.min_interval_ms) < 0:
            raise ValueError("projection job min_interval_ms must be non-negative")
        if self.source_mode == TYPED_UNAVAILABLE and not str(
            self.unavailable_reason_code or ""
        ).strip():
            raise ValueError(
                "typed unavailable projection job requires unavailable_reason_code"
            )


class _ObsApplicationPortsLifecycleClaim:
    """Bundle-local, process-local single-use ownership fence."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._claimed = False

    def claim(self) -> None:
        with self._lock:
            if self._claimed:
                raise RuntimeError("OBS_APPLICATION_PORTS_ALREADY_CONSUMED")
            self._claimed = True


@dataclass(frozen=True, slots=True)
class ObsApplicationPorts:
    """Narrow query/projection ports consumed by one Obs application."""

    ledger_query: Any
    query_projection_store: QueryProjectionStore
    metrics_reader: MetricsCollector
    life_snapshot_reader: Callable[[str], Mapping[str, Any]]
    life_action_context_provider: Callable[..., Any] | None = None
    approval_token_validator: Callable[..., Any] | None = None
    degradation_capability_port_provider: Callable[[], Any] | None = None
    projection_jobs: tuple[ObsProjectionJob, ...] = ()
    close_owned_resources: Callable[[], None] | None = None
    projection_coverage: str = "CALLER_DEFINED"
    missing_projection_jobs: tuple[str, ...] = ()
    _lifecycle_claim: _ObsApplicationPortsLifecycleClaim = field(
        default_factory=_ObsApplicationPortsLifecycleClaim,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        canonical_db_path = str(
            getattr(self.ledger_query, "canonical_db_path", "") or ""
        ).strip()
        projection_db_path = str(
            getattr(self.query_projection_store, "db_path", "") or ""
        ).strip()
        if not canonical_db_path or canonical_db_path == ":memory:":
            raise ValueError("Obs ledger query port requires a durable canonical database")
        if Path(canonical_db_path).expanduser().resolve() != Path(
            projection_db_path
        ).expanduser().resolve():
            raise ValueError("Obs query and projection ports must share one canonical database")
        if not callable(self.life_snapshot_reader):
            raise TypeError("life_snapshot_reader must be callable")
        for field_name in (
            "life_action_context_provider",
            "approval_token_validator",
            "degradation_capability_port_provider",
        ):
            provider = getattr(self, field_name)
            if provider is not None and not callable(provider):
                raise TypeError(f"{field_name} must be callable or None")
        if not str(self.projection_coverage or "").strip():
            raise ValueError("projection_coverage is required")
        if any(not str(name or "").strip() for name in self.missing_projection_jobs):
            raise ValueError("missing projection job names must be non-empty")

    def claim_lifecycle_owner(self) -> None:
        """Fence this exact mutable resource bundle to one app lifespan."""

        self._lifecycle_claim.claim()


@dataclass(slots=True)
class ObsApplicationContainer:
    """All mutable lifecycle state owned by one FastAPI app instance."""

    ports: ObsApplicationPorts
    projection_daemon: ProjectionJobDaemon
    closed: bool = False

    @classmethod
    def compose(cls, ports: ObsApplicationPorts) -> "ObsApplicationContainer":
        daemon = ProjectionJobDaemon()
        for job in ports.projection_jobs:
            daemon.register(
                job.name,
                job.callback,
                min_interval_ms=job.min_interval_ms,
            )
        return cls(ports=ports, projection_daemon=daemon)

    def start_projection_jobs(
        self,
        *,
        interval_ms: int = 5_000,
    ) -> dict[str, object]:
        """Start this app's sole projection daemon from its ASGI lifespan."""

        if self.closed:
            raise RuntimeError("OBS_APPLICATION_CONTAINER_CLOSED")
        return self.projection_daemon.start(
            store=self.ports.query_projection_store,
            interval_ms=interval_ms,
        )

    def health_snapshot(self) -> dict[str, object]:
        return {
            "closed": self.closed,
            "projection_daemon": self.projection_daemon.health_snapshot(),
        }

    def close(self) -> dict[str, object]:
        if self.closed:
            return {"running": False, "closed": True}
        status = self.projection_daemon.stop()
        if bool(status.get("running")):
            raise RuntimeError(
                str(status.get("reason_code") or "OBS_PROJECTION_STOP_FAILED")
            )
        close = self.ports.close_owned_resources
        if close is not None:
            close()
        self.closed = True
        return {**status, "closed": True}


def build_default_obs_application_ports(
    *,
    canonical_db_path: str | Path,
) -> ObsApplicationPorts:
    """Build the standalone sidecar without touching Host process globals."""

    raw_path = str(canonical_db_path or "").strip()
    if not raw_path or raw_path == ":memory:":
        raise ValueError("standalone Obs requires a durable canonical database")
    db_path = Path(raw_path).expanduser().resolve()
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="obs-application-startup",
    )
    if not migrated.success:
        raise RuntimeError(
            f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: {migrated.detail}"
        )

    ledger = SQLiteLedger(
        db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    try:
        store = QueryProjectionStore(db_path)
        metrics = MetricsCollector(ledger=ledger)
        life_reader = LifeSnapshotReader(LifeCommandRepository.from_ledger(ledger))

        def life_snapshot(person_id: str) -> Mapping[str, Any]:
            return read_life_snapshot(person_id, reader=life_reader)

        jobs = (
            ObsProjectionJob(
                "ledger-stats",
                lambda: refresh_ledger_stats_projection(ledger=ledger, store=store),
                source_mode=DURABLE_READ,
                min_interval_ms=30_000,
            ),
            ObsProjectionJob(
                "life-evolution",
                lambda: refresh_life_evolution_projection(
                    snapshot_reader=life_snapshot,
                    store=store,
                ),
                source_mode=DURABLE_READ,
                min_interval_ms=5_000,
            ),
            *(
                ObsProjectionJob(
                    definition.name,
                    definition.callback,
                    source_mode=definition.source_mode,
                    min_interval_ms=definition.min_interval_ms,
                    unavailable_reason_code=definition.unavailable_reason_code,
                )
                for definition in build_explicit_obs_projection_callbacks(
                    ledger=ledger,
                    store=store,
                    canonical_db_path=db_path,
                    life_snapshot_reader=life_snapshot,
                    host_read_transport=HostReadTransport.from_environment(),
                )
            ),
        )
        return ObsApplicationPorts(
            ledger_query=ledger,
            query_projection_store=store,
            metrics_reader=metrics,
            life_snapshot_reader=life_snapshot,
            projection_jobs=jobs,
            close_owned_resources=ledger.close,
            projection_coverage="REGISTERED_22_OF_22",
            missing_projection_jobs=(),
        )
    except BaseException:
        ledger.close()
        raise


__all__ = [
    "OBS_PROJECTION_JOBS_MIGRATED_TO_EXPLICIT_PORTS",
    "ObsApplicationContainer",
    "ObsApplicationPorts",
    "ObsProjectionJob",
    "build_default_obs_application_ports",
]
