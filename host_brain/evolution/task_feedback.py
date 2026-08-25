"""Proposal-only Task Runtime feedback adapter for NE-06."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any


_logger = logging.getLogger("Natsume.evolution.task_feedback")


class EvolutionFeedbackAcceptance:
    """Observe a canonical acceptance judgement without becoming authority."""

    __slots__ = ("_inner", "_scope_provider", "_service_provider")

    def __init__(
        self,
        *,
        inner: Any,
        service_provider: Callable[[], Any],
        scope_provider: Callable[[], tuple[str, str]],
    ) -> None:
        if not callable(getattr(inner, "judge", None)):
            raise TypeError("inner acceptance must expose judge()")
        if not callable(service_provider):
            raise TypeError("service_provider must be callable")
        if not callable(scope_provider):
            raise TypeError("scope_provider must be callable")
        self._inner = inner
        self._service_provider = service_provider
        self._scope_provider = scope_provider

    def judge(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        judgement = dict(
            self._inner.judge(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                verification_result=verification_result,
                evidence_refs=evidence_refs,
            )
            or {}
        )
        self._stage(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            verification_result=verification_result,
            judgement=judgement,
            evidence_refs=evidence_refs,
        )
        return judgement

    def _stage(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        judgement: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> None:
        workspace_id, _ = self._scope_provider()
        route = _enum_text(getattr(command, "route_type", None))
        domain = str(getattr(work_order, "domain", "") or "").strip()
        subject_id = f"{domain or 'unknown'}:{route or 'unknown'}"
        context = {
            "command_id": str(
                getattr(command, "command_id", "") or ""
            ),
            "work_order_id": str(
                getattr(work_order, "work_order_id", "") or ""
            ),
            "graph_id": str(
                execution_context.get("graph_id") or ""
            ),
            "node_id": str(
                execution_context.get("node_id") or ""
            ),
            "attempt_id": str(
                execution_context.get("attempt_id") or ""
            ),
            "route_type": route,
            "domain": domain,
        }
        refs = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in (
                    *evidence_refs,
                    *tuple(
                        verification_result.get("evidence_refs") or ()
                    ),
                    *tuple(judgement.get("evidence_refs") or ()),
                )
                if str(item or "").strip()
            )
        )
        if not refs:
            _logger.error(
                "Task feedback had no durable evidence: subject=%s",
                subject_id,
            )
            return
        service = self._service_provider()
        signals = [
            (
                "verification",
                str(
                    verification_result.get("status")
                    or "UNKNOWN"
                ),
            ),
            (
                "task_outcome",
                str(judgement.get("decision") or "UNKNOWN"),
            ),
        ]
        if route == "skill":
            signals.append(
                (
                    "tool_reliability",
                    str(judgement.get("decision") or "UNKNOWN"),
                )
            )
        if (
            str(judgement.get("decision") or "").strip().upper()
            == "REJECT"
        ):
            signals.append(("user_rejection", "REJECT"))
        for source_kind, outcome in signals:
            try:
                service.stage_feedback_signal(
                    workspace_id=str(workspace_id),
                    subject_id=subject_id,
                    source_kind=source_kind,
                    outcome=outcome,
                    evidence_refs=refs,
                    context=context,
                    confidence=1.0,
                )
            except Exception:
                # The service has already written durable failure evidence.
                # Keep the accepted Task result authoritative and make the
                # separate evolution degradation observable.
                _logger.exception(
                    "Task feedback proposal failed: "
                    "subject=%s source=%s",
                    subject_id,
                    source_kind,
                )


def decorate_task_acceptance(
    acceptance: Any,
    *,
    task_runtime_container: Any,
) -> Any:
    service_provider = getattr(
        task_runtime_container,
        "evolution_activation_service_provider",
        None,
    )
    scope_provider = getattr(
        task_runtime_container,
        "evolution_scope_provider",
        None,
    )
    if not callable(service_provider) or not callable(scope_provider):
        return acceptance
    return EvolutionFeedbackAcceptance(
        inner=acceptance,
        service_provider=service_provider,
        scope_provider=scope_provider,
    )


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


__all__ = [
    "EvolutionFeedbackAcceptance",
    "decorate_task_acceptance",
]
