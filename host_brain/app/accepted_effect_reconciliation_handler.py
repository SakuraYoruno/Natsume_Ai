"""受 Acceptance 约束的通用签名 Acceptance Effect 归因执行器。

未知结果的 effect 由 Operator 提供签名证据，但 handler 不信任瞬态 payload：
它先重开耐久 evidence，核对 case/effect/目的/结论，再调用分流后的对账服务。
只有可验证的 proof 结果才会产生回执；可能已发生但无法归因的异常保持
outcome unknown，交由恢复流程继续处理。
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import json
from typing import Any

from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.task_runtime.acceptance_effect_reconciliation import (
    AcceptanceEffectReconciliationError,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationError,
)


EFFECT_RECONCILIATION_EFFECT_TYPE = "task.acceptance_effect_reconciliation"


class AcceptedEffectReconciliationHandler:
    """只在 Task 接受后应用或精确重放一份签名归因证明。"""

    __slots__ = ("_evidence_store", "_service")

    def __init__(self, *, service: Any, evidence_store: Any) -> None:
        if not callable(
            getattr(service, "reconcile_graph_cancellation_effect", None)
        ):
            raise TypeError(
                "Effect reconciliation handler requires command service"
            )
        if not callable(
            getattr(
                evidence_store,
                "get_acceptance_effect_reconciliation_evidence",
                None,
            )
        ):
            raise TypeError(
                "Effect reconciliation handler requires evidence store"
            )
        self._service = service
        self._evidence_store = evidence_store

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Effect handler 入口；具体应用与回收 claim 对账共享同一证据路径。"""
        return await self._apply(payload)

    async def reconcile(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """对回收的 effect claim 重放同一归因，而不创建新的签名请求。"""
        return await self._apply(payload)

    async def _apply(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """重开 evidence、调用对账服务并投影 proof 身份为稳定 COMMIT 回执。"""
        durable_request = effect_reconciliation_effect_request(payload)
        evidence_raw = (
            self._evidence_store
            .get_acceptance_effect_reconciliation_evidence(
                durable_request["evidence_ref"]
            )
        )
        if not isinstance(evidence_raw, Mapping):
            raise ValueError(
                "Acceptance Effect reconciliation evidence could not be "
                "reopened"
            )
        evidence = dict(evidence_raw)
        if (
            _durable_request_from_evidence(evidence) != durable_request
            or str(evidence.get("request_id") or "").strip()
            != durable_request["request_id"]
        ):
            raise ValueError(
                "Acceptance Effect reconciliation evidence binding drifted"
            )
        request = {
            key: evidence.get(key)
            for key in (
                "effect_id",
                "case_id",
                "expected_case_snapshot_sha256",
                "expected_subject_snapshot_sha256",
                "purpose",
                "outcome",
                "reconciler_actor_id",
                "issued_ts_ms",
                "downstream_receipt_ref",
                "executor_quiescence_ref",
                "attestation_ref",
                "attestation_key_id",
                "attestation_signature",
            )
        }
        try:
            raw = self._service.reconcile_graph_cancellation_effect(
                **request
            )
            result = await raw if inspect.isawaitable(raw) else raw
        except (
            AcceptanceEffectReconciliationError,
            GraphCancellationEffectReconciliationError,
        ) as exc:
            if exc.side_effects_may_have_run:
                raise AcceptanceEffectOutcomeUnknown(
                    "signed Acceptance Effect attribution outcome is uncertain"
                ) from exc
            raise
        if not isinstance(result, Mapping):
            raise TypeError(
                "Acceptance Effect reconciliation result must be a mapping"
            )
        projection = effect_reconciliation_result_projection(result)
        return {
            "receipt_ref": effect_reconciliation_handler_receipt_ref(
                request=request,
                result=projection,
            ),
            "status": "COMMITTED",
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "purpose": request["purpose"],
            "outcome": request["outcome"],
            "proof": projection,
        }


def effect_reconciliation_effect_request(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """验证 effect payload 的最小身份，只允许通用未知效果的两种归因结论。"""
    value = dict(payload or {})
    request_id = str(value.get("request_id") or "").strip()
    evidence_ref = str(value.get("evidence_ref") or "").strip()
    evidence_digest = str(
        value.get("evidence_digest") or ""
    ).strip().lower()
    digest_suffix = evidence_digest.removeprefix("sha256:")
    request = {
        "request_id": request_id,
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
        str(value.get("operator_command_id") or "").strip()
        != "task_effect.reconcile"
        or not request_id
        or not evidence_ref.startswith(
            "effect-reconcile-evidence://sha256/"
        )
        or evidence_ref.rsplit("/", 1)[-1] != digest_suffix
        or len(digest_suffix) != 64
        or any(
            character not in "0123456789abcdef"
            for character in digest_suffix
        )
        or not request["effect_id"]
        or not request["case_id"]
        or request["purpose"] != "GENERAL_UNKNOWN_EFFECT"
        or request["outcome"]
        not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}
        or not request["reconciler_actor_id"]
    ):
        raise ValueError(
            "Acceptance Effect reconciliation payload is invalid"
        )
    return request


def _durable_request_from_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """从耐久 evidence 重建同一请求，用于精确比较 effect payload 是否漂移。"""
    return effect_reconciliation_effect_request(
        {
            "operator_command_id": "task_effect.reconcile",
            "request_id": value.get("request_id"),
            "evidence_ref": value.get("evidence_ref"),
            "evidence_digest": value.get("evidence_digest"),
            "effect_id": value.get("effect_id"),
            "case_id": value.get("case_id"),
            "purpose": value.get("purpose"),
            "outcome": value.get("outcome"),
            "reconciler_actor_id": value.get("reconciler_actor_id"),
        }
    )


def effect_reconciliation_result_projection(
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """收敛 Store 返回的 proof/case/effect 为公共、可校验的结果投影。"""
    value = dict(result or {})
    case_raw = value.get("case")
    proof_raw = value.get("proof")
    effect_raw = value.get("effect")
    case = dict(case_raw) if isinstance(case_raw, Mapping) else {}
    proof = dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
    effect = dict(effect_raw) if isinstance(effect_raw, Mapping) else {}
    projection = {
        "status": str(value.get("status") or "").strip().upper(),
        "case_id": str(
            value.get("case_id") or case.get("case_id") or ""
        ).strip(),
        "effect_id": str(
            value.get("effect_id") or effect.get("effect_id") or ""
        ).strip(),
        "purpose": str(
            value.get("purpose")
            or proof.get("purpose")
            or case.get("purpose")
            or ""
        ).strip().upper(),
        "outcome": str(
            value.get("outcome") or proof.get("outcome") or ""
        ).strip().upper(),
        "target": str(
            value.get("target_effect_status")
            or value.get("target")
            or proof.get("target_effect_status")
            or proof.get("target_resolution_status")
            or ""
        ).strip().upper(),
        "receipt_ref": (
            str(
                value.get("receipt_ref")
                or proof.get("receipt_ref")
                or ""
            ).strip()
            or None
        ),
        "executor_quiescence_ref": (
            str(
                value.get("executor_quiescence_ref")
                or proof.get("executor_quiescence_ref")
                or ""
            ).strip()
            or None
        ),
        "proof_id": str(
            value.get("proof_id") or proof.get("proof_id") or ""
        ).strip(),
        "outbox_id": (
            str(
                value.get("outbox_id")
                or proof.get("outbox_id")
                or ""
            ).strip()
            or None
        ),
    }
    if (
        not projection["status"]
        or not projection["case_id"]
        or not projection["effect_id"]
        or not projection["purpose"]
        or not projection["outcome"]
        or not projection["target"]
        or not projection["proof_id"]
    ):
        raise TypeError(
            "Acceptance Effect reconciliation result binding is incomplete"
        )
    return projection


def effect_reconciliation_handler_receipt_ref(
    *,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> str:
    """把认证者、签名引用和 proof 结果散列为可重放的对账回执。"""
    projection = effect_reconciliation_result_projection(result)
    material = {
        "schema_version": (
            "Natsume.task.acceptance_effect_reconciliation_receipt.v1"
        ),
        "effect_id": str(request.get("effect_id") or "").strip(),
        "case_id": str(request.get("case_id") or "").strip(),
        "purpose": str(request.get("purpose") or "").strip().upper(),
        "outcome": str(request.get("outcome") or "").strip().upper(),
        "reconciler_actor_id": str(
            request.get("reconciler_actor_id") or ""
        ).strip(),
        "attestation_ref": str(
            request.get("attestation_ref") or ""
        ).strip(),
        "attestation_key_id": str(
            request.get("attestation_key_id") or ""
        ).strip(),
        "result": projection,
    }
    if (
        not material["effect_id"]
        or not material["case_id"]
        or not material["purpose"]
        or not material["outcome"]
        or not material["reconciler_actor_id"]
        or not material["attestation_ref"]
        or not material["attestation_key_id"]
        or projection["effect_id"] != material["effect_id"]
        or projection["case_id"] != material["case_id"]
        or projection["purpose"] != material["purpose"]
        or projection["outcome"] != material["outcome"]
    ):
        raise ValueError(
            "Acceptance Effect reconciliation receipt binding drifted"
        )
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return (
        "effect-reconcile://receipt/sha256/"
        + hashlib.sha256(encoded).hexdigest()
    )


def register_accepted_effect_reconciliation_handler(
    handlers: dict[str, Any],
    *,
    service: Any | None,
    evidence_store: Any | None,
) -> None:
    """仅在对账服务和证据存储同时存在时注册通用恢复 handler。"""

    if service is None:
        return
    if evidence_store is None:
        raise TypeError("Effect reconciliation handler requires evidence store")
    handlers[EFFECT_RECONCILIATION_EFFECT_TYPE] = (
        AcceptedEffectReconciliationHandler(
            service=service,
            evidence_store=evidence_store,
        )
    )


__all__ = [
    "AcceptedEffectReconciliationHandler",
    "EFFECT_RECONCILIATION_EFFECT_TYPE",
    "effect_reconciliation_effect_request",
    "effect_reconciliation_handler_receipt_ref",
    "effect_reconciliation_result_projection",
    "register_accepted_effect_reconciliation_handler",
]
