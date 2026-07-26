"""Acceptance-bound execution of one signed Operation reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
from typing import Any

from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)


OPERATION_RECONCILIATION_EFFECT_TYPE = "task.operation_reconciliation"


class AcceptedOperationReconciliationHandler:
    """Apply or exactly replay a signed request only after Task acceptance."""

    __slots__ = ("_continuation_provider", "_evidence_store", "_service")

    def __init__(
        self,
        *,
        service: Any,
        evidence_store: Any,
        continuation_provider: Callable[[], Any],
    ) -> None:
        if not callable(getattr(service, "reconcile_operation", None)):
            raise TypeError(
                "operation reconciliation handler requires reconcile_operation"
            )
        if not callable(continuation_provider):
            raise TypeError(
                "operation reconciliation continuation provider must be callable"
            )
        if not callable(
            getattr(
                evidence_store,
                "get_operation_reconciliation_evidence",
                None,
            )
        ):
            raise TypeError(
                "operation reconciliation handler requires evidence store"
            )
        self._service = service
        self._evidence_store = evidence_store
        self._continuation_provider = continuation_provider

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._apply(payload)

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._apply(payload)

    async def _apply(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        durable_request = _request(payload)
        evidence_raw = (
            self._evidence_store.get_operation_reconciliation_evidence(
                durable_request["evidence_ref"]
            )
        )
        if not isinstance(evidence_raw, Mapping):
            raise ValueError(
                "operation reconciliation evidence could not be reopened"
            )
        evidence = dict(evidence_raw)
        if (
            _durable_request_from_evidence(evidence) != durable_request
            or str(evidence.get("request_id") or "").strip()
            != durable_request["request_id"]
        ):
            raise ValueError(
                "operation reconciliation evidence binding drifted"
            )
        service_request = {
            key: evidence.get(key)
            for key in (
                "intent_ref",
                "reservation_id",
                "expected_version",
                "action",
                "reconciler_actor_id",
                "downstream_receipt_ref",
                "attestation_ref",
                "attestation_key_id",
                "attestation_signature",
            )
        }
        try:
            reconciliation_raw = self._service.reconcile_operation(
                **service_request
            )
            reconciliation = (
                await reconciliation_raw
                if inspect.isawaitable(reconciliation_raw)
                else reconciliation_raw
            )
        except GraphCancellationOperationReconciliationError as exc:
            if exc.side_effects_may_have_run:
                raise AcceptanceEffectOutcomeUnknown(
                    "signed Operation reconciliation outcome is uncertain"
                ) from exc
            raise
        if not isinstance(reconciliation, Mapping):
            raise TypeError("operation reconciliation result must be a mapping")
        result = dict(reconciliation)
        continuation: dict[str, Any] | None = None
        initial_operation_raw = result.get("operation")
        initial_operation = (
            dict(initial_operation_raw)
            if isinstance(initial_operation_raw, Mapping)
            else {}
        )
        reacquisition_continuation = (
            service_request["action"] == "REACQUIRE"
            and str(initial_operation.get("status") or "").upper()
            != "COMMITTED"
        )
        checkpoint_continuation = (
            service_request["action"] == "COMMIT"
            and service_request["intent_ref"] is None
            and str(initial_operation.get("status") or "").upper()
            == "COMMITTED"
        )
        if reacquisition_continuation or checkpoint_continuation:
            runtime = self._continuation_provider()
            continuation_method = (
                "continue_operation_reacquisition"
                if reacquisition_continuation
                else "continue_operation_reconciliation"
            )
            continue_target = getattr(
                runtime,
                continuation_method,
                None,
            )
            if not callable(continue_target):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation has no app-local continuation owner"
                )
            try:
                continued_raw = continue_target(result)
                continued = (
                    await continued_raw
                    if inspect.isawaitable(continued_raw)
                    else continued_raw
                )
            except Exception as exc:
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation continuation outcome is uncertain"
                ) from exc
            if not isinstance(continued, Mapping):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation continuation result is invalid"
                )
            continuation = dict(continued)
            receipt_raw = continuation.get("execution_receipt")
            receipt = (
                dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
            )
            if (
                receipt.get("accepted") is not True
                or str(receipt.get("status") or "").strip().upper()
                != "ACCEPTED"
                or str(receipt.get("graph_id") or "").strip()
                != str(result.get("graph_id") or "").strip()
                or receipt.get("graph_revision") != result.get("graph_revision")
            ):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled target graph has no exact accepted receipt"
                )
            if reacquisition_continuation:
                try:
                    settled_raw = self._service.reconcile_operation(
                        **service_request
                    )
                    settled = (
                        await settled_raw
                        if inspect.isawaitable(settled_raw)
                        else settled_raw
                    )
                except Exception as exc:
                    raise AcceptanceEffectOutcomeUnknown(
                        "reacquired Operation final state cannot be reopened"
                    ) from exc
                if not isinstance(settled, Mapping):
                    raise AcceptanceEffectOutcomeUnknown(
                        "reacquired Operation final state is invalid"
                    )
                result = dict(settled)
        operation_raw = result.get("operation")
        operation = (
            dict(operation_raw) if isinstance(operation_raw, Mapping) else {}
        )
        terminal_raw = result.get("terminal")
        terminal = (
            dict(terminal_raw)
            if isinstance(terminal_raw, Mapping)
            else None
        )
        terminal_failure = (
            service_request["action"] == "RELEASE"
            and service_request["intent_ref"] is None
        )
        if terminal_failure:
            try:
                _terminal_failure_receipt_record(terminal)
            except ValueError as exc:
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled terminal failure has no exact Task settlement"
                ) from exc
        resolved_recovery_case = _resolved_recovery_case(
            evidence_store=self._evidence_store,
            evidence=evidence,
        )
        receipt_ref = operation_reconciliation_handler_receipt_ref(
            request=evidence,
            operation=operation,
            terminal=terminal,
        )
        return {
            "receipt_ref": receipt_ref,
            "status": "COMMITTED",
            "reservation_id": service_request["reservation_id"],
            "action": service_request["action"],
            "evidence_ref": durable_request["evidence_ref"],
            "operation_status": str(operation.get("status") or "").upper(),
            "operation_status_version": operation.get("status_version"),
            "target_continuation": continuation,
            "target_terminal": terminal,
            "recovery_case": resolved_recovery_case,
            "exact_replay": bool(result.get("replayed")),
        }


def operation_reconciliation_handler_receipt_ref(
    *,
    request: Mapping[str, Any],
    operation: Mapping[str, Any],
    terminal: Mapping[str, Any] | None = None,
) -> str:
    checkpoint_commit = (
        str(request.get("action") or "").strip().upper() == "COMMIT"
        and not str(request.get("intent_ref") or "").strip()
    )
    terminal_failure = (
        str(request.get("action") or "").strip().upper() == "RELEASE"
        and not str(request.get("intent_ref") or "").strip()
    )
    terminal_failure_record = (
        _terminal_failure_receipt_record(terminal)
        if terminal_failure
        else None
    )
    node_failure = (
        terminal_failure_record is not None
        and terminal_failure_record["authority_kind"]
        == "RECONCILED_OPERATION_NODE_FAILURE"
    )
    recovery_case_ref = str(
        request.get("recovery_case_ref") or ""
    ).strip() or None
    material: dict[str, Any] = {
        "schema_version": (
            "Natsume.task.operation_reconciliation_receipt.v5"
            if recovery_case_ref is not None
            else (
                "Natsume.task.operation_reconciliation_receipt.v2"
                if checkpoint_commit
                else (
                    (
                        "Natsume.task.operation_reconciliation_receipt.v4"
                        if node_failure
                        else "Natsume.task.operation_reconciliation_receipt.v3"
                    )
                    if terminal_failure
                    else "Natsume.task.operation_reconciliation_receipt.v1"
                )
            )
        ),
        "reservation_id": str(request.get("reservation_id") or "").strip(),
        "expected_version": request.get("expected_version"),
        "action": str(request.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            request.get("reconciler_actor_id") or ""
        ).strip(),
        "attestation_ref": str(request.get("attestation_ref") or "").strip(),
        "attestation_key_id": str(
            request.get("attestation_key_id") or ""
        ).strip(),
        "operation_status": str(operation.get("status") or "").strip().upper(),
        "operation_status_version": operation.get("status_version"),
        "operation_fencing_token": operation.get("fencing_token"),
    }
    if checkpoint_commit:
        material.update(
            {
                "downstream_receipt_ref": str(
                    request.get("downstream_receipt_ref") or ""
                ).strip(),
                "operation_receipt_ref": str(
                    operation.get("receipt_ref") or ""
                ).strip(),
                "operation_reconciliation_action": str(
                    operation.get("reconciliation_action") or ""
                )
                .strip()
                .upper(),
                "operation_reconciliation_outcome": str(
                    operation.get("reconciliation_outcome") or ""
                )
                .strip()
                .upper(),
            }
        )
    if terminal_failure:
        material["terminal_failure"] = terminal_failure_record
    if recovery_case_ref is not None:
        material["recovery_case"] = {
            "case_ref": recovery_case_ref,
            "subject_sha256": str(
                request.get("recovery_case_subject_sha256") or ""
            ),
        }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "operation-reconcile://receipt/sha256/" + hashlib.sha256(
        encoded
    ).hexdigest()


def _terminal_failure_receipt_record(
    terminal: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate live settlement state and return stable receipt material.

    Multi-node Graph/Budget settlement is a later 0090 decision.  Its live
    PENDING/COMMITTED projection is validated here but deliberately omitted
    from the v4 receipt digest so the accepted Operation effect remains an
    exact replay after that append-only Graph settlement commits.
    """

    terminal_record = dict(terminal or {})
    common = {
        "authority_kind": str(
            terminal_record.get("authority_kind") or ""
        ),
        "node_status": str(
            terminal_record.get("node_status") or ""
        ),
        "attempt_status": str(
            terminal_record.get("attempt_status") or ""
        ),
        "lease_status": str(
            terminal_record.get("lease_status") or ""
        ),
        "failure_reason_code": str(
            terminal_record.get("failure_reason_code") or ""
        ),
        "error_ref": str(
            terminal_record.get("error_ref") or ""
        ),
    }
    if (
        common["node_status"] != "FAILED"
        or common["attempt_status"] != "FAILED"
        or common["lease_status"] != "EXPIRED"
        or not common["failure_reason_code"]
        or not common["error_ref"].startswith(
            "taskerror://operation-failure/sha256/"
        )
    ):
        raise ValueError(
            "terminal failure reconciliation receipt is incomplete"
        )
    graph_status = str(
        terminal_record.get("graph_status") or ""
    )
    budget_status = str(
        terminal_record.get("budget_status") or ""
    )
    if common["authority_kind"] == "RECONCILED_OPERATION_TERMINAL_FAILURE":
        if graph_status != "FAILED" or budget_status != "RELEASED":
            raise ValueError(
                "single-node failure terminal is incomplete"
            )
        return {
            "authority_kind": common["authority_kind"],
            "graph_status": graph_status,
            "node_status": common["node_status"],
            "attempt_status": common["attempt_status"],
            "budget_status": budget_status,
            "lease_status": common["lease_status"],
            "failure_reason_code": common["failure_reason_code"],
            "error_ref": common["error_ref"],
        }
    if (
        common["authority_kind"] != "RECONCILED_OPERATION_NODE_FAILURE"
        or str(terminal_record.get("settlement_scope") or "")
        != "NODE_ONLY"
    ):
        raise ValueError(
            "multi-node failure authority is incomplete"
        )
    settlement_status = str(
        terminal_record.get("graph_settlement_status") or ""
    )
    settlement_ref = (
        str(terminal_record.get("parallel_settlement_ref") or "").strip()
        or None
    )
    pending = (
        settlement_status == "PENDING"
        and graph_status == "ACTIVE"
        and budget_status in {"ACTIVE", "EXCEEDED"}
        and settlement_ref is None
    )
    committed = (
        settlement_status == "COMMITTED"
        and graph_status == "FAILED"
        and budget_status == "RELEASED"
        and settlement_ref is not None
        and settlement_ref.startswith("taskbatch://sha256/")
    )
    if not pending and not committed:
        raise ValueError(
            "multi-node failure Graph settlement is incomplete"
        )
    return {
        "authority_kind": common["authority_kind"],
        "settlement_scope": "NODE_ONLY",
        "node_status": common["node_status"],
        "attempt_status": common["attempt_status"],
        "lease_status": common["lease_status"],
        "failure_reason_code": common["failure_reason_code"],
        "error_ref": common["error_ref"],
    }


def _request(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    expected_version = value.get("expected_version")
    evidence_ref = str(value.get("evidence_ref") or "").strip()
    evidence_digest = str(
        value.get("evidence_digest") or ""
    ).strip().lower()
    if evidence_digest.startswith("sha256:"):
        evidence_digest = evidence_digest.removeprefix("sha256:")
    request = {
        "request_id": str(value.get("request_id") or "").strip(),
        "evidence_ref": evidence_ref,
        "evidence_digest": f"sha256:{evidence_digest}",
        "reservation_id": str(value.get("reservation_id") or "").strip(),
        "expected_version": expected_version,
        "action": str(value.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
    }
    recovery_case_ref = str(
        value.get("recovery_case_ref") or ""
    ).strip() or None
    recovery_case_digest = str(
        value.get("recovery_case_subject_sha256") or ""
    ).strip().lower() or None
    if (
        str(value.get("operator_command_id") or "").strip()
        != "task_operation.reconcile"
        or not request["request_id"]
        or not evidence_ref.startswith(
            "operation-reconcile-evidence://sha256/"
        )
        or evidence_ref.rsplit("/", 1)[-1] != evidence_digest
        or len(evidence_digest) != 64
        or any(char not in "0123456789abcdef" for char in evidence_digest)
        or not request["reservation_id"]
        or type(expected_version) is not int
        or expected_version < 0
        or request["action"] not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not request["reconciler_actor_id"]
        or ((recovery_case_ref is None) != (recovery_case_digest is None))
        or (
            recovery_case_ref is not None
            and (
                not recovery_case_ref.startswith(
                    "operation-recovery-hold://sha256/"
                )
                or len(recovery_case_ref.rsplit("/", 1)[-1]) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in recovery_case_ref.rsplit("/", 1)[-1]
                )
                or recovery_case_digest
                != "sha256:" + recovery_case_ref.rsplit("/", 1)[-1]
            )
        )
    ):
        raise ValueError("operation reconciliation effect payload is invalid")
    if recovery_case_ref is not None:
        request["recovery_case_ref"] = recovery_case_ref
        request["recovery_case_subject_sha256"] = recovery_case_digest
    return request


def _durable_request_from_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    request = {
        "request_id": str(value.get("request_id") or "").strip(),
        "evidence_ref": str(value.get("evidence_ref") or "").strip(),
        "evidence_digest": str(
            value.get("evidence_digest") or ""
        ).strip().lower(),
        "reservation_id": str(value.get("reservation_id") or "").strip(),
        "expected_version": value.get("expected_version"),
        "action": str(value.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
    }
    recovery_case_ref = str(
        value.get("recovery_case_ref") or ""
    ).strip() or None
    if recovery_case_ref is not None:
        request["recovery_case_ref"] = recovery_case_ref
        request["recovery_case_subject_sha256"] = str(
            value.get("recovery_case_subject_sha256") or ""
        ).strip().lower()
    return request


def _resolved_recovery_case(
    *,
    evidence_store: Any,
    evidence: Mapping[str, Any],
) -> dict[str, Any] | None:
    case_ref = str(evidence.get("recovery_case_ref") or "").strip()
    if not case_ref:
        return None
    reopen = getattr(evidence_store, "get_operation_recovery_hold", None)
    if not callable(reopen):
        raise AcceptanceEffectOutcomeUnknown(
            "Operation recovery case reader is unavailable"
        )
    try:
        raw = reopen(case_ref)
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "Operation recovery case outcome cannot be reopened"
        ) from exc
    case = dict(raw) if isinstance(raw, Mapping) else {}
    if (
        case.get("current") is not False
        or str(case.get("case_ref") or "") != case_ref
        or str(case.get("subject_sha256") or "")
        != str(evidence.get("recovery_case_subject_sha256") or "")
        or str(case.get("reservation_id") or "")
        != str(evidence.get("reservation_id") or "")
        or case.get("operation_status_version")
        != evidence.get("expected_version")
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Operation reconciliation recovery case remains unresolved"
        )
    return {
        "case_ref": case_ref,
        "case_kind": str(case.get("case_kind") or ""),
        "classification": str(case.get("classification") or ""),
        "subject_sha256": str(case.get("subject_sha256") or ""),
        "current": False,
    }


__all__ = [
    "AcceptedOperationReconciliationHandler",
    "OPERATION_RECONCILIATION_EFFECT_TYPE",
    "operation_reconciliation_handler_receipt_ref",
]
