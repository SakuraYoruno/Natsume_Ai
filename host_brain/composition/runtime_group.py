"""Lifecycle and Review-continuation group for governed surface runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import inspect
import re
import time
from typing import Any
from urllib.parse import quote

from natsume_contracts.task import HostCommand, HostCommandSurface
from host_brain.observability.command_ports import (
    build_governed_review_continuation_ref,
)
from host_brain.persistence.review_continuation_binding import (
    intent_admission_initial_status,
)
from host_brain.contracts.review_cancellation import (
    cancelled_review_target_status,
)


_SURFACE_RUNTIME = {
    HostCommandSurface.CHAT.value: "host",
    HostCommandSurface.HOST_INPUT.value: "host",
    HostCommandSurface.TOOL.value: "host",
    HostCommandSurface.GATEWAY.value: "host",
    HostCommandSurface.INTERNAL.value: "internal_agent",
    HostCommandSurface.SCHEDULER.value: "scheduler",
    # Operator commands are selected by their immutable target below.  The
    # default remains Scheduler for scheduler.task.trigger.
    HostCommandSurface.OPERATOR.value: "scheduler",
}
_OPERATOR_RUNTIME_TARGETS = {
    "operator_sys_mode_switch": "operator",
    "operator_edge_command_push": "operator",
    "operator_task_terminal_reconcile": "operator",
    "operator_task_operation_reconcile": "operator",
    "operator_task_effect_reconcile": "operator",
    "operator_task_control": "operator",
    "operator_state_control": "operator",
}
_CLAIM_COMPLETION_SURFACES = frozenset(
    {
        HostCommandSurface.INTERNAL.value,
        HostCommandSurface.SCHEDULER.value,
        HostCommandSurface.TOOL.value,
    }
)
_WAITING_REVIEW_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
)
_DENIED_REVIEW_STATUSES = frozenset({"REJECTED", "EXPIRED", "CANCELLED"})
_TASK_CANCEL_INTENT_REF = re.compile(r"^taskcancel-intent://sha256/[0-9a-f]{64}$")


def _runtime_name_for_command(command: HostCommand) -> str | None:
    if command.surface is HostCommandSurface.OPERATOR:
        target = str(command.target_id or "").strip()
        return _OPERATOR_RUNTIME_TARGETS.get(target, "scheduler")
    return _SURFACE_RUNTIME.get(command.surface.value)


class GraphCancellationOperatorError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        side_effects_may_have_run: bool,
    ) -> None:
        self.reason_code = str(reason_code)
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(self.reason_code)


class _OperatorReviewRecoveryPending(RuntimeError):
    reason_code = "OPERATOR_REVIEW_RECOVERY_PENDING"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


class _OperatorReviewCancellationRecoveryPending(RuntimeError):
    reason_code = "OPERATOR_REVIEW_CANCELLATION_RECOVERY_PENDING"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


class GovernedRuntimeGroupCloseError(RuntimeError):
    """Stable aggregate emitted after every owned runtime got a close attempt."""

    reason_code = "GOVERNED_RUNTIME_GROUP_CLOSE_FAILED"

    def __init__(
        self,
        failures: tuple[tuple[str, BaseException], ...],
    ) -> None:
        self.failures = failures
        components = ",".join(component for component, _exc in failures)
        super().__init__(f"{self.reason_code}:{components}")


class GovernedRuntimeGroup:
    def __init__(
        self,
        runtimes: Mapping[str, Any],
        *,
        review_store: Any | None = None,
        review_decision_store: Any | None = None,
        command_repository: Any | None = None,
        claim_result_store: Any | None = None,
        operator_outcome_finalizer: Any | None = None,
        review_continuation_worker_factory: Callable[..., Any] | None = None,
        review_continuation_worker_id: str | None = None,
        graph_cancellation_operator_enabled: bool = False,
        graph_cancellation_state_reader: Any | None = None,
        acceptance_effect_runtime: Any | None = None,
        operation_reacquisition_owner_id: str | None = None,
    ) -> None:
        normalized = {
            str(name).strip(): runtime
            for name, runtime in dict(runtimes or {}).items()
            if str(name).strip()
        }
        if not normalized:
            raise ValueError("at least one governed runtime is required")
        for name, runtime in normalized.items():
            for method_name in ("activate", "close_admission", "drain", "aclose"):
                if not callable(getattr(runtime, method_name, None)):
                    raise TypeError(f"runtime {name} lacks {method_name}")
        self._runtimes = normalized
        self._active_names: list[str] = []
        self._command_repository = command_repository
        self._claim_result_store = claim_result_store
        self._review_decision_store = review_decision_store
        self._operator_outcome_finalizer = operator_outcome_finalizer
        if operator_outcome_finalizer is not None and not callable(
            getattr(operator_outcome_finalizer, "finalize_review_outcome", None)
        ):
            raise TypeError(
                "operator_outcome_finalizer must expose finalize_review_outcome"
            )
        self._review_worker: Any | None = None
        self._review_worker_started = False
        self._continuation_open = False
        self._graph_cancellation_operator_enabled = bool(
            graph_cancellation_operator_enabled
        )
        self._graph_cancellation_state_reader = graph_cancellation_state_reader
        self._acceptance_effect_runtime = acceptance_effect_runtime
        self._acceptance_effect_runtime_active = False
        self._operation_reacquisition_owner_id = str(
            operation_reacquisition_owner_id or ""
        ).strip() or None
        self._operation_reacquisition_lock = asyncio.Lock()
        if acceptance_effect_runtime is not None:
            for method_name in ("activate", "drain", "aclose"):
                if not callable(getattr(acceptance_effect_runtime, method_name, None)):
                    raise TypeError(
                        f"Acceptance Effect runtime lacks {method_name}"
                    )
        if self._graph_cancellation_operator_enabled and not callable(
            getattr(
                graph_cancellation_state_reader,
                "get_graph_cancellation_for_graph",
                None,
            )
        ):
            raise TypeError(
                "enabled Graph cancellation Operator requires a durable state reader"
            )

        review_dependencies = (
            review_store,
            review_decision_store,
            command_repository,
            review_continuation_worker_factory,
            review_continuation_worker_id,
        )
        if any(value is not None for value in review_dependencies):
            if (
                review_store is None
                or review_decision_store is None
                or command_repository is None
            ):
                raise TypeError(
                    "Review continuation requires both Review stores and command_repository"
                )
            if not callable(getattr(command_repository, "get_by_graph", None)):
                raise TypeError("command_repository must expose get_by_graph")
            worker_id = str(review_continuation_worker_id or "").strip()
            if not worker_id:
                raise ValueError("review_continuation_worker_id is required")
            factory = review_continuation_worker_factory
            if factory is None:
                from host_brain.task_runtime.review_continuation_worker import (  # noqa: PLC0415
                    ReviewContinuationWorker,
                )

                factory = ReviewContinuationWorker
            self._review_worker = factory(
                store=review_store,
                runtime=self,
                worker_id=worker_id,
                review_expirer=review_decision_store,
            )
            for method_name in ("start", "wake", "stop", "join"):
                if not callable(getattr(self._review_worker, method_name, None)):
                    raise TypeError(
                        f"Review continuation worker lacks {method_name}"
                    )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._runtimes)

    @property
    def review_continuation_worker(self) -> Any | None:
        return self._review_worker

    def get(self, name: str) -> Any:
        return self._runtimes[str(name)]

    async def activate(self) -> None:
        if self._active_names or self._acceptance_effect_runtime_active:
            return
        try:
            for name, runtime in self._runtimes.items():
                await _await_call(runtime.activate())
                self._active_names.append(name)
            self._continuation_open = True
            # A recovery-time Acceptance Effect may need the app-local
            # Runtime Group (for example, to persist one graph cancellation).
            # Activate every target runtime before replaying committed effects
            # so startup can reconcile rather than deadlock on an inactive
            # provider.  Public ASGI admission remains closed until lifespan
            # activation completes.
            if self._acceptance_effect_runtime is not None:
                await _await_call(self._acceptance_effect_runtime.activate())
                self._acceptance_effect_runtime_active = True
            if self._review_worker is not None:
                await _await_call(self._review_worker.start())
                self._review_worker_started = True
        except BaseException:
            await self.aclose()
            raise

    async def close_admission(self) -> None:
        # Review continuation is an internal producer.  Quiesce it while every
        # surface is still active, then close public admission and drain.
        await self._stop_review_worker()
        for name in tuple(self._active_names):
            await _await_call(self._runtimes[name].close_admission())

    async def drain(self) -> None:
        for name in tuple(self._active_names):
            await _await_call(self._runtimes[name].drain())
        if self._acceptance_effect_runtime_active:
            await _await_call(self._acceptance_effect_runtime.drain())

    async def aclose(self) -> None:
        failures: list[tuple[str, BaseException]] = []
        try:
            await self._stop_review_worker()
        except BaseException as exc:
            # A failed Review-worker stop is not permission to leave the
            # surface runtimes or the shared Acceptance Effect owner live.
            # Keep the worker-started flag for a later retry, but attempt all
            # remaining children before reporting one aggregate failure.
            failures.append(("review_continuation_worker", exc))
        for name in reversed(tuple(self._active_names)):
            try:
                await _await_call(self._runtimes[name].aclose())
            except BaseException as exc:
                failures.append((f"surface:{name}", exc))
            else:
                self._active_names.remove(name)
        if self._acceptance_effect_runtime_active:
            try:
                await _await_call(self._acceptance_effect_runtime.aclose())
            except BaseException as exc:
                failures.append(("acceptance_effect_runtime", exc))
            else:
                self._acceptance_effect_runtime_active = False
        if failures:
            raise GovernedRuntimeGroupCloseError(tuple(failures)) from failures[0][1]

    def wake_review_continuation(self) -> None:
        worker = self._review_worker
        if worker is not None:
            worker.wake()

    @property
    def operation_reacquisition_owner_id(self) -> str | None:
        return self._operation_reacquisition_owner_id

    async def continue_operation_reacquisition(
        self,
        reconciliation: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Re-enter the exact admission selected by one signed REACQUIRE."""

        owner_id = self._operation_reacquisition_owner_id
        if owner_id is None or not self._continuation_open or not self._active_names:
            raise RuntimeError("OPERATION_REACQUISITION_RUNTIME_INACTIVE")
        normalized = _mapping(
            reconciliation,
            field_name="Operation reacquisition result",
        )
        operation = _mapping(
            normalized.get("operation"),
            field_name="reacquired Operation",
        )
        graph_id = str(normalized.get("graph_id") or "").strip()
        graph_revision = normalized.get("graph_revision")
        if (
            not graph_id
            or type(graph_revision) is not int
            or graph_revision < 1
            or str(operation.get("owner_id") or "").strip() != owner_id
            or str(operation.get("reconciliation_action") or "").upper()
            != "REACQUIRE"
            or str(operation.get("reconciliation_outcome") or "").upper()
            != "NO_SIDE_EFFECT"
        ):
            raise RuntimeError("OPERATION_REACQUISITION_BINDING_DRIFT")
        return await self._continue_reconciled_operation_target(
            operation=operation,
            graph_id=graph_id,
            graph_revision=graph_revision,
            detail_prefix="OPERATION_REACQUISITION",
        )

    async def continue_operation_reconciliation(
        self,
        reconciliation: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Re-enter one graph from a signed, durable worker checkpoint."""

        if not self._continuation_open or not self._active_names:
            raise RuntimeError("OPERATION_RECONCILIATION_RUNTIME_INACTIVE")
        normalized = _mapping(
            reconciliation,
            field_name="Operation checkpoint reconciliation result",
        )
        operation = _mapping(
            normalized.get("operation"),
            field_name="committed checkpoint Operation",
        )
        checkpoint = _mapping(
            normalized.get("checkpoint"),
            field_name="committed worker checkpoint",
        )
        graph_id = str(normalized.get("graph_id") or "").strip()
        graph_revision = normalized.get("graph_revision")
        receipt_ref = str(operation.get("receipt_ref") or "").strip()
        if (
            not graph_id
            or type(graph_revision) is not int
            or graph_revision < 1
            or str(operation.get("status") or "").upper() != "COMMITTED"
            or str(operation.get("reconciliation_action") or "").upper()
            != "COMMIT"
            or str(operation.get("reconciliation_outcome") or "").upper()
            != "SIDE_EFFECT_CONFIRMED"
            or not receipt_ref.startswith("worker-projection://")
            or str(checkpoint.get("projection_ref") or "").strip()
            != receipt_ref
        ):
            raise RuntimeError("OPERATION_RECONCILIATION_BINDING_DRIFT")
        return await self._continue_reconciled_operation_target(
            operation=operation,
            graph_id=graph_id,
            graph_revision=graph_revision,
            detail_prefix="OPERATION_RECONCILIATION",
        )

    async def _continue_reconciled_operation_target(
        self,
        *,
        operation: Mapping[str, Any],
        graph_id: str,
        graph_revision: int,
        detail_prefix: str,
    ) -> Mapping[str, Any]:
        async with self._operation_reacquisition_lock:
            getter = getattr(self._command_repository, "get_by_graph", None)
            if not callable(getter):
                raise RuntimeError(
                    f"{detail_prefix}_COMMAND_REPOSITORY_UNAVAILABLE"
                )
            record_raw = await _await_call(
                getter(graph_id=graph_id, graph_revision=graph_revision)
            )
            record = _mapping(
                record_raw,
                field_name="reacquired durable HostCommand admission",
            )
            command = record.get("command")
            if (
                not isinstance(command, HostCommand)
                or command.graph_id != graph_id
                or command.graph_revision != graph_revision
            ):
                raise RuntimeError(
                    f"{detail_prefix}_COMMAND_BINDING_DRIFT"
                )
            runtime_name = _runtime_name_for_command(command)
            runtime = self._runtimes.get(str(runtime_name or ""))
            continuation = getattr(
                runtime,
                "continue_operation_reacquisition",
                None,
            )
            if runtime is None or not callable(continuation):
                raise RuntimeError(
                    f"{detail_prefix}_SURFACE_UNAVAILABLE:"
                    f"{command.surface.value}"
                )
            result = _mapping(
                await _await_call(continuation(record, operation)),
                field_name="Operation reconciliation continuation result",
            )
            receipt = _mapping(
                result.get("execution_receipt"),
                field_name="Operation reconciliation execution receipt",
            )
            if (
                str(receipt.get("graph_id") or "").strip() != graph_id
                or receipt.get("graph_revision") != graph_revision
            ):
                raise RuntimeError(
                    f"{detail_prefix}_RECEIPT_BINDING_DRIFT"
                )
            return result

    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Re-enter the exact admitted command selected by a fenced claim."""

        if not self._continuation_open or not self._active_names:
            raise RuntimeError("REVIEW_CONTINUATION_RUNTIME_INACTIVE")
        normalized_claim = _mapping(claim, field_name="continuation claim")
        graph_id, graph_revision = _claim_graph_identity(normalized_claim)
        getter = getattr(self._command_repository, "get_by_graph", None)
        if not callable(getter):
            raise RuntimeError("REVIEW_CONTINUATION_COMMAND_REPOSITORY_UNAVAILABLE")
        record_raw = getter(
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
        record_raw = await _await_call(record_raw)
        record = _mapping(record_raw, field_name="durable HostCommand admission")
        command = record.get("command")
        if (
            not isinstance(command, HostCommand)
            or command.graph_id != graph_id
            or command.graph_revision != graph_revision
        ):
            raise RuntimeError("REVIEW_CONTINUATION_COMMAND_BINDING_DRIFT")
        claimed_command_id = str(normalized_claim.get("command_id") or "").strip()
        if claimed_command_id and claimed_command_id != command.command_id:
            raise RuntimeError("REVIEW_CONTINUATION_COMMAND_BINDING_DRIFT")
        surface = command.surface.value
        runtime_name = _runtime_name_for_command(command)
        runtime = self._runtimes.get(str(runtime_name or ""))
        continuation = getattr(runtime, "continue_review_admission", None)
        if runtime is None or not callable(continuation):
            raise RuntimeError(
                f"REVIEW_CONTINUATION_SURFACE_UNAVAILABLE:{surface or 'unknown'}"
            )
        result_raw = continuation(record, normalized_claim)
        result_raw = await _await_call(result_raw)
        result = _mapping(result_raw, field_name="Review continuation result")
        await self._complete_secure_ingress_claim(
            command=command,
            continuation_result=result,
            claim=normalized_claim,
        )
        return result

    async def request_graph_cancellation(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        reason_code: str,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Route one authenticated Operator cancellation to its owning surface."""

        if not self._graph_cancellation_operator_enabled:
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_OPERATOR_DISABLED",
                side_effects_may_have_run=False,
            )
        if not self._continuation_open or not self._active_names:
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_RUNTIME_INACTIVE",
                side_effects_may_have_run=False,
            )
        graph_ref = str(graph_id or "").strip()
        if (
            not graph_ref
            or type(graph_revision) is not int
            or graph_revision < 1
            or not isinstance(authority, Mapping)
        ):
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_COMMAND_INVALID",
                side_effects_may_have_run=False,
            )
        getter = getattr(self._command_repository, "get_by_graph", None)
        if not callable(getter):
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_COMMAND_REPOSITORY_UNAVAILABLE",
                side_effects_may_have_run=False,
            )
        try:
            record_raw = getter(
                graph_id=graph_ref,
                graph_revision=graph_revision,
            )
            record_raw = await _await_call(record_raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # This port is read-only and is invoked before a surface runtime
            # receives the command.  A lookup failure therefore cannot have
            # installed a cancellation intent and must not be reported as an
            # execution-uncertain mutation.
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_COMMAND_LOOKUP_UNAVAILABLE",
                side_effects_may_have_run=False,
            ) from exc
        try:
            record = _mapping(
                record_raw,
                field_name="durable cancellation admission",
            )
        except RuntimeError as exc:
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_COMMAND_NOT_FOUND",
                side_effects_may_have_run=False,
            ) from exc
        command = record.get("command")
        if (
            not isinstance(command, HostCommand)
            or command.graph_id != graph_ref
            or command.graph_revision != graph_revision
        ):
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_COMMAND_BINDING_DRIFT",
                side_effects_may_have_run=False,
            )
        runtime_name = _runtime_name_for_command(command)
        runtime = self._runtimes.get(str(runtime_name or ""))
        cancellation = getattr(runtime, "request_graph_cancellation", None)
        if runtime is None or not callable(cancellation):
            raise GraphCancellationOperatorError(
                f"GRAPH_CANCELLATION_SURFACE_UNAVAILABLE:{command.surface.value}",
                side_effects_may_have_run=False,
            )
        normalized_reason = str(reason_code or "").strip().upper()
        try:
            result_raw = cancellation(
                record,
                authority=dict(authority),
                source="OPERATOR",
                reason_code=normalized_reason,
            )
            result_raw = await _await_call(result_raw)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            may_have_run = await self._cancellation_intent_matches(
                graph_id=graph_ref,
                graph_revision=graph_revision,
                reason_code=normalized_reason,
                authority=authority,
            )
            raise GraphCancellationOperatorError(
                "GRAPH_CANCELLATION_EXECUTION_UNCERTAIN"
                if may_have_run
                else "GRAPH_CANCELLATION_REJECTED",
                side_effects_may_have_run=may_have_run,
            ) from exc
        return _mapping(result_raw, field_name="graph cancellation result")

    async def _cancellation_intent_matches(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        reason_code: str,
        authority: Mapping[str, Any],
    ) -> bool:
        getter = getattr(
            self._graph_cancellation_state_reader,
            "get_graph_cancellation_for_graph",
            None,
        )
        if not callable(getter):
            return True
        try:
            record_raw = getter(graph_id, graph_revision)
            record_raw = await _await_call(record_raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            return True
        if record_raw is None:
            return False
        if not isinstance(record_raw, Mapping):
            return True
        record = dict(record_raw)
        payload_raw = record.get("payload")
        payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else record
        try:
            return all(
                (
                    str(payload.get("request_id") or "").strip()
                    == str(authority.get("request_id") or "").strip(),
                    str(payload.get("request_identity") or "").strip()
                    == str(authority.get("audit_ref") or "").strip(),
                    str(payload.get("source") or "").strip().upper()
                    == "OPERATOR",
                    str(payload.get("reason_code") or "").strip().upper()
                    == reason_code,
                    str(payload.get("graph_id") or "").strip() == graph_id,
                    int(payload.get("graph_revision") or 0) == graph_revision,
                )
            )
        except (TypeError, ValueError):
            return True

    async def _stop_review_worker(self) -> None:
        self._continuation_open = False
        worker = self._review_worker
        if worker is None or not self._review_worker_started:
            return
        await _await_call(worker.stop())
        await _await_call(worker.join())
        self._review_worker_started = False

    async def _complete_secure_ingress_claim(
        self,
        *,
        command: HostCommand,
        continuation_result: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> None:
        surface = command.surface.value
        claim_ref = str(command.ingress_claim_ref or "").strip()
        if surface == HostCommandSurface.OPERATOR.value:
            if not claim_ref:
                raise RuntimeError("OPERATOR_REVIEW_INGRESS_CLAIM_REF_REQUIRED")
            await self._finalize_operator_review_outcome(
                claim_ref=claim_ref,
                continuation_result=continuation_result,
                claim=claim,
                command=command,
            )
            return
        if surface not in _CLAIM_COMPLETION_SURFACES or not claim_ref:
            return
        result_ref = _continuation_result_ref(
            continuation_result,
            claim=claim,
            command=command,
        )
        if result_ref is None:
            return
        result_ref = await self._exact_internal_continuation_result_ref(
            command=command,
            continuation_result=continuation_result,
            fallback_result_ref=result_ref,
        )
        if result_ref.startswith("review-decision://"):
            complete_review = getattr(
                self._claim_result_store,
                "complete_review_decision_claim",
                None,
            )
            if callable(complete_review):
                receipt_raw = continuation_result.get("execution_receipt")
                receipt = (
                    dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
                )
                review_raw = claim.get("review")
                review_record = (
                    dict(review_raw) if isinstance(review_raw, Mapping) else {}
                )
                review_item_raw = review_record.get("review")
                review_item = (
                    dict(review_item_raw)
                    if isinstance(review_item_raw, Mapping)
                    else review_record
                )
                completed = complete_review(
                    claim_ref=claim_ref,
                    result_ref=result_ref,
                    prior_result_ref=str(
                        receipt.get("receipt_ref")
                        or continuation_result.get("receipt_ref")
                        or ""
                    ).strip()
                    or None,
                    review_id=str(
                        claim.get("review_id")
                        or review_item.get("review_id")
                        or ""
                    ).strip(),
                    review_status=str(
                        claim.get("review_status")
                        or review_item.get("status")
                        or ""
                    ).strip(),
                    review_status_version=int(claim.get("review_status_version")),
                    graph_id=command.graph_id,
                    graph_revision=command.graph_revision,
                    node_id=str(
                        claim.get("node_id")
                        or review_record.get("task_node_id")
                        or ""
                    ).strip(),
                    at_ms=int(time.time() * 1_000),
                )
                await _await_call(completed)
                return
        complete = getattr(self._claim_result_store, "complete_claim", None)
        if not callable(complete):
            raise RuntimeError("REVIEW_CONTINUATION_CLAIM_RESULT_STORE_UNAVAILABLE")
        completed = complete(
            claim_ref=claim_ref,
            result_ref=result_ref,
            at_ms=int(time.time() * 1_000),
        )
        await _await_call(completed)

    async def _exact_internal_continuation_result_ref(
        self,
        *,
        command: HostCommand,
        continuation_result: Mapping[str, Any],
        fallback_result_ref: str,
    ) -> str:
        """Use the canonical Internal result only after accepted effects commit.

        A post-execution Review resumes a sealed worker checkpoint, whose
        ``receipt_ref`` remains a worker-projection reference.  It is evidence
        for the worker, not the terminal Secure Ingress result.  The Internal
        runtime must therefore reopen the accepted result ledger before this
        group is permitted to close the ingress claim.
        """

        if command.surface is not HostCommandSurface.INTERNAL:
            return fallback_result_ref
        receipt_raw = continuation_result.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        if (
            str(receipt.get("status") or "").strip().upper() != "ACCEPTED"
            or receipt.get("accepted") is not True
        ):
            return fallback_result_ref
        effect_status = str(
            continuation_result.get("internal_agent_effect_status") or ""
        ).strip().upper()
        if effect_status != "COMMITTED":
            raise RuntimeError(
                "REVIEW_CONTINUATION_INTERNAL_EFFECT_NOT_COMMITTED"
            )
        expected_ref = str(
            continuation_result.get("internal_agent_result_receipt_ref") or ""
        ).strip()
        runtime = self._runtimes.get("internal_agent")
        verifier = getattr(runtime, "exact_accepted_result_receipt", None)
        if not expected_ref or not callable(verifier):
            raise RuntimeError(
                "REVIEW_CONTINUATION_INTERNAL_RESULT_ATTRIBUTION_MISSING"
            )
        verified = await _await_call(
            verifier(
                command_id=command.command_id,
                expected_receipt_ref=expected_ref,
            )
        )
        if str(verified or "").strip() != expected_ref:
            raise RuntimeError(
                "REVIEW_CONTINUATION_INTERNAL_RESULT_ATTRIBUTION_MISSING"
            )
        return expected_ref

    async def _finalize_operator_review_outcome(
        self,
        *,
        claim_ref: str,
        continuation_result: Mapping[str, Any],
        claim: Mapping[str, Any],
        command: HostCommand,
    ) -> None:
        finalizer = self._operator_outcome_finalizer
        finalize = getattr(finalizer, "finalize_review_outcome", None)
        if not callable(finalize):
            raise RuntimeError("OPERATOR_REVIEW_OUTCOME_FINALIZER_UNAVAILABLE")
        review_id = str(claim.get("review_id") or "").strip()
        get_review = getattr(self._review_decision_store, "get_review", None)
        if not review_id or not callable(get_review):
            raise RuntimeError("OPERATOR_REVIEW_AUTHORITY_UNAVAILABLE")
        durable_raw = get_review(review_id)
        durable_record = _mapping(
            await _await_call(durable_raw),
            field_name="durable Review authority",
        )
        review_item = _mapping(
            durable_record.get("review"),
            field_name="durable ReviewItem",
        )
        review_status = str(review_item.get("status") or "").strip().upper()
        claim_status = str(claim.get("review_status") or "").strip().upper()
        try:
            review_status_version = int(durable_record.get("status_version"))
            claim_status_version = int(claim.get("review_status_version"))
            subject_version = int(review_item.get("subject_version"))
            claim_subject_version = int(claim.get("subject_version"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("OPERATOR_REVIEW_AUTHORITY_BINDING_DRIFT") from exc
        node_id = str(claim.get("node_id") or "").strip()
        if any(
            (
                str(review_item.get("review_id") or "").strip() != review_id,
                str(durable_record.get("task_graph_id") or "").strip()
                != command.graph_id,
                str(durable_record.get("task_node_id") or "").strip()
                != node_id,
                review_status != claim_status,
                review_status_version != claim_status_version,
                subject_version != claim_subject_version,
                str(review_item.get("resume_phase") or "").strip().upper()
                != str(claim.get("resume_phase") or "").strip().upper(),
                _subject_graph_revision(
                    review_item.get("subject_ref"),
                    command.graph_id,
                )
                != command.graph_revision,
            )
        ):
            raise RuntimeError("OPERATOR_REVIEW_AUTHORITY_BINDING_DRIFT")
        continuation_ref = build_governed_review_continuation_ref(
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
            review=review_item,
        )
        terminal_result_ref = (
            await self._operator_cancelled_review_result_ref(
                command=command,
                node_id=node_id,
                subject_version=subject_version,
                resume_phase=str(review_item.get("resume_phase") or ""),
                subject_ref=str(review_item.get("subject_ref") or ""),
            )
            if review_status == "CANCELLED"
            else _continuation_result_ref(
                continuation_result,
                claim=claim,
                command=command,
            )
        )
        # Returning normally authorizes ReviewContinuationWorker to complete
        # its one-shot claim.  A non-terminal Task receipt must therefore be
        # raised as a stable retry condition, not silently returned.
        if terminal_result_ref is None:
            raise _OperatorReviewRecoveryPending()
        receipt_raw = continuation_result.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        audit_raw = continuation_result.get("audit_refs")
        audit = dict(audit_raw) if isinstance(audit_raw, Mapping) else {}
        effect_raw = audit.get("acceptance_effects")
        effects = dict(effect_raw) if isinstance(effect_raw, Mapping) else {}
        if review_status in {"REJECTED", "EXPIRED", "CANCELLED"}:
            state = "REJECTED"
            reason_code = f"OBS_COMMAND_REVIEW_{review_status}"
            side_effects_executed = False
        else:
            state, reason_code, side_effects_executed = (
                _operator_effect_terminal_disposition(
                    receipt=receipt,
                    effects=effects,
                    historical_side_effects_executed=(
                        continuation_result.get(
                            "historical_side_effects_executed"
                        )
                    ),
                )
            )
        completed = finalize(
            claim_ref=claim_ref,
            state=state,
            reason_code=reason_code,
            side_effects_executed=side_effects_executed,
            terminal_result_ref=terminal_result_ref,
            continuation_ref=continuation_ref,
        )
        await _await_call(completed)

    async def _operator_cancelled_review_result_ref(
        self,
        *,
        command: HostCommand,
        node_id: str,
        subject_version: int,
        resume_phase: str,
        subject_ref: str,
    ) -> str:
        getter = getattr(
            self._graph_cancellation_state_reader,
            "get_graph_cancellation_for_graph",
            None,
        )
        if not callable(getter):
            raise _OperatorReviewCancellationRecoveryPending()
        try:
            raw = getter(command.graph_id, command.graph_revision)
            record = _mapping(
                await _await_call(raw),
                field_name="durable Graph cancellation",
            )
            payload = _mapping(
                record.get("payload"),
                field_name="durable Graph cancellation payload",
            )
            before_states = _mapping(
                payload.get("node_states_before"),
                field_name="Graph cancellation node_states_before",
            )
            after_states = _mapping(
                payload.get("node_states_after"),
                field_name="Graph cancellation node_states_after",
            )
            before = _mapping(
                before_states.get(node_id),
                field_name="cancelled Review node state before",
            )
            after = _mapping(
                after_states.get(node_id),
                field_name="cancelled Review node state after",
            )
            intent_ref = str(record.get("intent_ref") or "").strip().lower()
            expected_waiting = (
                intent_admission_initial_status(subject_ref)
                or (
                    "WAITING_APPROVAL"
                    if str(resume_phase or "").strip().upper()
                    == "PRE_EXECUTION"
                    else "WAITING_ACCEPTANCE_REVIEW"
                )
            )
            expected_after_status = cancelled_review_target_status(
                expected_waiting
            )
            if any(
                (
                    _TASK_CANCEL_INTENT_REF.fullmatch(intent_ref) is None,
                    str(record.get("status") or "").strip().upper()
                    not in {"REQUESTED", "FINALIZED"},
                    str(record.get("graph_id") or "").strip()
                    != command.graph_id,
                    int(record.get("graph_revision") or 0)
                    != command.graph_revision,
                    str(payload.get("graph_id") or "").strip()
                    != command.graph_id,
                    int(payload.get("graph_revision") or 0)
                    != command.graph_revision,
                    str(before.get("status") or "").strip().upper()
                    != expected_waiting,
                    int(before.get("status_version") or -1)
                    != subject_version,
                    str(after.get("status") or "").strip().upper()
                    != expected_after_status,
                    int(after.get("status_version") or -1)
                    != subject_version + 1,
                )
            ):
                raise _OperatorReviewCancellationRecoveryPending()
            return intent_ref
        except asyncio.CancelledError:
            raise
        except _OperatorReviewCancellationRecoveryPending:
            raise
        except Exception as exc:
            raise _OperatorReviewCancellationRecoveryPending() from exc
async def _await_call(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{field_name} must be a mapping")
    return dict(value)


def _operator_effect_terminal_disposition(
    *,
    receipt: Mapping[str, Any],
    effects: Mapping[str, Any],
    historical_side_effects_executed: Any,
) -> tuple[str, str, bool]:
    receipt_status = str(receipt.get("status") or "").strip().upper()
    if receipt_status != "ACCEPTED" or receipt.get("accepted") is not True:
        raise _OperatorReviewRecoveryPending()

    count = _effect_summary_count(effects, "count")
    committed = _effect_summary_count(effects, "committed")
    failed = _effect_summary_count(effects, "failed")
    pending = _effect_summary_count(effects, "pending")
    recovery_pending = _effect_summary_count(effects, "recovery_pending")
    dispatch_status = str(effects.get("dispatch_status") or "").strip().upper()

    # recovery_pending is a subset of pending.  The remaining three counters
    # must exactly account for the durable queue projection before an outer
    # command may become terminal.
    reconciled = count > 0 and count == committed + failed + pending
    if (
        not reconciled
        or pending > 0
        or recovery_pending > 0
        or dispatch_status == "RECOVERY_PENDING"
    ):
        raise _OperatorReviewRecoveryPending()
    if dispatch_status == "COMMITTED" and committed == count and failed == 0:
        if type(historical_side_effects_executed) is not bool:
            raise RuntimeError("OPERATOR_HISTORICAL_EFFECT_ATTRIBUTION_INVALID")
        return (
            "SUCCEEDED",
            "OBS_COMMAND_COMPLETED",
            historical_side_effects_executed,
        )
    if dispatch_status == "FAILED" and failed > 0 and committed + failed == count:
        if type(historical_side_effects_executed) is not bool:
            raise RuntimeError("OPERATOR_HISTORICAL_EFFECT_ATTRIBUTION_INVALID")
        return (
            "REJECTED",
            "OBS_COMMAND_ACCEPTANCE_EFFECT_FAILED",
            historical_side_effects_executed,
        )
    # NO_EFFECTS, missing/unknown status, and contradictory terminal counters
    # are not proof of rejection.  Keep the outer command open for durable
    # reconciliation instead of creating a rejected-then-later-effect split.
    raise _OperatorReviewRecoveryPending()


def _effect_summary_count(effects: Mapping[str, Any], field_name: str) -> int:
    raw = effects.get(field_name)
    if isinstance(raw, bool):
        raise RuntimeError("OPERATOR_ACCEPTANCE_EFFECT_SUMMARY_INVALID")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OPERATOR_ACCEPTANCE_EFFECT_SUMMARY_INVALID") from exc
    if value < 0:
        raise RuntimeError("OPERATOR_ACCEPTANCE_EFFECT_SUMMARY_INVALID")
    return value


def _claim_graph_identity(claim: Mapping[str, Any]) -> tuple[str, int]:
    review_record = claim.get("review")
    outer_review = dict(review_record) if isinstance(review_record, Mapping) else {}
    review_item_raw = outer_review.get("review")
    review_item = (
        dict(review_item_raw)
        if isinstance(review_item_raw, Mapping)
        else outer_review
    )
    graph_id = str(
        claim.get("graph_id")
        or claim.get("task_graph_id")
        or outer_review.get("task_graph_id")
        or ""
    ).strip()
    raw_revision = claim.get("graph_revision")
    if raw_revision is None:
        raw_revision = _subject_graph_revision(review_item.get("subject_ref"), graph_id)
    if isinstance(raw_revision, bool):
        raise RuntimeError("REVIEW_CONTINUATION_GRAPH_BINDING_INVALID")
    try:
        graph_revision = int(raw_revision)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("REVIEW_CONTINUATION_GRAPH_BINDING_INVALID") from exc
    if not graph_id or graph_revision < 1:
        raise RuntimeError("REVIEW_CONTINUATION_GRAPH_BINDING_INVALID")
    return graph_id, graph_revision


def _subject_graph_revision(subject_ref: Any, expected_graph_id: str) -> int | None:
    from urllib.parse import unquote  # local import keeps this helper isolated

    subject = str(subject_ref or "").strip()
    prefix = "task-node://"
    if not subject.startswith(prefix):
        return None
    path = subject[len(prefix) :].split("?", 1)[0]
    parts = path.split("/")
    if len(parts) < 3 or unquote(parts[0]) != expected_graph_id:
        return None
    try:
        revision = int(parts[1])
    except (TypeError, ValueError):
        return None
    return revision if revision >= 1 else None


def _continuation_result_ref(
    result: Mapping[str, Any],
    *,
    claim: Mapping[str, Any],
    command: HostCommand,
) -> str | None:
    receipt_raw = result.get("execution_receipt")
    receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
    status = str(receipt.get("status") or "").strip().upper()
    if status in _WAITING_REVIEW_STATUSES or status == "RECOVERY_REQUIRED":
        return None
    review_record = claim.get("review")
    outer_review = dict(review_record) if isinstance(review_record, Mapping) else {}
    review_item_raw = outer_review.get("review")
    review_item = (
        dict(review_item_raw)
        if isinstance(review_item_raw, Mapping)
        else outer_review
    )
    review_status = str(
        claim.get("review_status") or review_item.get("status") or ""
    ).strip().upper()
    if review_status in _DENIED_REVIEW_STATUSES:
        review_id = str(
            claim.get("review_id") or review_item.get("review_id") or ""
        ).strip()
        try:
            review_status_version = int(claim.get("review_status_version"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("REVIEW_CONTINUATION_REVIEW_BINDING_INVALID") from exc
        if (
            not review_id
            or review_status not in {"REJECTED", "EXPIRED"}
            or review_status_version < 1
        ):
            raise RuntimeError("REVIEW_CONTINUATION_REVIEW_BINDING_INVALID")
        # A POST review may carry the pre-acceptance worker receipt.  Rejection
        # must bind ingress replay to the durable Review decision instead of
        # falsely completing with that earlier, unaccepted receipt.
        return (
            f"review-decision://{quote(review_id, safe='')}/"
            f"{review_status.lower()}/{review_status_version}"
        )
    if status == "REVIEW_DENIED":
        # A consumed Review may resume into a later rejected Review in the
        # same DAG.  The current claim cannot manufacture authority for that
        # other decision; leave ingress open for the rejected Review's own
        # continuation.
        return None
    receipt_ref = str(
        receipt.get("receipt_ref") or result.get("receipt_ref") or ""
    ).strip()
    return receipt_ref or None


__all__ = [
    "GovernedRuntimeGroup",
    "GovernedRuntimeGroupCloseError",
    "GraphCancellationOperatorError",
]
