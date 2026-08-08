"""Bounded startup continuation for sealed Internal Agent checkpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from host_brain.contracts.agent_runtime.projection_recovery import (
    validate_projection_recovery_envelope,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import HostCommand, HostCommandSurface


class InternalStartupCheckpointContinuation:
    """Re-enter exact durable checkpoints without waiting for a client replay."""

    def __init__(
        self,
        *,
        task_store: Any | None,
        artifact_store: Any | None,
        claim_result_store: Any | None,
        command_repository: Any,
        application: Any,
        accepted_effect_runtime: Any,
        expected_node_route: str,
        authority_invalid_code: str,
        ingress_completion_failed_code: str,
        deferred_code: str,
        error_factory: Callable[[str, str], Exception],
    ) -> None:
        dependencies = (task_store, artifact_store)
        if any(value is not None for value in dependencies):
            if any(value is None for value in dependencies):
                raise TypeError(
                    "Internal startup checkpoint continuation requires TaskStore and artifact store"
                )
            for owner, method_name in (
                (task_store, "get_attempt"),
                (task_store, "get_graph"),
                (task_store, "get_node"),
                (task_store, "get_lease"),
                (task_store, "get_operation_reservation"),
                (
                    task_store,
                    "resolve_internal_agent_checkpoint_continuation_lineage",
                ),
                (artifact_store, "inventory_governed_seals"),
                (artifact_store, "read_projection_recovery"),
            ):
                if not callable(getattr(owner, method_name, None)):
                    raise TypeError(
                        f"Internal startup checkpoint authority lacks {method_name}"
                    )
        if claim_result_store is not None and not callable(
            getattr(claim_result_store, "complete_claim", None)
        ):
            raise TypeError("startup claim result store must expose complete_claim")
        self._tasks = task_store
        self._artifacts = artifact_store
        self._claims = claim_result_store
        self._commands = command_repository
        self._application = application
        self._accepted_effects = accepted_effect_runtime
        self._expected_node_route = str(expected_node_route)
        self._authority_invalid = str(authority_invalid_code)
        self._ingress_completion_failed = str(ingress_completion_failed_code)
        self._deferred = str(deferred_code)
        self._error_factory = error_factory
        self._report: tuple[dict[str, Any], ...] = ()

    @property
    def report(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._report)

    async def recover(
        self,
        *,
        page_limit: int = 100,
        scan_limit: int = 1_000,
    ) -> tuple[dict[str, Any], ...]:
        store = self._tasks
        artifacts = self._artifacts
        if store is None or artifacts is None:
            self._report = ()
            return ()
        if not 1 <= int(page_limit) <= 1_000 or not 1 <= int(scan_limit) <= 10_000:
            raise self._error(
                self._authority_invalid,
                "startup checkpoint continuation bounds are invalid",
            )
        cursor: str | None = None
        scanned = 0
        reports: list[dict[str, Any]] = []
        while True:
            seals = await asyncio.to_thread(
                artifacts.inventory_governed_seals,
                namespace="staging",
                limit=int(page_limit),
                after_stage_ref=cursor,
            )
            scanned += len(seals)
            if scanned > int(scan_limit):
                raise self._error(
                    self._authority_invalid,
                    "startup checkpoint inventory exceeds the verified bound",
                )
            for seal in seals:
                report = await self._recover_seal(seal=seal, store=store, artifacts=artifacts)
                reports.append(report)
            if len(seals) < int(page_limit):
                break
            cursor = str(seals[-1].stage_ref)
        self._report = tuple(dict(item) for item in reports)
        return self.report

    async def _recover_seal(
        self,
        *,
        seal: Any,
        store: Any,
        artifacts: Any,
    ) -> dict[str, Any]:
        stage_ref = str(getattr(seal, "stage_ref", ""))
        authority = dict(getattr(seal, "execution_authority", {}) or {})
        graph_id = str(authority.get("graph_id") or "")
        graph_revision = authority.get("graph_revision")
        attempt_id = str(authority.get("attempt_id") or "")
        if (
            not graph_id
            or type(graph_revision) is not int
            or graph_revision < 1
            or not attempt_id
        ):
            return self._blocked(stage_ref)
        try:
            envelope = validate_projection_recovery_envelope(
                await asyncio.to_thread(artifacts.read_projection_recovery, seal)
            )
            frozen = dict(envelope.get("frozen_node_input") or {})
            if any(frozen.get(key) != value for key, value in authority.items()):
                raise self._error(
                    self._authority_invalid,
                    "startup seal and frozen node authority differ",
                )
            reservation_id = str(frozen.get("reservation_id") or "")
            node_id = str(frozen.get("node_id") or "")
            historical_lease_id = str(frozen.get("lease_id") or "")
            # Keep authority reads ordered. Some durable-store decorators model
            # response loss and fencing transitions; parallel reads could observe
            # different transaction boundaries and turn a recoverable checkpoint
            # into an accidental HOLD.
            attempt = await asyncio.to_thread(store.get_attempt, attempt_id)
            reservation = await asyncio.to_thread(
                store.get_operation_reservation,
                reservation_id,
            )
            durable_graph = await asyncio.to_thread(
                store.get_graph,
                graph_id,
                graph_revision,
            )
            durable_node = await asyncio.to_thread(
                store.get_node,
                graph_id,
                graph_revision,
                node_id,
            )
            historical_lease = await asyncio.to_thread(
                store.get_lease,
                historical_lease_id,
            )
            node_status = str(durable_node.get("status") or "")
            recoverable = (
                str(attempt.get("status") or "") == "RECOVERABLE"
                and attempt.get("owner_id") is None
                and str(reservation.get("status") or "")
                in {"RECOVERY_REQUIRED", "COMMITTED"}
                and str(durable_graph.get("status") or "") == "ACTIVE"
                and node_status == "RUNNING"
                and str(historical_lease.get("status") or "") == "EXPIRED"
            )
            succeeded_checkpoint = (
                str(attempt.get("status") or "") == "SUCCEEDED"
                and str(reservation.get("status") or "") == "COMMITTED"
                and str(durable_graph.get("status") or "") == "ACTIVE"
                and node_status in {"RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"}
            )
            if succeeded_checkpoint:
                await asyncio.to_thread(
                    store.resolve_internal_agent_checkpoint_continuation_lineage,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                    node_id=node_id,
                    attempt_id=attempt_id,
                    historical_lease_id=historical_lease_id,
                    historical_lease_owner=str(frozen.get("lease_owner_id") or ""),
                    historical_fencing_token=int(frozen.get("fencing_token") or 0),
                    expected_node_status=node_status,
                )
            if not recoverable and not succeeded_checkpoint:
                return {"stage_ref": stage_ref, "status": "HOLD", "reason_code": None}
            return await self._continue_admission(
                stage_ref=stage_ref,
                graph_id=graph_id,
                graph_revision=graph_revision,
                authority=authority,
            )
        except Exception as exc:
            return self._blocked(
                stage_ref,
                reason_code=str(getattr(exc, "reason_code", self._authority_invalid)),
            )

    async def _continue_admission(
        self,
        *,
        stage_ref: str,
        graph_id: str,
        graph_revision: int,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
        if not isinstance(record, Mapping):
            raise self._error(
                self._authority_invalid,
                "startup seal has no exact HostCommand admission",
            )
        command = record.get("command")
        work_order = record.get("work_order")
        assignment = record.get("assignment")
        graph = record.get("graph")
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        if (
            not isinstance(command, HostCommand)
            or command.surface is not HostCommandSurface.INTERNAL
            or not isinstance(work_order, WorkOrderV3)
            or assignment is None
            or graph is None
            or len(nodes) != 1
            or nodes[0].node_id != authority.get("node_id")
            or nodes[0].route != self._expected_node_route
        ):
            raise self._error(
                self._authority_invalid,
                "startup seal is not an exact Internal Agent admission",
            )
        terminal: dict[str, Any] | None = None
        terminal_count = 0
        async for event in self._application.execute_startup_continuation_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        ):
            if bool(event.get("done")):
                terminal_count += 1
                terminal = await self._accepted_effects.dispatch(event, command=command)
        if terminal_count != 1 or terminal is None:
            raise self._error(
                self._authority_invalid,
                "startup checkpoint continuation returned no unique terminal result",
            )
        await self._complete_claim(command=command, terminal=terminal)
        receipt = (
            dict(terminal.get("execution_receipt") or {})
            if isinstance(terminal.get("execution_receipt"), Mapping)
            else {}
        )
        completed = (
            receipt.get("accepted") is True
            and str(terminal.get("internal_agent_effect_status") or "").strip().upper()
            == "COMMITTED"
        )
        return {
            "stage_ref": stage_ref,
            "status": "COMPLETED" if completed else "DEFERRED",
            "reason_code": (
                str(terminal.get("reason_code") or "").strip()
                or (None if completed else self._deferred)
            ),
        }

    async def _complete_claim(
        self,
        *,
        command: HostCommand,
        terminal: Mapping[str, Any],
    ) -> None:
        if self._claims is None:
            return
        effect_status = str(terminal.get("internal_agent_effect_status") or "")
        result_ref = str(terminal.get("internal_agent_result_receipt_ref") or "").strip()
        if effect_status != "COMMITTED" or not result_ref:
            return
        exact = await self._accepted_effects.exact_result_receipt(
            command_id=command.command_id,
            expected_receipt_ref=result_ref,
        )
        if exact != result_ref:
            raise self._error(
                self._ingress_completion_failed,
                "startup checkpoint result attribution is missing",
            )
        await asyncio.to_thread(
            self._claims.complete_claim,
            claim_ref=command.ingress_claim_ref,
            result_ref=result_ref,
        )

    def _blocked(self, stage_ref: str, *, reason_code: str | None = None) -> dict[str, Any]:
        return {
            "stage_ref": stage_ref,
            "status": "BLOCKED",
            "reason_code": str(reason_code or self._authority_invalid),
        }

    def _error(self, reason_code: str, detail: str) -> Exception:
        return self._error_factory(str(reason_code), str(detail))
