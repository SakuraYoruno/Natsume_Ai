"""Post-Acceptance execution for governed Task cancellation and Review control."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any

from host_brain.app.acceptance_effect_handler_common import (
    _opaque_failure,
    _receipt,
)
from host_brain.task_runtime.operator_control_support import (
    OPERATOR_CONTROL_COMMAND_IDS,
    OPERATOR_CONTROL_EFFECT_TYPE,
    operator_control_ingress_requirements,
    operator_control_operation_id,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class AcceptedOperatorControlHandler:
    """Execute one immutable Operator control only after outer Acceptance."""

    def __init__(
        self,
        *,
        graph_cancellation_service_provider: Callable[[], Any],
        secure_ingress_authority_resolver: Any,
        task_store: Any,
        review_command_service: Any,
        review_store: Any,
    ) -> None:
        if not callable(graph_cancellation_service_provider):
            raise TypeError("graph_cancellation_service_provider must be callable")
        if not callable(
            getattr(
                secure_ingress_authority_resolver,
                "resolve_committed_authority_by_audit",
                None,
            )
        ):
            raise TypeError("Secure Ingress authority resolver is required")
        if not callable(
            getattr(task_store, "get_graph_cancellation_for_graph", None)
        ):
            raise TypeError("TaskStore cancellation reader is required")
        if not callable(getattr(review_store, "get_review", None)):
            raise TypeError("ReviewStore reader is required")
        self._cancellation_service_provider = (
            graph_cancellation_service_provider
        )
        self._authority_resolver = secure_ingress_authority_resolver
        self._tasks = task_store
        self._review_commands = review_command_service
        self._reviews = review_store

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = _accepted_operator_control_request(payload)
        command_id = str(value["operator_command_id"])
        try:
            if command_id == "task_graph.cancel":
                await self._request_graph_cancellation(value)
            else:
                self._apply_review_control(value)
        except Exception as exc:
            try:
                return await self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                may_have_run = getattr(exc, "side_effects_may_have_run", None)
                if may_have_run is False:
                    # A typed pre-boundary rejection is known not to have
                    # installed a cancellation intent.  Preserve that typed
                    # failure so the dispatcher may use its bounded retry
                    # policy; converting it to outcome-unknown would force an
                    # unnecessary reconciliation case.
                    raise exc
                raise AcceptanceEffectOutcomeUnknown(
                    "Operator control invocation has no exact durable "
                    f"attribution after {_opaque_failure(exc)}; "
                    f"{reconciliation.detail}"
                ) from exc
        return await self.reconcile(payload)

    async def reconcile(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        value = _accepted_operator_control_request(payload)
        command_id = str(value["operator_command_id"])
        args = dict(value["control_args"])
        if command_id == "task_graph.cancel":
            outcome = self._tasks.get_graph_cancellation_for_graph(
                str(args["graph_id"]),
                int(args["graph_revision"]),
            )
            if not _cancellation_matches(value, outcome):
                raise AcceptanceEffectOutcomeUnknown(
                    "Operator cancellation has no exact durable intent"
                )
            intent = dict(outcome or {})
            if str(intent.get("status") or "").upper() != "FINALIZED":
                raise AcceptanceEffectOutcomeUnknown(
                    "Operator cancellation has not reached its durable terminal state"
                )
            receipt_ref = operator_control_handler_receipt_ref(
                value,
                outcome_identity={
                    "intent_ref": str(intent.get("intent_ref") or ""),
                    "status": str(intent.get("status") or ""),
                    "status_version": int(intent.get("status_version") or 0),
                },
            )
            return {
                "receipt_ref": receipt_ref,
                "operator_command_id": command_id,
                "operation_id": value["operation_id"],
                "cancellation_intent_ref": intent.get("intent_ref"),
                "cancellation_status": intent.get("status"),
                "cancellation_status_version": intent.get("status_version"),
            }
        record = self._reviews.get_review(str(args["review_id"]))
        review = (
            dict(record.get("review") or {})
            if isinstance(record, Mapping)
            else {}
        )
        status = _expected_review_status(command_id)
        if not _review_matches(command_id, args, record):
            raise AcceptanceEffectOutcomeUnknown(
                "Operator Review control has no exact durable decision"
            )
        outcome_identity: dict[str, Any] = {
            "review_id": str(review["review_id"]),
            "status": status,
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
        status_version = _review_outcome_status_version(command_id, record)
        return {
            "receipt_ref": operator_control_handler_receipt_ref(
                value,
                outcome_identity=outcome_identity,
            ),
            "operator_command_id": command_id,
            "operation_id": value["operation_id"],
            "review_id": review["review_id"],
            "review_status": status,
            "review_status_version": status_version,
            "resume_token_ref": (
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

    async def _request_graph_cancellation(
        self,
        value: Mapping[str, Any],
    ) -> None:
        args = dict(value["control_args"])
        service = self._cancellation_service_provider()
        request = getattr(service, "request_graph_cancellation", None)
        if not callable(request):
            raise RuntimeError("APP_LOCAL_GRAPH_CANCELLATION_SERVICE_UNAVAILABLE")
        authority = _reopen_cancellation_authority(
            value,
            resolver=self._authority_resolver,
        )
        result = request(
            graph_id=str(args["graph_id"]),
            graph_revision=int(args["graph_revision"]),
            reason_code=str(args["reason_code"]),
            authority=authority,
        )
        if hasattr(result, "__await__"):
            await result

    def _apply_review_control(self, value: Mapping[str, Any]) -> None:
        args = dict(value["control_args"])
        command_id = str(value["operator_command_id"])
        service = self._review_commands
        if command_id == "task_review.approve":
            approve = getattr(service, "approve_review", None)
            if not callable(approve):
                raise RuntimeError("APP_LOCAL_REVIEW_APPROVE_UNAVAILABLE")
            # The returned bearer is intentionally discarded.  The governed
            # Operator route exposes only ReviewItem.token_ref_public, which is
            # durable and recoverable while remaining unusable by the ordinary
            # bearer-only resume API.
            approve(
                str(args["review_id"]),
                actor_id=str(args["actor_id"]),
                expected_subject_ref=str(args["subject_ref"]),
                expected_subject_version=int(args["subject_version"]),
                expected_resume_phase=str(args["resume_phase"]),
                expected_checkpoint_id=str(args["checkpoint_id"]),
            )
            return
        if command_id == "task_review.reject":
            reject = getattr(service, "reject_review", None)
            if not callable(reject):
                raise RuntimeError("APP_LOCAL_REVIEW_REJECT_UNAVAILABLE")
            reject(
                str(args["review_id"]),
                actor_id=str(args["actor_id"]),
            )
            return
        resume = getattr(
            service,
            "resume_review_by_operator_token_ref",
            None,
        )
        if not callable(resume):
            raise RuntimeError("APP_LOCAL_REVIEW_RESUME_UNAVAILABLE")
        record = self._reviews.get_review(str(args["review_id"]))
        review = (
            dict(record.get("review") or {})
            if isinstance(record, Mapping)
            else {}
        )
        public_ref = str(review.get("token_ref") or "")
        if (
            "sha256:" + hashlib.sha256(public_ref.encode("utf-8")).hexdigest()
            != str(args["resume_binding_sha256"])
        ):
            raise ValueError("Operator Review resume public binding drifted")
        resume(
            str(args["review_id"]),
            token_ref_public=public_ref,
            subject_ref=str(args["subject_ref"]),
            subject_version=int(args["subject_version"]),
            resume_phase=str(args["resume_phase"]),
            checkpoint_id=str(args["checkpoint_id"]),
            resume_result_ref=str(args["resume_result_ref"]),
        )


def operator_control_handler_receipt_ref(
    payload: Mapping[str, Any],
    *,
    outcome_identity: Mapping[str, Any],
) -> str:
    value = dict(payload or {})
    value.pop("_action_preview_context", None)
    value.pop("_action_preview_terminal_proof", None)
    return _receipt(
        "operator-control",
        {
            "operation_id": value.get("operation_id"),
            "operator_command_id": value.get("operator_command_id"),
            "request_id": value.get("request_id"),
            "outcome_identity": dict(outcome_identity or {}),
        },
    )


def _accepted_operator_control_request(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from host_brain.governor.action_preview import (  # noqa: PLC0415
        ActionPreviewService,
    )

    value = dict(payload or {})
    raw_preview = value.pop("_action_preview_terminal_proof", None)
    preview = dict(raw_preview) if isinstance(raw_preview, Mapping) else {}
    required = {
        "operator_command_id",
        "request_id",
        "operation_id",
        "principal_id",
        "ingress_claim_ref",
        "ingress_audit_ref",
        "input_sha256",
        "control_args",
    }
    command_id = str(value.get("operator_command_id") or "").strip()
    control_args = (
        dict(value.get("control_args") or {})
        if isinstance(value.get("control_args"), Mapping)
        else {}
    )
    if (
        set(value) != required
        or command_id not in OPERATOR_CONTROL_COMMAND_IDS
        or value.get("operation_id")
        != operator_control_operation_id(
            request_id=str(value.get("request_id") or ""),
            command_id=command_id,
        )
        or control_args.get("operator_command_id") != command_id
        or control_args.get("request_id") != value.get("request_id")
        or control_args.get("operator_actor_id") != value.get("principal_id")
        or set(preview) != {"service", "event_id", "boundary"}
        or not isinstance(preview.get("service"), ActionPreviewService)
        or not str(preview.get("event_id") or "").strip()
        or not isinstance(preview.get("boundary"), Mapping)
    ):
        raise ValueError("accepted Operator control payload drifted")
    boundary = dict(preview["boundary"])
    authority_context = boundary.get("authority_context")
    scope = str(boundary.get("action_scope") or "").strip()
    if (
        str(boundary.get("action_kind") or "")
        != f"acceptance-effect.{OPERATOR_CONTROL_EFFECT_TYPE}"
        or int(boundary.get("risk_level") or 0) != 4
        or boundary.get("action_args") != value
        or not isinstance(authority_context, Mapping)
        or set(authority_context)
        != {
            "schema_version",
            "effect_id",
            "lease_owner_id",
            "fencing_token",
        }
        or authority_context.get("schema_version")
        != "Natsume.action_preview.acceptance_effect_authority_request.v1"
        or str(authority_context.get("effect_id") or "").strip() != scope
        or not scope
        or not str(authority_context.get("lease_owner_id") or "").strip()
        or isinstance(authority_context.get("fencing_token"), bool)
        or int(authority_context.get("fencing_token") or 0) < 1
    ):
        raise ValueError("accepted Operator control Preview boundary drifted")
    reopened = preview["service"].require_execution(
        event_id=str(preview["event_id"]),
        **boundary,
    )
    preview_payload = getattr(reopened, "payload", None)
    expected_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(preview_payload, Mapping)
        or preview_payload.get("authority_kind") != "ACCEPTANCE_EFFECT"
        or preview_payload.get("action_kind")
        != f"acceptance-effect.{OPERATOR_CONTROL_EFFECT_TYPE}"
        or str(preview_payload.get("action_scope") or "") != scope
        or int(preview_payload.get("risk_level") or 0) != 4
        or str(preview_payload.get("action_args_digest") or "").lower()
        != expected_digest
    ):
        raise ValueError("accepted Operator control Preview receipt drifted")
    return {**value, "control_args": control_args}


def _reopen_cancellation_authority(
    value: Mapping[str, Any],
    *,
    resolver: Any,
) -> dict[str, Any]:
    audit_ref = str(value.get("ingress_audit_ref") or "").strip()
    raw = resolver.resolve_committed_authority_by_audit(audit_ref=audit_ref)
    record = dict(raw) if isinstance(raw, Mapping) else {}
    action, path, scopes = operator_control_ingress_requirements(
        "task_graph.cancel"
    )
    body_digest = str(record.get("body_sha256") or "").strip().lower()
    expected_digest = str(value.get("input_sha256") or "").lower().removeprefix(
        "sha256:"
    )
    if (
        str(record.get("audit_ref") or "").strip() != audit_ref
        or str(record.get("claim_ref") or "").strip()
        != str(value.get("ingress_claim_ref") or "").strip()
        or str(record.get("request_id") or "").strip()
        != str(value.get("request_id") or "").strip()
        or str(record.get("principal_id") or "").strip()
        != str(value.get("principal_id") or "").strip()
        or body_digest != expected_digest
        or str(record.get("action") or "").strip() != action
        or str(record.get("normalized_path") or "").strip() != path
        or tuple(sorted(record.get("required_scopes") or ()))
        != tuple(sorted(scopes))
        or record.get("claim_result_ref") is not None
    ):
        raise ValueError("Operator cancellation Secure Ingress binding drifted")
    return {
        "allowed": True,
        "duplicate": False,
        "audit_ref": audit_ref,
        "claim_ref": str(record["claim_ref"]),
        "request_id": str(record["request_id"]),
        "principal_id": str(record["principal_id"]),
        "tenant_id": str(record["tenant_id"]),
        "body_sha256": body_digest,
        "action": action,
        "required_scopes": tuple(scopes),
    }


def _cancellation_matches(
    value: Mapping[str, Any],
    outcome: Any,
) -> bool:
    if not isinstance(outcome, Mapping):
        return False
    args = dict(value["control_args"])
    payload_raw = outcome.get("payload")
    payload = (
        dict(payload_raw)
        if isinstance(payload_raw, Mapping)
        else dict(outcome)
    )
    try:
        return all(
            (
                bool(str(outcome.get("intent_ref") or "").strip()),
                str(payload.get("request_id") or "")
                == str(value.get("request_id") or ""),
                str(payload.get("request_identity") or "")
                == str(value.get("ingress_audit_ref") or ""),
                str(payload.get("source") or "").upper() == "OPERATOR",
                str(payload.get("reason_code") or "").upper()
                == str(args.get("reason_code") or "").upper(),
                str(payload.get("graph_id") or "") == str(args.get("graph_id")),
                int(payload.get("graph_revision") or 0)
                == int(args.get("graph_revision") or 0),
            )
        )
    except (TypeError, ValueError):
        return False


def _review_matches(
    command_id: str,
    args: Mapping[str, Any],
    record: Any,
) -> bool:
    if not isinstance(record, Mapping):
        return False
    review_raw = record.get("review")
    review = dict(review_raw) if isinstance(review_raw, Mapping) else {}
    current_status = str(review.get("status") or "")
    if (
        str(review.get("review_id") or "") != str(args.get("review_id") or "")
        or int(record.get("status_version") or -1) < 1
    ):
        return False
    if command_id == "task_review.reject":
        return (
            current_status == "REJECTED"
            and review.get("decision_actor") == args.get("actor_id")
        )
    if command_id == "task_review.approve":
        return all(
            (
                current_status
                in {"APPROVED", "CONSUMED", "EXPIRED", "CANCELLED"},
                review.get("decision_actor") == args.get("actor_id"),
                review.get("subject_ref") == args.get("subject_ref"),
                review.get("subject_version") == args.get("subject_version"),
                review.get("resume_phase") == args.get("resume_phase"),
                review.get("checkpoint_id") == args.get("checkpoint_id"),
                str(review.get("token_ref") or "").startswith(
                    "review-token://"
                ),
            )
        )
    if current_status != "CONSUMED":
        return False
    public_ref = str(review.get("token_ref") or "")
    return all(
        (
            review.get("subject_ref") == args.get("subject_ref"),
            review.get("subject_version") == args.get("subject_version"),
            review.get("resume_phase") == args.get("resume_phase"),
            review.get("checkpoint_id") == args.get("checkpoint_id"),
            review.get("resume_result_ref") == args.get("resume_result_ref"),
            "sha256:" + hashlib.sha256(public_ref.encode("utf-8")).hexdigest()
            == args.get("resume_binding_sha256"),
        )
    )


def _expected_review_status(command_id: str) -> str:
    return {
        "task_review.approve": "APPROVED",
        "task_review.reject": "REJECTED",
        "task_review.resume": "CONSUMED",
    }[command_id]


def _review_outcome_status_version(
    command_id: str,
    record: Mapping[str, Any],
) -> int:
    if command_id == "task_review.approve":
        # Review creation is version 0 and approval is its only transition
        # into APPROVED.  Later CONSUMED/EXPIRED/CANCELLED transitions must
        # not change the historical approval receipt.
        return 1
    return int(record["status_version"])


__all__ = [
    "AcceptedOperatorControlHandler",
    "operator_control_handler_receipt_ref",
]
