"""Shared failure-evidenced mutation kernel for NE-06 activation services."""

from __future__ import annotations

from typing import Any, Callable

from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)


class EvolutionVersionActivationBase:
    """Own the canonical repository and make failed mutations observable."""

    def __init__(
        self,
        repository: SQLiteEvolutionActivationV2Repository,
    ) -> None:
        if not isinstance(
            repository,
            SQLiteEvolutionActivationV2Repository,
        ):
            raise TypeError(
                "repository must be SQLiteEvolutionActivationV2Repository"
            )
        self._repository = repository

    def _mutate(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        operation: str,
        callback: Callable[[], Any],
    ) -> Any:
        try:
            return callback()
        except Exception as exc:
            self._record_and_raise(
                workspace_id=workspace_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                operation=operation,
                exc=exc,
            )

    def record_pipeline_failure(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        operation: str,
        exc: Exception,
    ) -> str:
        """Persist an observed pipeline error without changing its caller."""

        reason = (
            exc.reason_code
            if isinstance(exc, EvolutionActivationV2Error)
            else "EVOLUTION_PIPELINE_UNEXPECTED_ERROR"
        )
        return self._repository.record_failure(
            workspace_id=workspace_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            operation=operation,
            reason_code=reason,
            detail=f"{type(exc).__name__}:{exc}",
        )

    def _record_and_raise(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        operation: str,
        exc: Exception,
    ) -> None:
        reason = (
            exc.reason_code
            if isinstance(exc, EvolutionActivationV2Error)
            else "EVOLUTION_ACTIVATION_V2_UNEXPECTED_ERROR"
        )
        try:
            self._repository.record_failure(
                workspace_id=workspace_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                operation=operation,
                reason_code=reason,
                detail=f"{type(exc).__name__}:{exc}",
            )
        except Exception as evidence_exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_FAILURE_EVIDENCE_WRITE_FAILED",
                f"{reason}:{type(evidence_exc).__name__}",
            ) from exc
        raise exc


__all__ = ["EvolutionVersionActivationBase"]
