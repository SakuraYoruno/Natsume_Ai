"""Governed Operator runtime slice for Task cancellation and Review controls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.accepted_operator_control_handler import (
    operator_control_handler_receipt_ref,
)
from host_brain.app.governed_operator_contracts import (
    GOVERNED_OPERATOR_GOVERNOR_REJECTED,
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
)
from host_brain.task_runtime.operator_control_support import (
    OPERATOR_CONTROL_COMMAND_IDS,
    OPERATOR_CONTROL_EFFECT_TYPE,
    OPERATOR_CONTROL_TARGET_ID,
    build_operator_control_governor_intent,
    normalize_operator_control_args,
    operator_control_action_kind,
    operator_control_ingress_requirements,
)
from host_brain.app.governed_operator_edge_support import (
    pending_operator_reviews,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.task_runtime import GovernedRuntimeError
from natsume_contracts.task import HostCommand, HostCommandSurface


class GovernedOperatorControlMixin:
    """Admit exact control commands into the shared immutable TaskGraph spine."""

    async def execute_operator_control(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        value = dict(payload or {})
        supplied = self._require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise _invalid("Operator control request_id is required")
        command_id = str(supplied.get("operator_command_id") or "").strip()
        ingress_action, ingress_path, required_scopes = (
            operator_control_ingress_requirements(command_id)
        )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = self._verify_operator_authority(
            supplied,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id=command_id,
            ingress_action=ingress_action,
            ingress_path=ingress_path,
            required_scopes=required_scopes,
        )
        principal_id = str(authority["principal_id"])
        args = normalize_operator_control_args(
            value,
            request_id=request_ref,
            principal_id=principal_id,
        )
        message = str(args["text"])
        intent_draft = {
            "route": "delegate",
            "target_id": OPERATOR_CONTROL_TARGET_ID,
            "domain": "organ_ops",
            "task": message,
            "action": operator_control_action_kind(command_id),
            "target": "task_runtime",
            "args": args,
        }
        received_at_ms = now_ms()
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get("operator_command_id")
                    or ""
                ).strip()
                != command_id
            ):
                raise _invalid(
                    "durable Operator control replay lacks exact command metadata"
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=build_operator_control_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_operator_ingress",
                    model_snapshot="server-owned-operator-control",
                    prompt_profile_id="operator-task-control-v1",
                )
                or {}
            )
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(
                    decision.get("detail")
                    or "Governor rejected Operator control"
                ),
            )
        terminal: dict[str, Any] | None = None
        try:
            async for raw in self._runtime.execute_stream(
                request_body={"input_source": "OPERATOR"},
                request_id=request_ref,
                session_id=f"operator-{request_ref}",
                ts_ms=received_at_ms,
                intent_draft=intent_draft,
                governor_result=decision,
                person_id=principal_id,
                sanitized_message=message,
                command_authority=authority,
            ):
                event = dict(raw or {})
                if bool(event.get("done")):
                    terminal = event
        except GovernedRuntimeError as exc:
            if exc.reason_code != INTENT_ADMISSION_REVIEW_REQUIRED:
                raise
            return self._pending_operator_control_review(
                request_id=request_ref,
                command_id=command_id,
            )
        if terminal is None:
            raise _invalid("governed Operator control emitted no terminal result")
        receipt_raw = terminal.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        outcome = require_durable_operator_control_outcome(
            task_store=self._task_store,
            review_store=self._review_store,
            execution_receipt=receipt,
            args=args,
        )
        accepted = (
            receipt.get("accepted") is True
            and str(receipt.get("status") or "").strip().upper() == "ACCEPTED"
        )
        replayed = receipt.get("replayed") is True
        success = bool(accepted and outcome["committed"])
        return {
            "success": success,
            "side_effects_executed": bool(success and not replayed),
            "historical_side_effects_executed": success,
            "exact_replay": replayed,
            "operator_command_id": command_id,
            "execution_receipt": receipt,
            "acceptance_effect_receipt": outcome,
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "terminal_status": terminal.get("status"),
            **dict(outcome.get("public_result") or {}),
        }

    def _pending_operator_control_review(
        self,
        *,
        request_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise _invalid("durable Operator control Review is unavailable")
        reviews = pending_operator_reviews(
            self._review_store,
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "operator_command_id": command_id,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }

    def _bind_operator_control_review_outcome(
        self,
        *,
        command_id: str,
        execution_receipt: Mapping[str, Any],
        args: Mapping[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        if command_id not in OPERATOR_CONTROL_COMMAND_IDS:
            return None
        outcome = require_durable_operator_control_outcome(
            task_store=self._task_store,
            review_store=self._review_store,
            execution_receipt=execution_receipt,
            args=args,
        )
        result["acceptance_effect_receipt"] = outcome
        result["historical_side_effects_executed"] = True
        result.update(dict(outcome.get("public_result") or {}))
        return result


def require_durable_operator_control_outcome(
    *,
    task_store: Any,
    review_store: Any,
    execution_receipt: Mapping[str, Any],
    args: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    revision = receipt.get("graph_revision")
    if (
        not graph_id
        or type(revision) is not int
        or revision < 1
        or not node_id
        or not attempt_id
    ):
        raise _invalid("Operator control receipt identity is invalid")
    rows = tuple(
        task_store.list_acceptance_effect_intents(
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
            limit=10,
        )
        or ()
    )
    effects = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "") == OPERATOR_CONTROL_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise _invalid("Operator control must bind one durable Acceptance Effect")
    effect = effects[0]
    payload_raw = effect.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    preview = payload.pop("_action_preview_context", None)
    command_id = str(args.get("operator_command_id") or "")
    expected_control_args = {
        str(key): value
        for key, value in args.items()
        if str(key) not in {"text", "route", "target_id"}
    }
    expected_payload = {
        "operator_command_id": command_id,
        "request_id": str(args.get("request_id") or ""),
        "operation_id": str(payload.get("operation_id") or ""),
        "principal_id": str(args.get("operator_actor_id") or ""),
        "ingress_claim_ref": str(payload.get("ingress_claim_ref") or ""),
        "ingress_audit_ref": str(payload.get("ingress_audit_ref") or ""),
        "input_sha256": str(payload.get("input_sha256") or ""),
        "control_args": expected_control_args,
    }
    if (
        str(effect.get("status") or "").upper() != "COMMITTED"
        or str(effect.get("attempt_id") or "") != attempt_id
        or payload != expected_payload
        or not isinstance(preview, Mapping)
        or not str(effect.get("receipt_ref") or "").strip()
    ):
        raise _invalid("Operator control Acceptance Effect binding drifted")
    outcome_identity, public_result = _control_outcome_identity(
        command_id=command_id,
        args=args,
        payload=payload,
        task_store=task_store,
        review_store=review_store,
    )
    expected_receipt = operator_control_handler_receipt_ref(
        payload,
        outcome_identity=outcome_identity,
    )
    if str(effect.get("receipt_ref") or "") != expected_receipt:
        raise _invalid("Operator control handler receipt drifted")
    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "effect_type": OPERATOR_CONTROL_EFFECT_TYPE,
        "handler_receipt_ref": expected_receipt,
        "operation_id": payload["operation_id"],
        "committed_ts_ms": int(effect.get("committed_ts_ms") or 0),
        "public_result": public_result,
    }


def _control_outcome_identity(
    *,
    command_id: str,
    args: Mapping[str, Any],
    payload: Mapping[str, Any],
    task_store: Any,
    review_store: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if command_id == "task_graph.cancel":
        intent = task_store.get_graph_cancellation_for_graph(
            str(args.get("graph_id") or ""),
            int(args.get("graph_revision") or 0),
        )
        if not isinstance(intent, Mapping):
            raise _invalid("durable cancellation intent is unavailable")
        body_raw = intent.get("payload")
        body = dict(body_raw) if isinstance(body_raw, Mapping) else dict(intent)
        if (
            body.get("request_id") != payload.get("request_id")
            or body.get("request_identity") != payload.get("ingress_audit_ref")
            or str(body.get("source") or "").upper() != "OPERATOR"
            or str(body.get("reason_code") or "").upper()
            != str(args.get("reason_code") or "").upper()
            or body.get("graph_id") != args.get("graph_id")
            or int(body.get("graph_revision") or 0)
            != int(args.get("graph_revision") or 0)
            or str(intent.get("status") or "").upper() != "FINALIZED"
        ):
            raise _invalid("durable cancellation intent binding drifted")
        identity = {
            "intent_ref": str(intent.get("intent_ref") or ""),
            "status": str(intent.get("status") or ""),
            "status_version": int(intent.get("status_version") or 0),
        }
        return identity, {
            "cancellation_intent_ref": identity["intent_ref"],
            "cancellation_status": identity["status"],
            "cancellation_status_version": identity["status_version"],
        }
    record = review_store.get_review(str(args.get("review_id") or ""))
    review_raw = record.get("review") if isinstance(record, Mapping) else None
    review = dict(review_raw) if isinstance(review_raw, Mapping) else {}
    expected_status = {
        "task_review.approve": "APPROVED",
        "task_review.reject": "REJECTED",
        "task_review.resume": "CONSUMED",
    }.get(command_id)
    current_status = str(review.get("status") or "")
    approval_history = (
        command_id == "task_review.approve"
        and current_status
        in {"APPROVED", "CONSUMED", "EXPIRED", "CANCELLED"}
        and str(review.get("token_ref") or "").startswith("review-token://")
    )
    if (
        not expected_status
        or (
            current_status != expected_status
            and not approval_history
        )
    ):
        raise _invalid("durable Review decision is unavailable")
    if command_id in {"task_review.approve", "task_review.reject"} and (
        review.get("decision_actor") != args.get("actor_id")
    ):
        raise _invalid("durable Review actor binding drifted")
    if command_id in {"task_review.approve", "task_review.resume"} and (
        review.get("subject_ref") != args.get("subject_ref")
        or review.get("subject_version") != args.get("subject_version")
        or review.get("resume_phase") != args.get("resume_phase")
        or review.get("checkpoint_id") != args.get("checkpoint_id")
    ):
        raise _invalid("durable Review subject binding drifted")
    if command_id == "task_review.resume" and (
        review.get("resume_result_ref") != args.get("resume_result_ref")
    ):
        raise _invalid("durable Review resume result binding drifted")
    identity: dict[str, Any] = {
        "review_id": str(review.get("review_id") or ""),
        "status": expected_status,
        "decision_actor": review.get("decision_actor"),
        "token_ref_public": (
            review.get("token_ref")
            if command_id == "task_review.approve"
            else None
        ),
        "resume_result_ref": (
            review.get("resume_result_ref")
            if command_id == "task_review.resume"
            else None
        ),
    }
    status_version = (
        1
        if command_id == "task_review.approve"
        else int(record.get("status_version") or 0)
    )
    return identity, {
        "review_id": identity["review_id"],
        "review_status": expected_status,
        "review_status_version": status_version,
        "resume_token_ref": identity["token_ref_public"],
        "resume_result_ref": identity["resume_result_ref"],
    }


def _invalid(detail: str) -> GovernedOperatorRuntimeError:
    return GovernedOperatorRuntimeError(
        GOVERNED_OPERATOR_RUNTIME_INVALID,
        detail,
    )


__all__ = [
    "GovernedOperatorControlMixin",
    "require_durable_operator_control_outcome",
]
