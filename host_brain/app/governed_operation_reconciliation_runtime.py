"""签名 Operation reconciliation 的受治理 Operator 垂直切片。"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any

from host_brain.app.accepted_operation_reconciliation_handler import (
    OPERATION_RECONCILIATION_EFFECT_TYPE,
    operation_reconciliation_handler_receipt_ref,
)
from host_brain.contracts.governed_operator import (
    GOVERNED_OPERATOR_GOVERNOR_REJECTED,
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
    _require_operator_authority,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.task_runtime.errors import GovernedRuntimeError
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from natsume_contracts.task import HostCommand, HostCommandSurface


OPERATION_RECONCILIATION_TARGET_ID = "operator_task_operation_reconcile"
OPERATION_RECONCILIATION_ACTION_KIND = "operator_task_operation_reconciliation"
OPERATION_RECONCILIATION_INGRESS_ACTION = (
    "obs:command:task_operation.reconcile"
)
OPERATION_RECONCILIATION_INGRESS_PATH = (
    "/v1/host/obs-commands/task_operation.reconcile"
)
OPERATION_RECONCILIATION_INGRESS_REQUIRED_SCOPES = (
    "obs:write",
    "task-runtime:reconcile",
)
_SIGNATURE = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")


def operation_reconciliation_commit_event(
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    """将冻结签名字段编译为一个不含新 authority 的 effect proposal。

    该函数只验证原始 intent binding 并提议 acceptance effect；它不自行执行 COMMIT、
    RELEASE 或 REACQUIRE，实际副作用仍由后续受验收处理器与耐久证据链负责。
    """

    pack = dict(frozen.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    request = operation_reconciliation_request_from_args(args)
    if (
        str(intent.get("action") or "").strip()
        != OPERATION_RECONCILIATION_ACTION_KIND
        or str(intent.get("target") or "").strip().lower() != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != OPERATION_RECONCILIATION_TARGET_ID
        or str(args.get("operator_command_id") or "").strip()
        != "task_operation.reconcile"
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen Operation reconciliation intent binding drifted",
        )
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": "签名 Operation 对账已通过执行验收",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": "task_operation.reconcile",
            "proposed_effect_type": OPERATION_RECONCILIATION_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": OPERATION_RECONCILIATION_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": "task_operation.reconcile",
                    "request_id": frozen.request_id,
                    **request,
                },
            }
        ],
    }


class GovernedOperationReconciliationMixin:
    """混入 Operator owner 的签名 Operation command 行为。

    HTTP payload 仅是候选输入；Secure Ingress authority、耐久 evidence、Governor 决策和
    frozen TaskGraph 必须逐层一致，任一漂移都失败关闭而不把修复请求直接交给 TaskStore。
    """

    async def execute_operation_reconciliation(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """执行受治理 Operation reconciliation，并返回可审计的耐久结果。

        同 request_id 的已落库命令只允许精确重放；新请求须先 stage evidence、经 Governor
        准入，再进入 runtime stream。Review 尚未完成时只公开 WAITING_REVIEW，不声称
        历史副作用已经完成。
        """
        value = dict(payload or {})
        supplied_authority = _require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operation reconciliation request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = self._verify_operator_authority(
            supplied_authority,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id="task_operation.reconcile",
            ingress_action=OPERATION_RECONCILIATION_INGRESS_ACTION,
            ingress_path=OPERATION_RECONCILIATION_INGRESS_PATH,
            required_scopes=OPERATION_RECONCILIATION_INGRESS_REQUIRED_SCOPES,
        )
        principal_id = str(authority["principal_id"])
        signed_request = _normalize_request(
            value,
            reconciler_actor_id=principal_id,
        )
        if (
            str(value.get("principal_id") or "").strip() != principal_id
            or authority.get("operator_command_id")
            != "task_operation.reconcile"
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operation reconciliation differs from Secure Ingress authority",
            )
        evidence_raw = self._task_store.stage_operation_reconciliation_evidence(
            request_id=request_ref,
            ingress_audit_ref=str(authority["audit_ref"]),
            body_sha256=str(authority["body_sha256"]),
            **signed_request,
        )
        if not isinstance(evidence_raw, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operation reconciliation evidence intake returned no record",
            )
        request = _durable_request_from_evidence(evidence_raw)
        action = request["action"]
        message = (
            f"执行签名 Operation {action}：{request['reservation_id']}"
        )
        args = {
            "text": message,
            "route": "delegate",
            "target_id": OPERATION_RECONCILIATION_TARGET_ID,
            "operator_command_id": "task_operation.reconcile",
            **request,
        }
        intent_draft = {
            "route": "delegate",
            "target_id": OPERATION_RECONCILIATION_TARGET_ID,
            "domain": "task_runtime",
            "task": message,
            "action": OPERATION_RECONCILIATION_ACTION_KIND,
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
                != "task_operation.reconcile"
                or _request_from_work_order(work_order) != request
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable Operation reconciliation replay metadata drifted",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=build_operation_reconciliation_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_operation_reconciliation",
                    model_snapshot="server-owned-signed-reconciliation",
                    prompt_profile_id="operator-operation-reconciliation-v1",
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
                    or "Governor rejected Operation reconciliation"
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
            return self._pending_operation_reconciliation_review(
                request_id=request_ref,
                request=request,
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed Operation reconciliation emitted no terminal result",
            )
        receipt_raw = terminal.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        outcome = require_durable_operation_reconciliation_outcome(
            task_store=self._task_store,
            execution_receipt=receipt,
            request=request,
        )
        return {
            "success": receipt.get("accepted") is True,
            "side_effects_executed": outcome["exact_replay"] is False,
            "historical_side_effects_executed": True,
            "exact_replay": outcome["exact_replay"],
            "status": "RECONCILED",
            "reservation_id": request["reservation_id"],
            "action": request["action"],
            "recovery_case_ref": request.get("recovery_case_ref"),
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "execution_receipt": receipt,
            "acceptance_effect_receipt": outcome,
        }

    def _pending_operation_reconciliation_review(
        self,
        *,
        request_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """将 ReviewRequired 映射为唯一 pending ReviewItem，避免客户端误判为最终成功。"""
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operation reconciliation Review admission is unavailable",
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
            if str(review.get("status") or "").upper() in {"PENDING", "APPROVED"}:
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
                "Operation reconciliation must expose one pending ReviewItem",
            )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "reservation_id": request["reservation_id"],
            "action": request["action"],
            "recovery_case_ref": request.get("recovery_case_ref"),
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }


def build_operation_reconciliation_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """构造可重复计算的 Governor intent；request ID 摘要稳定绑定 intent 与 nonce。"""
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-operation-reconcile-{digest[:32]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": OPERATION_RECONCILIATION_ACTION_KIND,
        "target": "task_runtime",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-operation-reconcile\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": OPERATION_RECONCILIATION_TARGET_ID,
    }


def require_durable_operation_reconciliation_outcome(
    *,
    task_store: Any,
    execution_receipt: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """验证 execution receipt 对应的一条已提交 acceptance effect 与耐久修复结果。

    该检查交叉比对 evidence、effect、reservation、proof 和可选终态投影；任一字段不符
    都意味着回执无法证明所声称的历史操作，不能向调用者报告 RECONCILED。
    """
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
            "Operation reconciliation execution lineage is invalid",
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
        == OPERATION_RECONCILIATION_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation must bind one Acceptance Effect",
        )
    effect = effects[0]
    effect_payload_raw = effect.get("payload")
    effect_payload = (
        dict(effect_payload_raw)
        if isinstance(effect_payload_raw, Mapping)
        else {}
    )
    durable_request = operation_reconciliation_request_from_args(request)
    evidence_raw = task_store.get_operation_reconciliation_evidence(
        durable_request["evidence_ref"]
    )
    evidence = (
        dict(evidence_raw) if isinstance(evidence_raw, Mapping) else {}
    )
    if _durable_request_from_evidence(evidence) != durable_request:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation evidence reference drifted",
        )
    expected_payload = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": str(effect_payload.get("request_id") or "").strip(),
        **durable_request,
    }
    comparable_payload = {
        key: effect_payload.get(key) for key in expected_payload
    }
    operation = task_store.get_operation_reservation(
        evidence["reservation_id"]
    )
    operation_record = (
        dict(operation) if isinstance(operation, Mapping) else {}
    )
    expected_status = {
        "COMMIT": "COMMITTED",
        "RELEASE": "RELEASED",
        "REACQUIRE": "COMMITTED",
    }[evidence["action"]]
    expected_reconciliation_outcome = (
        "SIDE_EFFECT_CONFIRMED"
        if evidence["action"] == "COMMIT"
        else "NO_SIDE_EFFECT"
    )
    terminal_failure = (
        evidence["action"] == "RELEASE"
        and not str(evidence.get("intent_ref") or "").strip()
    )
    terminal = (
        task_store.resolve_reconciled_operation_failure(
            evidence["reservation_id"]
        )
        if terminal_failure
        else None
    )
    terminal_projection = (
        {
            "authority_kind": terminal.get("authority_kind"),
            "graph_status": terminal.get("graph", {}).get("status"),
            "node_status": terminal.get("node", {}).get("status"),
            "attempt_status": terminal.get("attempt", {}).get("status"),
            "budget_status": terminal.get("budget", {}).get("status"),
            "lease_status": terminal.get("lease", {}).get("status"),
            "failure_reason_code": terminal.get("failure_reason_code"),
            "error_ref": terminal.get("error_ref"),
            "settlement_scope": terminal.get("settlement_scope"),
            "graph_settlement_status": terminal.get(
                "graph_settlement_status"
            ),
            "parallel_settlement_ref": terminal.get(
                "parallel_settlement_ref"
            ),
        }
        if isinstance(terminal, Mapping)
        else None
    )
    expected_receipt_ref = operation_reconciliation_handler_receipt_ref(
        request=evidence,
        operation=operation_record,
        terminal=terminal_projection,
    )
    resolved_recovery_case = _require_resolved_recovery_case(
        task_store=task_store,
        evidence=evidence,
    )
    if (
        not expected_payload["request_id"]
        or comparable_payload != expected_payload
        or str(effect.get("status") or "").upper() != "COMMITTED"
        or str(effect.get("attempt_id") or "") != attempt_id
        or str(effect.get("receipt_ref") or "") != expected_receipt_ref
        or str(operation_record.get("status") or "").upper() != expected_status
        or str(operation_record.get("reconciliation_action") or "").upper()
        != evidence["action"]
        or str(
            operation_record.get("reconciliation_outcome") or ""
        ).upper()
        != expected_reconciliation_outcome
        or (
            evidence["action"] == "COMMIT"
            and str(operation_record.get("receipt_ref") or "")
            != str(evidence.get("downstream_receipt_ref") or "")
        )
        or str(operation_record.get("reconciler_actor_id") or "")
        != evidence["reconciler_actor_id"]
        or operation_record.get("reconciliation_recovery_version")
        != evidence["expected_version"]
        or str(operation_record.get("reconciliation_proof_ref") or "")
        != evidence["attestation_ref"]
        or str(operation_record.get("reconciliation_proof_key_id") or "")
        != evidence["attestation_key_id"]
        or str(operation_record.get("reconciliation_proof_signature") or "")
        != evidence["attestation_signature"]
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation durable outcome binding drifted",
        )
    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "handler_receipt_ref": expected_receipt_ref,
        "reservation_id": evidence["reservation_id"],
        "operation_status": expected_status,
        "target_terminal": terminal_projection,
        "recovery_case": resolved_recovery_case,
        "exact_replay": receipt.get("replayed") is True,
    }


def _request_from_work_order(work_order: WorkOrderV3) -> dict[str, Any]:
    """从冻结 WorkOrder input pack 重建规范请求，用于重放元数据一致性校验。"""
    pack = dict(work_order.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    return operation_reconciliation_request_from_args(args)


def operation_reconciliation_request_from_args(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """规范并严格校验 evidence、reservation、版本、动作和可选 recovery case 绑定。"""
    expected_version = value.get("expected_version")
    request = {
        "evidence_ref": str(value.get("evidence_ref") or "").strip(),
        "evidence_digest": str(
            value.get("evidence_digest") or ""
        ).strip().lower(),
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
    digest = request["evidence_digest"]
    if digest.startswith("sha256:"):
        digest = digest.removeprefix("sha256:")
    if (
        not request["evidence_ref"].startswith(
            "operation-reconcile-evidence://sha256/"
        )
        or len(request["evidence_ref"].rsplit("/", 1)[-1]) != 64
        or any(
            char not in "0123456789abcdef"
            for char in request["evidence_ref"].rsplit("/", 1)[-1]
        )
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or request["evidence_ref"].rsplit("/", 1)[-1] != digest
        or not request["reservation_id"]
        or type(expected_version) is not int
        or expected_version < 0
        or request["action"] not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not request["reconciler_actor_id"]
        or ((recovery_case_ref is None) != (recovery_case_digest is None))
        or (
            recovery_case_ref is not None
            and not re.fullmatch(
                r"operation-recovery-hold://sha256/[0-9a-f]{64}",
                recovery_case_ref,
            )
        )
        or (
            recovery_case_digest is not None
            and recovery_case_digest
            != "sha256:" + recovery_case_ref.rsplit("/", 1)[-1]
        )
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation evidence binding is invalid",
        )
    request["evidence_digest"] = f"sha256:{digest}"
    if recovery_case_ref is not None:
        request["recovery_case_ref"] = recovery_case_ref
        request["recovery_case_subject_sha256"] = recovery_case_digest
    return request


def _durable_request_from_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    return operation_reconciliation_request_from_args(
        {
            key: value.get(key)
            for key in (
                "evidence_ref",
                "evidence_digest",
                "reservation_id",
                "expected_version",
                "action",
                "reconciler_actor_id",
                "recovery_case_ref",
                "recovery_case_subject_sha256",
            )
        }
    )


def _normalize_request(
    value: Mapping[str, Any],
    *,
    reconciler_actor_id: str,
) -> dict[str, Any]:
    action = str(value.get("action") or "").strip().upper()
    expected_version = value.get("expected_version")
    request = {
        "intent_ref": str(value.get("intent_ref") or "").strip() or None,
        "reservation_id": str(value.get("reservation_id") or "").strip(),
        "expected_version": expected_version,
        "action": action,
        "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
        "downstream_receipt_ref": (
            str(value.get("downstream_receipt_ref") or "").strip() or None
        ),
        "attestation_ref": str(value.get("attestation_ref") or "").strip(),
        "attestation_key_id": str(
            value.get("attestation_key_id") or ""
        ).strip(),
        "attestation_signature": str(
            value.get("attestation_signature") or ""
        ).strip(),
        "recovery_case_ref": (
            str(value.get("recovery_case_ref") or "").strip() or None
        ),
        "recovery_case_subject_sha256": (
            str(value.get("recovery_case_subject_sha256") or "")
            .strip()
            .lower()
            or None
        ),
    }
    if (
        not request["reservation_id"]
        or type(expected_version) is not int
        or expected_version < 0
        or action not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not request["reconciler_actor_id"]
        or not request["attestation_ref"]
        or not request["attestation_key_id"]
        or not _SIGNATURE.fullmatch(request["attestation_signature"])
        or (action == "REACQUIRE" and request["intent_ref"] is not None)
        or (
            action == "REACQUIRE"
            and request["downstream_receipt_ref"] is not None
        )
        or (action == "COMMIT" and request["downstream_receipt_ref"] is None)
        or (
            action == "COMMIT"
            and request["intent_ref"] is None
            and not str(request["downstream_receipt_ref"]).startswith(
                "worker-projection://"
            )
        )
        or (
            action == "RELEASE"
            and request["downstream_receipt_ref"] is not None
        )
        or (
            (request["recovery_case_ref"] is None)
            != (request["recovery_case_subject_sha256"] is None)
        )
        or (
            request["recovery_case_ref"] is not None
            and not re.fullmatch(
                r"operation-recovery-hold://sha256/[0-9a-f]{64}",
                request["recovery_case_ref"],
            )
        )
        or (
            request["recovery_case_subject_sha256"] is not None
            and request["recovery_case_subject_sha256"]
            != "sha256:"
            + str(request["recovery_case_ref"]).rsplit("/", 1)[-1]
        )
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation signed fields or action matrix is invalid",
        )
    return request


def _require_resolved_recovery_case(
    *,
    task_store: Any,
    evidence: Mapping[str, Any],
) -> dict[str, Any] | None:
    case_ref = str(evidence.get("recovery_case_ref") or "").strip()
    if not case_ref:
        return None
    raw = task_store.get_operation_recovery_hold(case_ref)
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
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operation reconciliation did not resolve its exact recovery case",
        )
    return {
        "case_ref": case_ref,
        "case_kind": str(case.get("case_kind") or ""),
        "classification": str(case.get("classification") or ""),
        "subject_sha256": str(case.get("subject_sha256") or ""),
        "current": False,
    }


__all__ = [
    "GovernedOperationReconciliationMixin",
    "OPERATION_RECONCILIATION_ACTION_KIND",
    "OPERATION_RECONCILIATION_TARGET_ID",
    "operation_reconciliation_request_from_args",
    "operation_reconciliation_commit_event",
    "require_durable_operation_reconciliation_outcome",
]
