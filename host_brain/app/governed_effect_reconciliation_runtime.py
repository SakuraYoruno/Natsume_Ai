"""Governed Operator vertical slice for signed Acceptance Effect recovery."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any

from host_brain.app.accepted_effect_reconciliation_handler import (
    EFFECT_RECONCILIATION_EFFECT_TYPE,
    effect_reconciliation_handler_receipt_ref,
    effect_reconciliation_result_projection,
)
from host_brain.app.governed_operator_contracts import (
    GOVERNED_OPERATOR_GOVERNOR_REJECTED,
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.task_runtime import GovernedRuntimeError
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from natsume_contracts.task import HostCommand, HostCommandSurface


EFFECT_RECONCILIATION_TARGET_ID = "operator_task_effect_reconcile"
EFFECT_RECONCILIATION_ACTION_KIND = "operator_task_effect_reconciliation"
EFFECT_RECONCILIATION_INGRESS_ACTION = "obs:command:task_effect.reconcile"
EFFECT_RECONCILIATION_INGRESS_PATH = (
    "/v1/host/obs-commands/task_effect.reconcile"
)
EFFECT_RECONCILIATION_INGRESS_REQUIRED_SCOPES = (
    "obs:write",
    "task-runtime:reconcile",
)

_GENERAL_PURPOSE = "GENERAL_UNKNOWN_EFFECT"
_CANCELLATION_PURPOSES = frozenset(
    {"PENDING_CANCELLATION", "LEGACY_FINALIZED_CORRECTION"}
)
_DURABLE_REFERENCE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")


def effect_reconciliation_commit_event(
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    """Compile frozen signed fields into one non-executable Effect intent."""

    pack = dict(frozen.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    request = effect_reconciliation_request_from_args(args)
    if (
        str(intent.get("action") or "").strip()
        != EFFECT_RECONCILIATION_ACTION_KIND
        or str(intent.get("target") or "").strip().lower()
        != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != EFFECT_RECONCILIATION_TARGET_ID
        or str(args.get("operator_command_id") or "").strip()
        != "task_effect.reconcile"
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen Acceptance Effect reconciliation intent drifted",
        )
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": "签名 Acceptance Effect 对账已通过执行验收",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": "task_effect.reconcile",
            "proposed_effect_type": EFFECT_RECONCILIATION_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": EFFECT_RECONCILIATION_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": "task_effect.reconcile",
                    "request_id": frozen.request_id,
                    **request,
                },
            }
        ],
    }


class GovernedEffectReconciliationMixin:
    """Signed Effect command behavior mixed into the Operator owner."""

    async def execute_effect_reconciliation(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        value = dict(payload or {})
        supplied_authority = self._require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Acceptance Effect reconciliation request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = self._verify_operator_authority(
            supplied_authority,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id="task_effect.reconcile",
            ingress_action=EFFECT_RECONCILIATION_INGRESS_ACTION,
            ingress_path=EFFECT_RECONCILIATION_INGRESS_PATH,
            required_scopes=EFFECT_RECONCILIATION_INGRESS_REQUIRED_SCOPES,
        )
        principal_id = str(authority["principal_id"])
        signed_request = normalize_effect_reconciliation_request(
            value,
            reconciler_actor_id=principal_id,
        )
        if (
            str(value.get("principal_id") or "").strip() != principal_id
            or authority.get("operator_command_id")
            != "task_effect.reconcile"
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Effect reconciliation differs from Secure Ingress authority",
            )
        evidence_raw = (
            self._task_store.stage_acceptance_effect_reconciliation_evidence(
                request_id=request_ref,
                ingress_audit_ref=str(authority["audit_ref"]),
                body_sha256=str(authority["body_sha256"]),
                **signed_request,
            )
        )
        if not isinstance(evidence_raw, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Acceptance Effect reconciliation evidence intake returned "
                "no record",
            )
        request = _durable_request_from_evidence(evidence_raw)
        message = (
            "执行签名 Acceptance Effect 对账："
            f"{request['effect_id']} / {request['outcome']}"
        )
        args = {
            "text": message,
            "route": "delegate",
            "target_id": EFFECT_RECONCILIATION_TARGET_ID,
            "operator_command_id": "task_effect.reconcile",
            **request,
        }
        intent_draft = {
            "route": "delegate",
            "target_id": EFFECT_RECONCILIATION_TARGET_ID,
            "domain": "task_runtime",
            "task": message,
            "action": EFFECT_RECONCILIATION_ACTION_KIND,
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
                    dict(work_order.metadata or {}).get(
                        "operator_command_id"
                    )
                    or ""
                ).strip()
                != "task_effect.reconcile"
                or effect_reconciliation_request_from_work_order(work_order)
                != request
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable Effect reconciliation replay metadata drifted",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=build_effect_reconciliation_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_effect_reconciliation",
                    model_snapshot="server-owned-signed-reconciliation",
                    prompt_profile_id=(
                        "operator-effect-reconciliation-v1"
                    ),
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
                    or "Governor rejected Effect reconciliation"
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
            return self._pending_effect_reconciliation_review(
                request_id=request_ref,
                request=request,
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed Effect reconciliation emitted no terminal result",
            )
        receipt_raw = terminal.get("execution_receipt")
        receipt = (
            dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        )
        outcome = require_durable_effect_reconciliation_outcome(
            task_store=self._task_store,
            execution_receipt=receipt,
            request=request,
        )
        return {
            "success": receipt.get("accepted") is True,
            # The immutable proof confirms historical state.  It cannot prove
            # that this particular HTTP attempt created the inner proof.
            "side_effects_executed": False,
            "historical_side_effects_executed": True,
            "exact_replay": receipt.get("replayed") is True,
            "status": "RECONCILED",
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "purpose": request["purpose"],
            "outcome": request["outcome"],
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "execution_receipt": receipt,
            "acceptance_effect_receipt": outcome,
        }

    def _pending_effect_reconciliation_review(
        self,
        *,
        request_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = (
            durable.get("command") if isinstance(durable, Mapping) else None
        )
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Effect reconciliation Review admission is unavailable",
            )
        rows = self._review_store.list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        reviews = []
        for raw in tuple(rows or ()):
            record = dict(raw or {}) if isinstance(raw, Mapping) else {}
            review_raw = record.get("review")
            review = (
                dict(review_raw) if isinstance(review_raw, Mapping) else {}
            )
            if str(review.get("status") or "").upper() in {
                "PENDING",
                "APPROVED",
            }:
                reviews.append(
                    {
                        key: review.get(key)
                        for key in (
                            "review_id",
                            "status",
                            "subject_ref",
                            "subject_version",
                            "resume_phase",
                            "checkpoint_id",
                        )
                    }
                )
        if len(reviews) != 1:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Effect reconciliation must expose one pending ReviewItem",
            )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }


def build_effect_reconciliation_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-effect-reconcile-{digest[:32]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": EFFECT_RECONCILIATION_ACTION_KIND,
        "target": "task_runtime",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-effect-reconcile\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": EFFECT_RECONCILIATION_TARGET_ID,
    }


def require_durable_effect_reconciliation_outcome(
    *,
    task_store: Any,
    execution_receipt: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    graph_revision = receipt.get("graph_revision")
    if (
        not graph_id
        or type(graph_revision) is not int
        or graph_revision < 1
        or not node_id
        or not attempt_id
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Effect reconciliation execution lineage is invalid",
        )
    rows = tuple(
        task_store.list_acceptance_effect_intents(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            limit=10,
        )
        or ()
    )
    effects = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "")
        == EFFECT_RECONCILIATION_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Effect reconciliation must bind one Acceptance Effect",
        )
    outer = effects[0]
    payload_raw = outer.get("payload")
    payload = (
        dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    )
    durable_request = effect_reconciliation_request_from_args(request)
    evidence_raw = (
        task_store.get_acceptance_effect_reconciliation_evidence(
            durable_request["evidence_ref"]
        )
    )
    evidence = (
        dict(evidence_raw) if isinstance(evidence_raw, Mapping) else {}
    )
    if (
        _durable_request_from_evidence(evidence) != durable_request
        or str(evidence.get("request_id") or "").strip()
        != str(payload.get("request_id") or "").strip()
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Effect reconciliation evidence reference drifted",
        )
    expected_payload = {
        "operator_command_id": "task_effect.reconcile",
        "request_id": str(payload.get("request_id") or "").strip(),
        **durable_request,
    }
    comparable_payload = {
        key: payload.get(key) for key in expected_payload
    }
    if not expected_payload["request_id"] or comparable_payload != expected_payload:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Effect reconciliation Acceptance payload drifted",
        )
    proof_result = _reopen_effect_reconciliation_result(
        task_store,
        request=evidence,
    )
    expected_receipt_ref = effect_reconciliation_handler_receipt_ref(
        request=evidence,
        result=proof_result,
    )
    if (
        str(outer.get("status") or "").upper() != "COMMITTED"
        or str(outer.get("attempt_id") or "") != attempt_id
        or str(outer.get("receipt_ref") or "") != expected_receipt_ref
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Effect reconciliation durable outcome binding drifted",
        )
    projection = effect_reconciliation_result_projection(proof_result)
    return {
        "committed": True,
        "outer_effect_id": str(outer.get("effect_id") or ""),
        "handler_receipt_ref": expected_receipt_ref,
        "effect_id": durable_request["effect_id"],
        "case_id": durable_request["case_id"],
        "purpose": durable_request["purpose"],
        "outcome": durable_request["outcome"],
        "proof": projection,
        "exact_replay": receipt.get("replayed") is True,
    }


def _reopen_effect_reconciliation_result(
    task_store: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if request["purpose"] == _GENERAL_PURPOSE:
        raw = task_store.read_acceptance_effect_reconciliation(
            request["effect_id"],
            case_id=request["case_id"],
        )
        subject = dict(raw or {}) if isinstance(raw, Mapping) else {}
        proof_raw = subject.get("proof")
        proof = (
            dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
        )
        return {
            "status": "PROOF_APPLIED",
            "purpose": request["purpose"],
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "outcome": proof.get("outcome"),
            "target_effect_status": proof.get("target_effect_status"),
            "receipt_ref": proof.get("receipt_ref"),
            "executor_quiescence_ref": proof.get(
                "executor_quiescence_ref"
            ),
            "proof_id": proof.get("proof_id"),
            "outbox_id": proof.get("outbox_id"),
            "proof": proof,
        }
    raw = task_store.read_graph_cancellation_effect_reconciliation(
        request["intent_ref"],
        request["effect_id"],
    )
    subject = dict(raw or {}) if isinstance(raw, Mapping) else {}
    proof_raw = subject.get("proof")
    proof = dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
    case_raw = subject.get("case")
    effect_raw = subject.get("effect")
    return {
        "status": (
            "PROOF_RECORDED"
            if request["purpose"] == "PENDING_CANCELLATION"
            else "CORRECTION_PROOF_RECORDED"
        ),
        "case": dict(case_raw) if isinstance(case_raw, Mapping) else {},
        "effect": (
            dict(effect_raw) if isinstance(effect_raw, Mapping) else {}
        ),
        "proof": proof,
    }


def effect_reconciliation_request_from_work_order(
    work_order: WorkOrderV3,
) -> dict[str, Any]:
    pack = dict(work_order.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    return effect_reconciliation_request_from_args(args)


def effect_reconciliation_request_from_args(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_ref = str(value.get("evidence_ref") or "").strip()
    evidence_digest = str(
        value.get("evidence_digest") or ""
    ).strip().lower()
    digest_suffix = evidence_digest.removeprefix("sha256:")
    request = {
        "evidence_ref": evidence_ref,
        "evidence_digest": "sha256:" + digest_suffix,
        "effect_id": str(value.get("effect_id") or "").strip(),
        "case_id": str(value.get("case_id") or "").strip(),
        "purpose": str(value.get("purpose") or "").strip().upper(),
        "outcome": str(value.get("outcome") or "").strip().upper(),
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
    }
    if (
        not evidence_ref.startswith(
            "effect-reconcile-evidence://sha256/"
        )
        or len(digest_suffix) != 64
        or any(
            character not in "0123456789abcdef"
            for character in digest_suffix
        )
        or evidence_ref.rsplit("/", 1)[-1] != digest_suffix
        or _DURABLE_REFERENCE.fullmatch(request["effect_id"]) is None
        or _DURABLE_REFERENCE.fullmatch(request["case_id"]) is None
        or request["purpose"] != _GENERAL_PURPOSE
        or request["outcome"]
        not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}
        or not request["reconciler_actor_id"]
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Acceptance Effect reconciliation evidence binding is invalid",
        )
    return request


def _durable_request_from_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    return effect_reconciliation_request_from_args(
        {
            key: value.get(key)
            for key in (
                "evidence_ref",
                "evidence_digest",
                "effect_id",
                "case_id",
                "purpose",
                "outcome",
                "reconciler_actor_id",
            )
        }
    )


def normalize_effect_reconciliation_request(
    value: Mapping[str, Any],
    *,
    reconciler_actor_id: str,
) -> dict[str, Any]:
    purpose = str(value.get("purpose") or "").strip().upper()
    outcome = str(value.get("outcome") or "").strip().upper()
    intent_ref = str(value.get("intent_ref") or "").strip() or None
    receipt = (
        str(value.get("downstream_receipt_ref") or "").strip() or None
    )
    quiescence = (
        str(value.get("executor_quiescence_ref") or "").strip() or None
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": str(value.get("effect_id") or "").strip(),
        "case_id": str(value.get("case_id") or "").strip(),
        "expected_case_snapshot_sha256": str(
            value.get("expected_case_snapshot_sha256") or ""
        ).strip().lower(),
        "expected_subject_snapshot_sha256": str(
            value.get("expected_subject_snapshot_sha256") or ""
        ).strip().lower(),
        "purpose": purpose,
        "outcome": outcome,
        "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
        "issued_ts_ms": value.get("issued_ts_ms"),
        "downstream_receipt_ref": receipt,
        "executor_quiescence_ref": quiescence,
        "attestation_ref": str(
            value.get("attestation_ref") or ""
        ).strip(),
        "attestation_key_id": str(
            value.get("attestation_key_id") or ""
        ).strip(),
        "attestation_signature": str(
            value.get("attestation_signature") or ""
        ).strip(),
    }
    durable_values = [
        request["effect_id"],
        request["case_id"],
        request["attestation_ref"],
    ]
    if intent_ref is not None:
        durable_values.append(intent_ref)
    if receipt is not None:
        durable_values.append(receipt)
    if quiescence is not None:
        durable_values.append(quiescence)
    general = purpose == _GENERAL_PURPOSE
    if (
        (not general and purpose not in _CANCELLATION_PURPOSES)
        or any(
            _DURABLE_REFERENCE.fullmatch(str(item)) is None
            for item in durable_values
        )
        or _SHA256.fullmatch(
            request["expected_case_snapshot_sha256"]
        )
        is None
        or _SHA256.fullmatch(
            request["expected_subject_snapshot_sha256"]
        )
        is None
        or not request["reconciler_actor_id"]
        or not request["attestation_key_id"]
        or _SIGNATURE.fullmatch(request["attestation_signature"]) is None
        or type(request["issued_ts_ms"]) is not int
        or request["issued_ts_ms"] < 0
        or (general and intent_ref is not None)
        or (not general and intent_ref is None)
        or outcome not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}
        or (outcome == "NO_SIDE_EFFECT" and receipt is not None)
        or (outcome == "SIDE_EFFECT_CONFIRMED" and receipt is None)
        or (general and outcome == "NO_SIDE_EFFECT" and quiescence is None)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Acceptance Effect reconciliation signed fields are invalid",
        )
    return {
        key: item
        for key, item in request.items()
        if item is not None
    }


__all__ = [
    "EFFECT_RECONCILIATION_ACTION_KIND",
    "EFFECT_RECONCILIATION_TARGET_ID",
    "GovernedEffectReconciliationMixin",
    "effect_reconciliation_commit_event",
    "effect_reconciliation_request_from_args",
    "normalize_effect_reconciliation_request",
    "require_durable_effect_reconciliation_outcome",
]
