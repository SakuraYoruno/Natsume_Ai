"""NE-06 激活服务共享的、失败留证的变更内核。"""

from __future__ import annotations

from typing import Any, Callable

from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)


class EvolutionVersionActivationBase:
    """持有唯一 repository，并让失败变更留下可查询证据。"""

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
        # 领域 mixin 只能通过这个入口调用 repository：成功直接返回，失败则先
        # 留下与工作区/主题绑定的证据，防止演进提案悄然消失。
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
        """记录已观察到的流水线错误，不改变调用方原有控制流。"""

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
        # 原始异常应当在证据提交后继续向上传播；若连失败证据也写不进去，改为
        # 明确的 fail-closed 错误，避免上层误把不可审计的变更失败当成可重试成功。
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
