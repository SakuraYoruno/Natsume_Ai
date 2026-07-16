"""Lifecycle and Review-continuation group for governed surface runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import inspect
import time
from typing import Any
from urllib.parse import quote

from natsume_contracts.task import HostCommand, HostCommandSurface


_SURFACE_RUNTIME = {
    HostCommandSurface.CHAT.value: "host",
    HostCommandSurface.HOST_INPUT.value: "host",
    HostCommandSurface.GATEWAY.value: "host",
    HostCommandSurface.INTERNAL.value: "internal_agent",
    HostCommandSurface.SCHEDULER.value: "scheduler",
}
_CLAIM_COMPLETION_SURFACES = frozenset(
    {HostCommandSurface.INTERNAL.value, HostCommandSurface.SCHEDULER.value}
)
_WAITING_REVIEW_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
)
_DENIED_REVIEW_STATUSES = frozenset({"REJECTED", "EXPIRED", "CANCELLED"})


class GovernedRuntimeGroup:
    def __init__(
        self,
        runtimes: Mapping[str, Any],
        *,
        review_store: Any | None = None,
        review_decision_store: Any | None = None,
        command_repository: Any | None = None,
        claim_result_store: Any | None = None,
        review_continuation_worker_factory: Callable[..., Any] | None = None,
        review_continuation_worker_id: str | None = None,
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
        self._review_worker: Any | None = None
        self._review_worker_started = False
        self._continuation_open = False

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
        if self._active_names:
            return
        try:
            for name, runtime in self._runtimes.items():
                await _await_call(runtime.activate())
                self._active_names.append(name)
            self._continuation_open = True
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

    async def aclose(self) -> None:
        await self._stop_review_worker()
        failures: list[BaseException] = []
        for name in reversed(tuple(self._active_names)):
            try:
                await _await_call(self._runtimes[name].aclose())
            except BaseException as exc:
                failures.append(exc)
            else:
                self._active_names.remove(name)
        if failures:
            raise RuntimeError("GOVERNED_RUNTIME_GROUP_CLOSE_FAILED") from failures[0]

    def wake_review_continuation(self) -> None:
        worker = self._review_worker
        if worker is not None:
            worker.wake()

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
        runtime_name = _SURFACE_RUNTIME.get(surface)
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
        if surface not in _CLAIM_COMPLETION_SURFACES or not claim_ref:
            return
        result_ref = _continuation_result_ref(
            continuation_result,
            claim=claim,
            command=command,
        )
        if result_ref is None:
            return
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


async def _await_call(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{field_name} must be a mapping")
    return dict(value)


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


__all__ = ["GovernedRuntimeGroup"]
