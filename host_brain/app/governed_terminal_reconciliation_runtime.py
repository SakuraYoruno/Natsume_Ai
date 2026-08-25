"""签名 terminal reconciliation 的受治理 Operator 垂直切片。

本模块拥有命令校验与耐久 outcome 重开；真正的终态变更仍是 Acceptance Effect，因而
不能在 frozen worker 内执行。
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any

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


TERMINAL_TARGET_ID = "operator_task_terminal_reconcile"
TERMINAL_EFFECT_TYPE = "task.terminal_reconciliation"
TERMINAL_ACTION_KIND = "operator_task_terminal_reconciliation"
TERMINAL_INGRESS_ACTION = "obs:command:task_terminal.reconcile"
TERMINAL_INGRESS_PATH = "/v1/host/obs-commands/task_terminal.reconcile"
TERMINAL_INGRESS_REQUIRED_SCOPES = ("obs:write", "task-runtime:reconcile")


def terminal_reconciliation_commit_event(
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    """将冻结请求编译为一个不携带新 authority 的 Acceptance Effect。

    worker 仅提议绑定 signed command 的终态修复 effect；最终 mutation 由 handler 依据
    case/proof 重新验证，避免模型或执行节点自行改写 graph、budget 等历史终态。
    """

    pack = dict(frozen.input_pack or {})
    intent_raw = pack.get("intent_draft")
    intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
    args_raw = intent.get("args")
    args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
    required = ("case_id", "signed_command_ref", "signed_command_sha256")
    if (
        str(intent.get("action") or "").strip() != TERMINAL_ACTION_KIND
        or str(intent.get("target") or "").strip() != "system"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != TERMINAL_TARGET_ID
        or str(args.get("operator_command_id") or "").strip()
        != "task_terminal.reconcile"
        or any(not str(args.get(field) or "").strip() for field in required)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "frozen terminal reconciliation intent binding drifted",
        )
    payload = {key: args.get(key) for key in ("operator_command_id", *required)}
    payload["request_id"] = frozen.request_id
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": frozen.request_id,
        "narrative": "终态签名修复已通过执行验收",
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": {
            "operator_command_id": "task_terminal.reconcile",
            "proposed_effect_type": TERMINAL_EFFECT_TYPE,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": TERMINAL_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": payload,
            }
        ],
    }


class GovernedTerminalReconciliationMixin:
    """混入应用局部 Operator owner 的 terminal command 行为。

    终态修复会影响并行任务的收敛解释，故必须让 Secure Ingress、暂存签名 bundle、
    Governor、Review 和 acceptance effect 全部对同一 case/快照达成一致。
    """

    async def execute_terminal_reconciliation(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """执行签名终态修复，并只返回可由耐久 proof 证明的结果。

        重放复用原 command 的 grant；新请求先暂存签名字段再进入 runtime。若要求 Review，
        仅暴露 WAITING_REVIEW，不能在没有 committed effect 前宣称并行终态已收敛。
        """
        value = dict(payload or {})
        supplied_authority = _require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "terminal reconciliation request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = self._verify_operator_authority(
            supplied_authority,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id="task_terminal.reconcile",
            ingress_action=TERMINAL_INGRESS_ACTION,
            ingress_path=TERMINAL_INGRESS_PATH,
            required_scopes=TERMINAL_INGRESS_REQUIRED_SCOPES,
        )
        principal_id = str(authority["principal_id"])
        signed_fields = (
            "case_id",
            "expected_case_snapshot_sha256",
            "expected_subject_snapshot_sha256",
            "action",
            "attestation_ref",
            "attestation_key_id",
            "attestation_signature",
        )
        signed_command = {
            field: str(value.get(field) or "").strip() for field in signed_fields
        }
        signed_command.update(
            {
                "operator_command_id": "task_terminal.reconcile",
                "target_graph_status": str(
                    value.get("target_graph_status") or ""
                ).strip().upper()
                or None,
                "target_budget_status": str(
                    value.get("target_budget_status") or ""
                ).strip().upper()
                or None,
                "reconciler_actor_id": principal_id,
            }
        )
        try:
            signed_command["issued_ts_ms"] = int(value.get("issued_ts_ms"))
        except (TypeError, ValueError) as exc:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "terminal reconciliation issued_ts_ms is invalid",
            ) from exc
        if (
            any(not signed_command[field] for field in signed_fields)
            or signed_command["issued_ts_ms"] < 0
            or str(value.get("principal_id") or "").strip() != principal_id
            or authority.get("operator_command_id") != "task_terminal.reconcile"
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "terminal reconciliation differs from Secure Ingress authority",
            )
        received_at_ms = now_ms()
        staged = self._terminal_operator_command_store.stage(
            request_id=request_ref,
            principal_id=principal_id,
            command=signed_command,
            ingress_claim_ref=str(authority["ingress_claim_ref"]),
            ingress_audit_ref=str(authority["ingress_audit_ref"]),
            ingress_body_sha256=str(authority["body_sha256"]),
            staged_at_ms=received_at_ms,
        )
        message = f"执行签名终态修复：{signed_command['case_id']}"
        args = {
            "operator_command_id": "task_terminal.reconcile",
            "case_id": signed_command["case_id"],
            "signed_command_ref": staged["bundle_ref"],
            "signed_command_sha256": staged["command_sha256"],
        }
        args.update(
            {
                "text": message,
                "route": "delegate",
                "target_id": TERMINAL_TARGET_ID,
            }
        )
        intent_draft = {
            "route": "delegate",
            "target_id": TERMINAL_TARGET_ID,
            "domain": "task_runtime",
            "task": message,
            "action": TERMINAL_ACTION_KIND,
            "target": "system",
            "args": args,
        }
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get("operator_command_id") or ""
                ).strip()
                != "task_terminal.reconcile"
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable terminal reconciliation replay metadata drifted",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=build_terminal_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_terminal_reconciliation",
                    model_snapshot="server-owned-signed-reconciliation",
                    prompt_profile_id="operator-terminal-reconciliation-v1",
                )
                or {}
            )
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(decision.get("detail") or "Governor rejected terminal repair"),
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
            return self._pending_terminal_review_result(
                request_id=request_ref,
                case_id=args["case_id"],
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed terminal reconciliation emitted no terminal result",
            )
        receipt_raw = terminal.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        outcome = require_durable_terminal_outcome(
            task_store=self._task_store,
            command_store=self._terminal_operator_command_store,
            execution_receipt=receipt,
            args=args,
        )
        return {
            "success": receipt.get("accepted") is True,
            "side_effects_executed": outcome["exact_replay"] is False,
            "historical_side_effects_executed": True,
            "exact_replay": outcome["exact_replay"],
            "status": "RECONCILED",
            "case_id": args["case_id"],
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "execution_receipt": receipt,
            "acceptance_effect_receipt": outcome,
        }

    def _pending_terminal_review_result(
        self,
        *,
        request_id: str,
        case_id: str,
    ) -> dict[str, Any]:
        """将 ReviewRequired 映射为唯一 pending ReviewItem，保留 case 与 graph 定位。"""
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "terminal reconciliation Review admission is unavailable",
            )
        rows = self._review_store.list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        reviews = []
        for raw in tuple(rows or ()):
            record = dict(raw or {}) if isinstance(raw, Mapping) else {}
            review_raw = record.get("review")
            review = dict(review_raw) if isinstance(review_raw, Mapping) else {}
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
                "terminal reconciliation must expose one pending ReviewItem",
            )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "case_id": case_id,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }


def build_terminal_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """从 request ID 生成稳定 Governor intent 与 nonce，保证受治理重放身份固定。"""
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-terminal-{digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": TERMINAL_ACTION_KIND,
        "target": "system",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-terminal\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": TERMINAL_TARGET_ID,
    }


def require_durable_terminal_outcome(
    *,
    task_store: Any,
    command_store: Any,
    execution_receipt: Mapping[str, Any],
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """验证终态回执绑定的一条 committed effect、签名 bundle 与 case proof。

    effect payload、attempt、proof authority 与每个签名快照字段都需匹配；否则无法区分
    正确恢复与对另一历史 case 的误归属，必须失败关闭。
    """
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    try:
        graph_revision = int(receipt.get("graph_revision") or 0)
    except (TypeError, ValueError) as exc:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "terminal reconciliation execution lineage is invalid",
        ) from exc
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
        and str(row.get("effect_type") or "") == TERMINAL_EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "terminal reconciliation must bind one Acceptance Effect",
        )
    effect = effects[0]
    effect_payload_raw = effect.get("payload")
    effect_payload = (
        dict(effect_payload_raw)
        if isinstance(effect_payload_raw, Mapping)
        else {}
    )
    expected_effect_payload = {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": str(args.get("case_id") or "").strip(),
        "signed_command_ref": str(args.get("signed_command_ref") or "").strip(),
        "signed_command_sha256": str(
            args.get("signed_command_sha256") or ""
        ).strip(),
    }
    request_id = str(effect_payload.get("request_id") or "").strip()
    if (
        not request_id
        or {key: effect_payload.get(key) for key in expected_effect_payload}
        != expected_effect_payload
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "terminal reconciliation Acceptance Effect binding drifted",
        )
    signed = _reopen_signed_command(
        command_store=command_store,
        request_id=request_id,
        args=args,
    )
    aggregate = task_store.read_terminal_reconciliation_case(signed["case_id"])
    proof_raw = aggregate.get("proof") if isinstance(aggregate, Mapping) else None
    proof = dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
    expected = {
        "case_snapshot_sha256": signed["expected_case_snapshot_sha256"],
        "subject_snapshot_sha256": signed["expected_subject_snapshot_sha256"],
        "action": signed["action"],
        "target_graph_status": signed.get("target_graph_status"),
        "target_budget_status": signed.get("target_budget_status"),
        "reconciler_actor_id": signed["reconciler_actor_id"],
        "issued_ts_ms": signed["issued_ts_ms"],
        "attestation_ref": signed["attestation_ref"],
        "attestation_key_id": signed["attestation_key_id"],
        "attestation_signature": signed["attestation_signature"],
    }
    if (
        not graph_id
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or str(effect.get("status") or "").upper() != "COMMITTED"
        or str(effect.get("attempt_id") or "") != attempt_id
        or str(effect.get("receipt_ref") or "")
        != str(proof.get("proof_id") or "")
        or proof.get("authority_kind") != "SIGNED_OPERATOR"
        or any(proof.get(key) != value for key, value in expected.items())
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "terminal reconciliation durable outcome binding drifted",
        )
    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "handler_receipt_ref": str(effect.get("receipt_ref") or ""),
        "proof_digest": str(proof.get("proof_digest") or ""),
        "exact_replay": receipt.get("replayed") is True,
    }


def _reopen_signed_command(
    *,
    command_store: Any,
    request_id: str,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """按 bundle 引用重开并校验原签名 command，不重新信任调用方的完整载荷。"""
    bundle_ref = str(args.get("signed_command_ref") or "").strip()
    command_sha256 = str(args.get("signed_command_sha256") or "").strip()
    case_id = str(args.get("case_id") or "").strip()
    reopened = command_store.read(bundle_ref)
    command_raw = reopened.get("command") if isinstance(reopened, Mapping) else None
    command = dict(command_raw) if isinstance(command_raw, Mapping) else {}
    if (
        not request_id
        or reopened.get("request_id") != request_id
        or reopened.get("case_id") != case_id
        or reopened.get("command_sha256") != command_sha256
        or command.get("operator_command_id") != "task_terminal.reconcile"
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "terminal reconciliation signed command binding drifted",
        )
    return command


__all__ = [
    "GovernedTerminalReconciliationMixin",
    "TERMINAL_EFFECT_TYPE",
    "TERMINAL_TARGET_ID",
    "require_durable_terminal_outcome",
    "terminal_reconciliation_commit_event",
]
