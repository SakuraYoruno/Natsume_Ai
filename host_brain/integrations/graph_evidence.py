"""Compatibility facade for the catalog-owned Graph Evidence repository."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Any, TypeVar

from host_brain.core.ids import now_ms
from host_brain.integrations.evidence_settings import (
    EvidenceSettings,
    load_evidence_settings,
)
from host_brain.persistence.graph_evidence_repository import (
    GRAPH_EVIDENCE_PERSISTENCE_FAILED,
    GRAPH_EVIDENCE_SCHEMA_NOT_READY,
    GraphEvidenceRepository,
    GraphEvidenceRepositoryError,
)


logger = logging.getLogger(__name__)

_SINGLETON: EvidencePlane | None = None
_SINGLETON_LOCK = threading.Lock()
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class EvidencePlaneStatus:
    available: bool
    schema_authorized: bool
    degraded: bool
    reason_code: str | None = None
    detail: str | None = None


def get_evidence_plane() -> EvidencePlane:
    """Return the process-wide, lazily configured Evidence Plane facade."""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = EvidencePlane()
    return _SINGLETON


class EvidencePlane:
    """Stable integration API with explicit degraded and authority states."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        settings: EvidenceSettings | None = None,
        repository: GraphEvidenceRepository | None = None,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 3_000,
    ) -> None:
        if settings is not None and db_path is not None:
            raise ValueError("pass either db_path or settings, not both")
        resolved = settings or load_evidence_settings(
            db_path=db_path,
            timeout_ms=timeout_ms,
        )
        self._db_path = resolved.db_path
        self._repository: GraphEvidenceRepository | None = repository
        self._status = EvidencePlaneStatus(
            available=False,
            schema_authorized=False,
            degraded=True,
            reason_code=GRAPH_EVIDENCE_SCHEMA_NOT_READY,
            detail="graph evidence repository has not been initialized",
        )
        if self._repository is None:
            try:
                self._repository = GraphEvidenceRepository(
                    resolved.db_path,
                    clock=clock,
                    timeout_ms=resolved.timeout_ms,
                )
            except GraphEvidenceRepositoryError as exc:
                self._record_initialization_failure(exc)
                return
            except Exception as exc:
                self._record_initialization_failure(
                    GraphEvidenceRepositoryError(
                        GRAPH_EVIDENCE_PERSISTENCE_FAILED,
                        str(exc),
                    )
                )
                return
        self._status = EvidencePlaneStatus(
            available=True,
            schema_authorized=True,
            degraded=False,
        )

    @property
    def status(self) -> EvidencePlaneStatus:
        return self._status

    @property
    def available(self) -> bool:
        return self._status.available

    @property
    def _initialized(self) -> bool:
        """Read-only compatibility alias for older callers and tests."""
        return self._repository is not None and self._status.schema_authorized

    def _record_initialization_failure(
        self,
        exc: GraphEvidenceRepositoryError,
    ) -> None:
        schema_failure = exc.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY
        self._repository = None
        self._status = EvidencePlaneStatus(
            available=False,
            schema_authorized=False,
            degraded=True,
            reason_code=exc.reason_code,
            detail=exc.detail,
        )
        if schema_failure:
            logger.error(
                "EvidencePlane schema authority verification failed: %s (%s)",
                exc.detail,
                exc.reason_code,
            )
        else:
            logger.warning(
                "EvidencePlane persistence initialization degraded: %s (%s)",
                exc.detail,
                exc.reason_code,
            )

    def _record_operation_failure(self, operation: str, exc: Exception) -> None:
        reason_code = getattr(exc, "reason_code", GRAPH_EVIDENCE_PERSISTENCE_FAILED)
        detail = getattr(exc, "detail", str(exc))
        self._status = EvidencePlaneStatus(
            available=self._repository is not None,
            schema_authorized=self._repository is not None,
            degraded=True,
            reason_code=str(reason_code),
            detail=str(detail),
        )
        logger.warning(
            "EvidencePlane.%s degraded: %s (%s)",
            operation,
            detail,
            reason_code,
        )

    def _write(self, operation: str, action: Callable[[GraphEvidenceRepository], Any]) -> None:
        repository = self._repository
        if repository is None:
            return
        try:
            action(repository)
        except Exception as exc:
            self._record_operation_failure(operation, exc)

    def _query(
        self,
        operation: str,
        action: Callable[[GraphEvidenceRepository], _T],
        fallback: _T,
    ) -> _T:
        repository = self._repository
        if repository is None:
            return fallback
        try:
            return action(repository)
        except Exception as exc:
            self._record_operation_failure(operation, exc)
            return fallback

    def write_trace(
        self,
        *,
        request_id: str,
        graph_id: str,
        trace: list[dict[str, Any]],
    ) -> None:
        if not trace:
            return
        self._write(
            "write_trace",
            lambda repository: repository.append_traces(
                request_id=request_id,
                graph_id=graph_id,
                trace=trace,
            ),
        )

    def write_subgraph_summary(
        self,
        *,
        request_id: str,
        summary: dict[str, Any],
    ) -> None:
        if not summary:
            return
        self._write(
            "write_subgraph_summary",
            lambda repository: repository.append_subgraph_summary(
                request_id=request_id,
                summary=summary,
            ),
        )

    def write_clock_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        clock_events: list[dict[str, Any]],
    ) -> None:
        if not clock_events:
            return
        self._write(
            "write_clock_events",
            lambda repository: repository.append_clock_events(
                request_id=request_id,
                graph_id=graph_id,
                events=clock_events,
            ),
        )

    def write_branch_manifest(
        self,
        *,
        request_id: str,
        graph_id: str,
        manifest: dict[str, Any],
    ) -> None:
        if not manifest:
            return
        self._write(
            "write_branch_manifest",
            lambda repository: repository.append_branch_manifest(
                request_id=request_id,
                graph_id=graph_id,
                manifest=manifest,
            ),
        )

    def query_branch_manifest(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_branch_manifest",
            lambda repository: repository.list_branch_manifests(request_id=request_id),
            [],
        )

    def write_stream_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        if not events:
            return
        self._write(
            "write_stream_events",
            lambda repository: repository.append_stream_events(
                request_id=request_id,
                graph_id=graph_id,
                events=events,
            ),
        )

    def query_stream_events(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_stream_events",
            lambda repository: repository.list_stream_events(request_id=request_id),
            [],
        )

    def query_stream_events_by_time(
        self,
        *,
        since_ms: int,
        end_ms: int,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._query(
            "query_stream_events_by_time",
            lambda repository: repository.list_stream_events_by_time(
                since_ms=since_ms,
                end_ms=end_ms,
                event_types=event_types,
            ),
            [],
        )

    def write_checkpoint(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        graph_id: str,
        interrupt_reason: str = "",
        resume_deadline_at: int = 0,
    ) -> None:
        self._write(
            "write_checkpoint",
            lambda repository: repository.save_checkpoint(
                checkpoint_id=checkpoint_id,
                request_id=request_id,
                graph_id=graph_id,
                interrupt_reason=interrupt_reason,
                resume_deadline_at=resume_deadline_at,
            ),
        )

    def query_checkpoint(self, *, checkpoint_id: str) -> dict[str, Any] | None:
        return self._query(
            "query_checkpoint",
            lambda repository: repository.get_checkpoint(checkpoint_id=checkpoint_id),
            None,
        )

    def write_barrier_join(
        self,
        *,
        request_id: str,
        graph_id: str,
        barrier_join_result: dict[str, Any],
    ) -> None:
        self._write(
            "write_barrier_join",
            lambda repository: repository.append_barrier_join(
                request_id=request_id,
                graph_id=graph_id,
                result=barrier_join_result,
            ),
        )

    def query_barrier_join(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_barrier_join",
            lambda repository: repository.list_barrier_joins(request_id=request_id),
            [],
        )

    def write_subgraph_trace(
        self,
        *,
        run_id: str,
        trace: Sequence[Mapping[str, Any]],
        parent_path: str | None = None,
    ) -> None:
        if not trace:
            return
        self._write(
            "write_subgraph_trace",
            lambda repository: repository.append_subgraph_traces(
                run_id=run_id,
                trace=trace,
                parent_path=parent_path,
            ),
        )

    def query_subgraph_trace(self, *, run_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_subgraph_trace",
            lambda repository: repository.list_subgraph_traces(run_id=run_id),
            [],
        )

    def write_llm_call(
        self,
        *,
        run_id: str,
        node_id: str,
        call: Mapping[str, Any] | None = None,
        model_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        ts_ms: int | None = None,
    ) -> None:
        payload = dict(call or {})
        if model_id is not None:
            payload["model_id"] = model_id
        payload.setdefault("prompt_tokens", prompt_tokens)
        payload.setdefault("completion_tokens", completion_tokens)
        payload.setdefault("latency_ms", latency_ms)
        if ts_ms is not None:
            payload["ts_ms"] = ts_ms
        self._write(
            "write_llm_call",
            lambda repository: repository.append_llm_call(
                run_id=run_id,
                node_id=node_id,
                call=payload,
            ),
        )

    def query_llm_calls(self, *, run_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_llm_calls",
            lambda repository: repository.list_llm_calls(run_id=run_id),
            [],
        )

    def query_trace(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_trace",
            lambda repository: repository.list_traces(request_id=request_id),
            [],
        )

    def query_subgraph_summary(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_subgraph_summary",
            lambda repository: repository.list_subgraph_summaries(request_id=request_id),
            [],
        )

    def query_clock_events(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "query_clock_events",
            lambda repository: repository.list_clock_events(request_id=request_id),
            [],
        )

    def close(self) -> None:
        repository = self._repository
        if repository is not None:
            repository.close()
        self._repository = None
        self._status = EvidencePlaneStatus(
            available=False,
            schema_authorized=self._status.schema_authorized,
            degraded=self._status.degraded,
            reason_code=self._status.reason_code,
            detail=self._status.detail,
        )


__all__ = [
    "EvidencePlane",
    "EvidencePlaneStatus",
    "get_evidence_plane",
]
