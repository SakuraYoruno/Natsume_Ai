"""Explicit production composition for durable Agent git worktrees."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_runtime import (
    WORKSPACE_RUNTIME_BINDING_INVALID,
    DurableWorkspaceRuntime,
    DurableWorkspaceRuntimeError,
)
from host_brain.persistence.workspace_repository import WorkspaceRepository


logger = logging.getLogger(__name__)


PRODUCTION_WORKSPACE_ROOT_REF = "agent-runtime-worktrees"
PRODUCTION_WORKSPACE_SOURCE_KEY = "runtime-repository"
AGENT_WORKSPACE_START_FAILED = "AGENT_WORKSPACE_START_FAILED"
AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED = (
    "AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED"
)
AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED = (
    "AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED"
)


class AgentWorkspaceLifecycleError(RuntimeError):
    """Fail a process lifecycle without discarding retryable runtime handles."""

    def __init__(self, reason_code: str, *, detail: dict[str, Any]) -> None:
        self.reason_code = str(reason_code)
        self.detail = dict(detail)
        super().__init__(self.reason_code)


class WorkspaceCleanupContinuationWorker:
    """Retry durable CLEANUP_PENDING workspaces after live leases expire."""

    def __init__(
        self,
        runtime: DurableWorkspaceRuntime,
        *,
        interval_ms: int = 5_000,
        batch_limit: int = 100,
        observation_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        if int(interval_ms) < 100:
            raise ValueError("workspace cleanup interval must be at least 100ms")
        if int(batch_limit) < 1 or int(batch_limit) > 1_000:
            raise ValueError("workspace cleanup batch limit must be between 1 and 1000")
        self._runtime = runtime
        if observation_sink is not None and not callable(observation_sink):
            raise TypeError("workspace cleanup observation sink must be callable")
        self._observation_sink = observation_sink
        self._interval_s = int(interval_ms) / 1000.0
        self._batch_limit = int(batch_limit)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        runtime_timeout = float(getattr(runtime, "operation_timeout_s", 60.0))
        self._close_timeout_s = max(1.0, runtime_timeout + 1.0)
        self._state_lock = threading.RLock()
        self._last_failure: dict[str, Any] | None = None
        self._last_report: tuple[dict[str, Any], ...] = ()
        self._last_run_ts_ms: int | None = None
        self._last_success_ts_ms: int | None = None
        self._consecutive_failures = 0
        self._guardian_projection_status = (
            "READY" if observation_sink is not None else "UNBOUND"
        )
        self._guardian_projection_error_type: str | None = None
        self._guardian_incident_digest: str | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return bool(self._thread is not None and self._thread.is_alive())

    @property
    def close_timeout_s(self) -> float:
        return self._close_timeout_s

    @property
    def last_failure(self) -> dict[str, Any] | None:
        with self._state_lock:
            return dict(self._last_failure) if self._last_failure is not None else None

    def health_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            running = bool(self._thread is not None and self._thread.is_alive())
            if not running:
                status = "STOPPED"
            elif self._consecutive_failures > 0:
                status = "DEGRADED"
            else:
                status = "HEALTHY"
            return {
                "status": status,
                "running": running,
                "consecutive_failures": self._consecutive_failures,
                "last_run_ts_ms": self._last_run_ts_ms,
                "last_success_ts_ms": self._last_success_ts_ms,
                "last_failure": (
                    dict(self._last_failure)
                    if self._last_failure is not None
                    else None
                ),
                "last_report": tuple(dict(item) for item in self._last_report),
                "close_timeout_s": self._close_timeout_s,
                "guardian_projection_status": self._guardian_projection_status,
                "guardian_projection_error_type": (
                    self._guardian_projection_error_type
                ),
            }

    def run_once(self) -> tuple[dict[str, Any], ...]:
        ts_ms = time.time_ns() // 1_000_000
        try:
            report = self._runtime.continue_pending_cleanups(
                limit=self._batch_limit,
                cancelled=self._stop.is_set,
            )
        except Exception as exc:
            with self._state_lock:
                self._last_run_ts_ms = ts_ms
                self._consecutive_failures += 1
                self._last_failure = {
                    "ts_ms": ts_ms,
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:500],
                }
                consecutive_failures = self._consecutive_failures
                if self._guardian_incident_digest is None:
                    self._guardian_incident_digest = (
                        "sha256:" + secrets.token_hex(32)
                    )
                incident_digest = self._guardian_incident_digest
                should_notify = (
                    consecutive_failures == 1
                    or self._guardian_projection_status == "FAILED"
                )
            if should_notify:
                try:
                    self._notify_failure(
                        error=exc,
                        consecutive_failures=consecutive_failures,
                        incident_digest=incident_digest,
                    )
                except Exception as observer_exc:
                    with self._state_lock:
                        self._guardian_projection_status = "FAILED"
                        self._guardian_projection_error_type = type(
                            observer_exc
                        ).__name__
            raise
        normalized = tuple(dict(item) for item in report)
        # Every successful pass asks the durable observer to resolve owned
        # breaches.  A replacement process has no in-memory failure counter,
        # so gating this call on local state would strand a pre-crash incident.
        if self._observation_sink is not None:
            try:
                self._notify_recovery()
            except Exception as exc:
                with self._state_lock:
                    self._last_run_ts_ms = ts_ms
                    self._consecutive_failures = max(
                        1,
                        self._consecutive_failures,
                    )
                    self._last_failure = {
                        "ts_ms": ts_ms,
                        "error_type": type(exc).__name__,
                        "detail": (
                            "Workspace cleanup recovery could not be "
                            "projected durably"
                        ),
                    }
                    self._guardian_projection_status = "FAILED"
                    self._guardian_projection_error_type = type(exc).__name__
                raise RuntimeError(
                    "AGENT_WORKSPACE_CLEANUP_GUARDIAN_PROJECTION_FAILED"
                ) from exc
        with self._state_lock:
            self._last_run_ts_ms = ts_ms
            self._last_success_ts_ms = ts_ms
            self._consecutive_failures = 0
            self._last_report = normalized
            self._guardian_incident_digest = None
            if self._observation_sink is not None:
                self._guardian_projection_status = "READY"
                self._guardian_projection_error_type = None
        return normalized

    def _notify_failure(
        self,
        *,
        error: Exception,
        consecutive_failures: int,
        incident_digest: str,
    ) -> None:
        if self._observation_sink is None:
            return
        self._observation_sink(
            {
                "schema": "natsume.agent_workspace.cleanup_observation.v1",
                "status": "FAILED",
                "error_type": type(error).__name__,
                # This random opaque identity is stable for one consecutive
                # failure sequence and is never derived from private details.
                "error_digest": incident_digest,
                "consecutive_failures": int(consecutive_failures),
            }
        )
        with self._state_lock:
            self._guardian_projection_status = "READY"
            self._guardian_projection_error_type = None

    def _notify_recovery(self) -> None:
        if self._observation_sink is None:
            return
        self._observation_sink(
            {
                "schema": "natsume.agent_workspace.cleanup_observation.v1",
                "status": "RECOVERED",
            }
        )

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()

            def loop() -> None:
                while not self._stop.wait(self._interval_s):
                    try:
                        self.run_once()
                    except Exception:
                        # Failure is retained in the health snapshot.  One bad
                        # repository/Git cycle must not permanently stop cleanup.
                        continue

            self._thread = threading.Thread(
                target=loop,
                name="agent-workspace-cleanup-continuation",
                daemon=True,
            )
            self._thread.start()

    def close(self, *, timeout_s: float | None = None) -> None:
        self._stop.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None:
            configured_timeout = (
                self._close_timeout_s
                if timeout_s is None
                else max(0.0, float(timeout_s))
            )
            thread.join(timeout=configured_timeout)
            if thread.is_alive():
                raise RuntimeError("AGENT_WORKSPACE_CLEANUP_WORKER_STOP_TIMEOUT")
        with self._state_lock:
            if self._thread is thread:
                self._thread = None


@dataclass(slots=True)
class ProcessAgentWorkspaceContainer:
    repository: WorkspaceRepository
    allocator: WorkspaceAllocator
    runtime: DurableWorkspaceRuntime
    managed_root: Path
    source_repository: Path
    cleanup_continuation_report: tuple[dict[str, Any], ...]
    cleanup_worker: WorkspaceCleanupContinuationWorker

    def start(self) -> None:
        self.cleanup_worker.start()

    def close(self) -> None:
        self.cleanup_worker.close()


def start_agent_workspace_application_scope(
    application: Any,
    container: ProcessAgentWorkspaceContainer,
) -> None:
    """Publish only a successfully started workspace container to ASGI state."""

    application.state.agent_workspace_container = None
    application.state.agent_workspace_shutdown_error = None
    try:
        container.start()
    except Exception as exc:
        close_error: dict[str, str] | None = None
        try:
            container.close()
        except Exception as close_exc:
            close_error = {
                "error_type": type(close_exc).__name__,
                "detail": str(close_exc)[:500],
            }
        detail: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "detail": str(exc)[:500],
        }
        if close_error is not None:
            detail["close_error"] = close_error
            retry_detail = {
                "reason_code": AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED,
                "retry_required": True,
                "start_error": {
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:500],
                },
                "close_error": close_error,
            }
            # start() may have published a live worker before failing.  Until
            # close succeeds, the container remains an explicit retry handle.
            application.state.agent_workspace_container = container
            application.state.agent_workspace_shutdown_error = retry_detail
            raise AgentWorkspaceLifecycleError(
                AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED,
                detail=retry_detail,
            ) from exc
        # A failed startup that quiesced successfully is not an authority.
        application.state.agent_workspace_container = None
        application.state.agent_workspace_shutdown_error = None
        raise AgentWorkspaceLifecycleError(
            AGENT_WORKSPACE_START_FAILED,
            detail=detail,
        ) from exc
    application.state.agent_workspace_container = container


def compose_agent_workspace_application_scope(
    application: Any,
    task_runtime_container: Any,
    runtime_settings: Any,
    runtime_owner_id: str,
    *,
    cleanup_observer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> ProcessAgentWorkspaceContainer | None:
    """Build and publish the optional W2 runtime without hiding retry handles."""

    try:
        from host_brain.agent_runtime.artifact_store import REPO_ROOT  # noqa: PLC0415

        container = build_process_agent_workspace_container(
            canonical_db_path=task_runtime_container.canonical_db_path,
            managed_root=runtime_settings.agent_workspace_managed_root,
            source_repository=REPO_ROOT,
            runtime_owner_id=runtime_owner_id,
            lease_ms=runtime_settings.agent_workspace_lease_ms,
            heartbeat_interval_ms=runtime_settings.agent_workspace_heartbeat_ms,
            cleanup_interval_ms=runtime_settings.agent_workspace_cleanup_poll_ms,
            cleanup_observer=cleanup_observer,
            token_secret=(
                runtime_settings.agent_workspace_hmac_secret
                .get_secret_value()
                .encode("utf-8")
            ),
        )
        deferred_cleanup = [
            item
            for item in container.cleanup_continuation_report
            if str(item.get("status") or "") != "CLEANED"
        ]
        if deferred_cleanup:
            logger.warning(
                "AGENT_WORKSPACE_CLEANUP_CONTINUATION_DEFERRED:%s",
                deferred_cleanup,
            )
        start_agent_workspace_application_scope(application, container)
        return container
    except AgentWorkspaceLifecycleError as exc:
        if exc.reason_code == AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED:
            # A failed rollback can still own a live worker.  Retain both
            # authority handles and block Host startup so shutdown is retryable.
            application.state.task_runtime_container = task_runtime_container
            logger.critical(
                "AGENT_WORKSPACE_START_ROLLBACK_FAILED_RETRY_REQUIRED",
                exc_info=True,
            )
            raise
        application.state.agent_workspace_container = None
        application.state.agent_workspace_shutdown_error = None
        logger.error(
            "AGENT_WORKSPACE_PRODUCTION_COMPOSITION_UNAVAILABLE",
            exc_info=True,
        )
        return None
    except Exception:
        application.state.agent_workspace_container = None
        application.state.agent_workspace_shutdown_error = None
        logger.error(
            "AGENT_WORKSPACE_PRODUCTION_COMPOSITION_UNAVAILABLE",
            exc_info=True,
        )
        return None


def quiesce_agent_workspace_application_scope(
    application: Any,
    container: ProcessAgentWorkspaceContainer | None,
    task_runtime_container: Any,
) -> None:
    """Stop workspace cleanup after Task execution drains; retain failed handles."""

    if container is None:
        application.state.agent_workspace_container = None
        application.state.agent_workspace_shutdown_error = None
        return
    try:
        container.close()
    except Exception as exc:
        shutdown_error = {
            "reason_code": AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED,
            "retry_required": True,
            "error_type": type(exc).__name__,
            "detail": str(exc)[:500],
        }
        # The container and Task Store remain one retryable authority bundle.
        application.state.agent_workspace_container = container
        application.state.task_runtime_container = task_runtime_container
        application.state.agent_workspace_shutdown_error = shutdown_error
        raise AgentWorkspaceLifecycleError(
            AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED,
            detail=shutdown_error,
        ) from exc
    application.state.agent_workspace_container = None
    application.state.agent_workspace_shutdown_error = None


def build_process_agent_workspace_container(
    *,
    canonical_db_path: str | Path,
    managed_root: str | Path,
    source_repository: str | Path,
    runtime_owner_id: str,
    lease_ms: int = 120_000,
    heartbeat_interval_ms: int | None = None,
    cleanup_interval_ms: int = 5_000,
    cleanup_observer: Callable[[Mapping[str, Any]], Any] | None = None,
    token_secret: bytes,
) -> ProcessAgentWorkspaceContainer:
    """Build one process-local secret holder over durable W1 authority."""

    if not isinstance(token_secret, bytes) or len(token_secret) < 32:
        # Validate the explicit opt-in before creating the managed root or
        # opening Git/SQLite resources.  Default fail-closed startup must not
        # leave an otherwise unused workspace directory behind.
        raise DurableWorkspaceRuntimeError(
            WORKSPACE_RUNTIME_BINDING_INVALID,
            "workspace token secret must contain at least 32 bytes",
        )
    root = Path(managed_root).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    source = Path(source_repository).expanduser().resolve(strict=True)
    repository = WorkspaceRepository(canonical_db_path)
    allocator = WorkspaceAllocator(
        repository=repository,
        managed_roots={
            PRODUCTION_WORKSPACE_ROOT_REF: ManagedWorkspaceRoot(
                root_ref=PRODUCTION_WORKSPACE_ROOT_REF,
                path=root,
                isolation_modes=frozenset({"DIRECTORY_FENCE"}),
            )
        },
        git_sources={
            PRODUCTION_WORKSPACE_SOURCE_KEY: ManagedGitSource(
                source_key=PRODUCTION_WORKSPACE_SOURCE_KEY,
                source_ref_prefix=f"git-worktree://{PRODUCTION_WORKSPACE_SOURCE_KEY}",
                repository=source,
            )
        },
    )
    runtime = DurableWorkspaceRuntime(
        repository=repository,
        allocator=allocator,
        runtime_owner_id=runtime_owner_id,
        managed_root_ref=PRODUCTION_WORKSPACE_ROOT_REF,
        git_source_key=PRODUCTION_WORKSPACE_SOURCE_KEY,
        lease_ms=lease_ms,
        heartbeat_interval_ms=heartbeat_interval_ms,
        token_secret=token_secret,
    )
    # Keep Host startup bounded to one cleanup operation.  The returned row
    # preserves startup continuation evidence/fail-closed reporting; the
    # composition-owned worker resumes the remaining durable inventory after
    # the application scope starts.
    recovery = runtime.continue_pending_cleanups(limit=1)
    cleanup_worker = WorkspaceCleanupContinuationWorker(
        runtime,
        interval_ms=cleanup_interval_ms,
        observation_sink=cleanup_observer,
    )
    return ProcessAgentWorkspaceContainer(
        repository=repository,
        allocator=allocator,
        runtime=runtime,
        managed_root=root,
        source_repository=source,
        cleanup_continuation_report=recovery,
        cleanup_worker=cleanup_worker,
    )


__all__ = [
    "AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED",
    "AGENT_WORKSPACE_START_FAILED",
    "AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED",
    "AgentWorkspaceLifecycleError",
    "PRODUCTION_WORKSPACE_ROOT_REF",
    "PRODUCTION_WORKSPACE_SOURCE_KEY",
    "ProcessAgentWorkspaceContainer",
    "WorkspaceCleanupContinuationWorker",
    "build_process_agent_workspace_container",
    "compose_agent_workspace_application_scope",
    "quiesce_agent_workspace_application_scope",
    "start_agent_workspace_application_scope",
]
