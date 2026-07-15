"""Durable Graph cancellation orchestration."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)

from .admission import AdmittedExecution
from .coordinator_records import (
    _exception_reason,
    _mapping,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_CANCELLATION_AUTHORITY_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .graph_cancellation_projection import GraphCancellationProjection
from .graph_cancellation_records import (
    _cancellation_budget_target,
    _cancellation_final_node_states,
    _cancellation_node_states,
    _cancellation_requested_node_states,
    _validated_graph_cancellation_intent,
)
from .local_graph_execution_registry import LocalGraphExecutionRegistry
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.coordinator")
_TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"


class GraphCancellationRuntime:
    """Own cancellation intent, continuation, and exact-replay orchestration."""

    __slots__ = (
        "_execution_registry",
        "_lease_ttl_ms",
        "_projection",
        "_runtime_outbox",
        "_store",
    )

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        runtime_outbox: RuntimeOutbox,
        execution_registry: LocalGraphExecutionRegistry,
        lease_ttl_ms: int,
    ) -> None:
        self._store = task_store
        self._runtime_outbox = runtime_outbox
        self._execution_registry = execution_registry
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._projection = GraphCancellationProjection(task_store=task_store)

    async def request_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        request_id: str,
        request_identity: str,
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Install durable cancellation authority before touching local tasks."""

        admitted.validate()
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "cancellation command differs from durable admission",
            )
        await _maybe_await(self._store.assert_graph_cancellation_ready())
        existing = await self._projection.cancellation_for_graph(admitted)
        cancellation: Mapping[str, Any]
        cancellation_replayed = False
        if existing is None:
            graph = await self._projection.graph(
                admitted,
                field_name="cancellation Graph",
            )
            nodes = await self._projection.nodes(admitted, graph=graph)
            graph_status = _status(graph)
            if graph_status not in {"CREATED", "ACTIVE", "BLOCKED"}:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"TaskGraph cannot be cancelled from {graph_status}",
                )
            budget_record = await self._projection.budget_record(admitted)
            node_states_before = _cancellation_node_states(nodes)
            node_states_after = _cancellation_requested_node_states(
                node_states_before
            )
            graph_version = _version(graph)
            graph_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
            )
            try:
                intent = normalize_graph_cancellation_intent(
                    {
                        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
                        "request_id": str(request_id or "").strip(),
                        "request_identity": str(request_identity or "").strip(),
                        "source": str(source or "").strip().upper(),
                        "reason_code": str(reason_code or "").strip().upper(),
                        "graph_id": admitted.graph_id,
                        "graph_revision": admitted.graph_revision,
                        "graph_status_before": graph_status,
                        "graph_status_version_before": graph_version,
                        "graph_target_status": "BLOCKED",
                        "budget_id": (
                            _required_record_text(budget_record, "budget_id")
                            if budget_record is not None
                            else None
                        ),
                        "budget_status_before": (
                            _status(budget_record)
                            if budget_record is not None
                            else None
                        ),
                        "budget_status_version_before": (
                            _version(budget_record)
                            if budget_record is not None
                            else None
                        ),
                        "node_states_before": node_states_before,
                        "node_states_after": node_states_after,
                        "graph_outbox_id": graph_outbox_seed.outbox_id,
                    }
                )
            except GraphCancellationContractError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"graph cancellation intent is invalid: {exc}",
                ) from exc
            graph_outbox = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
                evidence_refs=(intent.intent_ref, intent.payload["request_identity"]),
            )
            arguments = {
                "budget_id": intent.payload["budget_id"],
                "expected_graph_version": graph_version,
                "expected_budget_version": intent.payload[
                    "budget_status_version_before"
                ],
                "expected_node_states": node_states_before,
                "intent_ref": intent.intent_ref,
                "intent_payload": intent.payload,
                "graph_outbox": graph_outbox,
            }
            try:
                requested_raw = await _maybe_await(
                    self._store.request_graph_cancellation(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as first_exc:
                try:
                    requested_raw = await _maybe_await(
                        self._store.request_graph_cancellation(
                            admitted.graph_id,
                            admitted.graph_revision,
                            **arguments,
                        )
                    )
                except Exception as replay_exc:
                    durable = await self._projection.cancellation_for_graph(admitted)
                    if not isinstance(durable, Mapping) or _text(
                        durable.get("intent_ref")
                    ) != intent.intent_ref:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "graph cancellation intent could not be committed or exactly replayed",
                        ) from replay_exc
                    cancellation = dict(durable)
                    cancellation_replayed = True
                else:
                    _logger.warning(
                        "Replayed committed graph cancellation intent",
                        extra={
                            "graph_id": admitted.graph_id,
                            "graph_revision": admitted.graph_revision,
                            "intent_ref": intent.intent_ref,
                            "first_exception_type": type(first_exc).__name__,
                        },
                    )
                    requested = _mapping(
                        requested_raw,
                        field_name="graph cancellation request",
                    )
                    cancellation = _mapping(
                        requested.get("intent"),
                        field_name="graph cancellation intent",
                    )
                    cancellation_replayed = requested.get("replayed") is True
            else:
                requested = _mapping(
                    requested_raw,
                    field_name="graph cancellation request",
                )
                cancellation = _mapping(
                    requested.get("intent"),
                    field_name="graph cancellation intent",
                )
                cancellation_replayed = requested.get("replayed") is True
        else:
            cancellation = _mapping(
                existing,
                field_name="existing graph cancellation intent",
            )
            cancellation_replayed = True

        intent = _validated_graph_cancellation_intent(admitted, cancellation)
        requested_binding = {
            "request_id": str(request_id or "").strip(),
            "request_identity": str(request_identity or "").strip(),
            "source": str(source or "").strip().upper(),
            "reason_code": str(reason_code or "").strip().upper(),
        }
        durable_binding = {
            key: intent.payload[key]
            for key in (
                "request_id",
                "request_identity",
                "source",
                "reason_code",
            )
        }
        if durable_binding != requested_binding:
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation replay authority differs from its durable intent",
            )
        local_pending: tuple[str, ...] = ()
        try:
            await self._runtime_outbox.publish_pending()
        finally:
            # Once the durable intent exists, Outbox transport health must not
            # keep local workers running behind an already-installed fence.
            # The publish exception still propagates after every local task
            # has received cancellation and the bounded join has completed.
            local_pending = await self._execution_registry.cancel_and_join(
                graph_id=admitted.graph_id,
                graph_revision=admitted.graph_revision,
                intent_ref=intent.intent_ref,
                timeout_seconds=self._lease_ttl_ms / 1000.0,
            )
        return await self.continue_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
            cancellation=cancellation,
            local_pending=local_pending,
            replayed=cancellation_replayed,
        )

    async def continue_requested_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
    ) -> dict[str, Any] | None:
        cancellation = await self._projection.cancellation_for_graph(admitted)
        if cancellation is None:
            return None
        return await self.continue_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
            cancellation=cancellation,
            replayed=True,
        )

    async def continue_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        cancellation: Mapping[str, Any],
        local_pending: Sequence[str] = (),
        replayed: bool = False,
    ) -> dict[str, Any]:
        del command, work_order
        intent = _validated_graph_cancellation_intent(admitted, cancellation)
        intent_record = dict(cancellation)
        reconciliation_evidence_refs: tuple[str, ...] = ()
        if _status(intent_record) == "FINALIZED":
            decision_record = await self._projection.decision_for_intent(
                admitted,
                intent_ref=intent.intent_ref,
            )
            return await self._projection.terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=decision_record,
                local_pending=local_pending,
                replayed=True,
            )

        if not local_pending:
            try:
                advanced_raw = await _maybe_await(
                    self._store.advance_graph_cancellation(intent.intent_ref)
                )
            except Exception as exc:
                if _exception_reason(exc, "") not in {
                    _TASK_STORE_OWNERSHIP_CONFLICT,
                    "TASK_STORE_CANCELLATION_CONFLICT",
                }:
                    raise
                return await self._projection.terminal_event(
                    admitted=admitted,
                    intent_record=intent_record,
                    decision_record=None,
                    replayed=replayed,
                )
            else:
                if isinstance(advanced_raw, Mapping):
                    advanced_intent = advanced_raw.get("intent")
                    if isinstance(advanced_intent, Mapping):
                        intent_record = dict(advanced_intent)
                        intent = _validated_graph_cancellation_intent(
                            admitted,
                            intent_record,
                        )
                    raw_evidence = advanced_raw.get(
                        "reconciliation_evidence_refs"
                    )
                    if isinstance(raw_evidence, (list, tuple)):
                        reconciliation_evidence_refs = tuple(
                            dict.fromkeys(
                                _text(item)
                                for item in raw_evidence
                                if _text(item)
                            )
                        )
                    if advanced_raw.get("ready_to_finalize") is not True:
                        await self._runtime_outbox.publish_pending()
                        return await self._projection.terminal_event(
                            admitted=admitted,
                            intent_record=intent_record,
                            decision_record=None,
                            replayed=replayed,
                        )
                else:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "graph cancellation continuation returned no durable result",
                    )
                await self._runtime_outbox.publish_pending()

        refreshed = await self._projection.cancellation_for_graph(admitted)
        if not isinstance(refreshed, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable cancellation intent disappeared during continuation",
            )
        intent_record = dict(refreshed)
        intent = _validated_graph_cancellation_intent(admitted, intent_record)
        if _status(intent_record) == "FINALIZED":
            decision_record = await self._projection.decision_for_intent(
                admitted,
                intent_ref=intent.intent_ref,
            )
            return await self._projection.terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=decision_record,
                local_pending=local_pending,
                replayed=True,
            )
        if local_pending:
            return await self._projection.terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=None,
                local_pending=local_pending,
                replayed=replayed,
            )

        graph = await self._projection.graph(
            admitted,
            field_name="cancellation final Graph",
        )
        nodes = await self._projection.nodes(admitted, graph=graph)
        budget_record = await self._projection.budget_record(admitted)
        node_states_before = _cancellation_node_states(nodes)
        try:
            node_states_after = _cancellation_final_node_states(
                node_states_before
            )
        except GovernedRuntimeError:
            return await self._projection.terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=None,
                replayed=replayed,
            )
        if _status(graph) != "BLOCKED":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "requested graph cancellation lost its BLOCKED fence",
            )
        graph_version = _version(graph)
        budget_id = (
            _required_record_text(budget_record, "budget_id")
            if budget_record is not None
            else None
        )
        budget_status = _status(budget_record) if budget_record is not None else None
        budget_version = _version(budget_record) if budget_record is not None else None
        budget_target = _cancellation_budget_target(budget_status)
        graph_outbox_seed = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_CANCELLED",
        )
        budget_outbox_seed = None
        if budget_id is not None and budget_status != budget_target:
            budget_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=int(budget_version) + 1,
                event_type=f"BUDGET_RESERVATION_{budget_target}",
            )
        try:
            decision = normalize_graph_cancellation_decision(
                {
                    "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
                    "intent_ref": intent.intent_ref,
                    "request_id": intent.payload["request_id"],
                    "request_identity": intent.payload["request_identity"],
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "graph_status_before": "BLOCKED",
                    "graph_status_version_before": graph_version,
                    "graph_target_status": "CANCELLED",
                    "budget_id": budget_id,
                    "budget_status_before": budget_status,
                    "budget_status_version_before": budget_version,
                    "budget_target_status": budget_target,
                    "node_states_before": node_states_before,
                    "node_states_after": node_states_after,
                    "reconciliation_evidence_refs": list(
                        reconciliation_evidence_refs
                    ),
                    "graph_outbox_id": graph_outbox_seed.outbox_id,
                    "budget_outbox_id": (
                        budget_outbox_seed.outbox_id
                        if budget_outbox_seed is not None
                        else None
                    ),
                }
            )
        except GraphCancellationContractError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"graph cancellation decision is invalid: {exc}",
            ) from exc
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    decision.decision_ref,
                    intent.intent_ref,
                    intent.payload["request_identity"],
                )
            )
        )
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_CANCELLED",
            evidence_refs=evidence_refs,
        )
        budget_outbox = None
        if budget_outbox_seed is not None and budget_id is not None:
            budget_outbox = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=int(budget_version) + 1,
                event_type=f"BUDGET_RESERVATION_{budget_target}",
                evidence_refs=evidence_refs,
            )
        arguments = {
            "budget_id": budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": node_states_before,
            "intent_ref": intent.intent_ref,
            "decision_ref": decision.decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
        }
        try:
            finalized_raw = await _maybe_await(
                self._store.finalize_graph_cancellation(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            reason = _exception_reason(first_exc, "")
            if reason == _TASK_STORE_OWNERSHIP_CONFLICT:
                return await self._projection.terminal_event(
                    admitted=admitted,
                    intent_record=intent_record,
                    decision_record=None,
                    replayed=replayed,
                )
            try:
                finalized_raw = await _maybe_await(
                    self._store.finalize_graph_cancellation(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                latest = await self._projection.cancellation_for_graph(admitted)
                if not isinstance(latest, Mapping) or _status(latest) != "FINALIZED":
                    if (
                        _exception_reason(replay_exc, "")
                        == _TASK_STORE_OWNERSHIP_CONFLICT
                    ):
                        return await self._projection.terminal_event(
                            admitted=admitted,
                            intent_record=intent_record,
                            decision_record=None,
                            replayed=replayed,
                        )
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "graph cancellation could not be finalized or exactly replayed",
                    ) from replay_exc
                final_intent = dict(latest)
                final_decision = await self._projection.decision_for_intent(
                    admitted,
                    intent_ref=intent.intent_ref,
                )
                await self._runtime_outbox.publish_pending()
                return await self._projection.terminal_event(
                    admitted=admitted,
                    intent_record=final_intent,
                    decision_record=final_decision,
                    replayed=True,
                )
        finalized = _mapping(
            finalized_raw,
            field_name="final graph cancellation",
        )
        final_intent = _mapping(
            finalized.get("intent"),
            field_name="final graph cancellation intent",
        )
        final_decision = _mapping(
            finalized.get("decision"),
            field_name="final graph cancellation decision",
        )
        _validated_graph_cancellation_intent(admitted, final_intent)
        await self._runtime_outbox.publish_pending()
        return await self._projection.terminal_event(
            admitted=admitted,
            intent_record=final_intent,
            decision_record=final_decision,
            replayed=(replayed or finalized.get("replayed") is True),
        )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["GraphCancellationRuntime"]
