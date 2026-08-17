"""可逆陪伴表达 profile 的已签名用户控制入口。

该边界只接收完整结构化的低风险偏好请求，不解析聊天句子、不推断情绪、
不接受 prompt 片段，也不允许调用方改写 Soul、自我情感、关系同意或亲密
程度。入口负责将已认证的用户意图绑定到关系作用域；唯一的 profile 写入
者仍是 canonical Life 账本。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleResult,
    CompanionProfileLifecycleService,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)
from natsume_contracts.life import (
    CompanionProfileControlRequestV1,
    CompanionProfileRollbackRequestV1,
    LifeCommandReceipt,
)


COMPANION_PROFILE_INGRESS_UNAVAILABLE = "COMPANION_PROFILE_INGRESS_UNAVAILABLE"
COMPANION_PROFILE_BODY_INVALID = "COMPANION_PROFILE_BODY_INVALID"
COMPANION_PROFILE_PRINCIPAL_KIND_DENIED = "COMPANION_PROFILE_PRINCIPAL_KIND_DENIED"
COMPANION_PROFILE_SCOPE_BINDING_DENIED = "COMPANION_PROFILE_SCOPE_BINDING_DENIED"
COMPANION_PROFILE_EVIDENCE_UNAVAILABLE = "COMPANION_PROFILE_EVIDENCE_UNAVAILABLE"
COMPANION_PROFILE_RESULT_CONFLICT = "COMPANION_PROFILE_RESULT_CONFLICT"
COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE = (
    "COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE"
)
COMPANION_PROFILE_COMMAND_NOT_APPLIED = "COMPANION_PROFILE_COMMAND_NOT_APPLIED"
COMPANION_PROFILE_LIFECYCLE_REJECTED = "COMPANION_PROFILE_LIFECYCLE_REJECTED"


class CompanionProfileIngressError(RuntimeError):
    """可安全映射给 Host HTTP 适配器的无秘密错误。

    对外只携带稳定原因码，不把签名材料、原始请求体或内部账本异常暴露给
    客户端，避免错误处理本身成为旁路信息源。
    """

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = str(reason_code or COMPANION_PROFILE_BODY_INVALID)
        self.detail = str(detail or self.reason_code)
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class CompanionProfileIngressResult:
    """已受理 profile 生命周期的回执，不含私密对话内容。

    返回对象把 Secure Ingress claim 与 Life receipt 关联起来，供调用方在
    重试时确认同一控制已完成；它刻意只投影 profile 摘要和引用，而非证据
    所来自的用户文本。
    """

    request: CompanionProfileControlRequestV1 | CompanionProfileRollbackRequestV1
    lifecycle: CompanionProfileLifecycleResult
    receipt: LifeCommandReceipt
    receipt_ref: str
    ingress_audit_ref: str
    ingress_claim_ref: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        state = self.lifecycle.state
        candidate = state.canary_profile
        profile = candidate if candidate is not None else state.active_profile
        return {
            "status": self.receipt.status.value,
            "reason_code": self.lifecycle.reason_code,
            "workspace_id": self.request.workspace_id,
            "relationship_subject_id": self.request.relationship_subject_id,
            "session_id": self.request.session_id,
            "profile_id": profile.profile_id,
            "profile_digest": profile.profile_digest,
            "profile_revision": state.revision,
            "profile_stage": "CANARY" if candidate is not None else "ACTIVE",
            "action": (
                "ROLLBACK"
                if isinstance(self.request, CompanionProfileRollbackRequestV1)
                else "CANARY_START"
            ),
            "command_id": self.receipt.command_id,
            "event_id": self.receipt.event_id,
            "state_version": self.receipt.state_version,
            "state_digest": self.receipt.state_digest,
            "receipt_ref": self.receipt_ref,
            "replayed": self.replayed,
            "secure_ingress": {
                "audit_ref": self.ingress_audit_ref,
                "claim_ref": self.ingress_claim_ref,
            },
        }


def _strict_json_object(raw_body: bytes) -> Mapping[str, Any]:
    """按签名覆盖的原始字节严格解码单一 JSON 对象。

    拒绝重复键和非有限数，防止不同解析器对同一签名字节得出不同控制语义。
    """
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_non_finite(_value: str) -> None:
        raise ValueError("non-finite JSON value")

    try:
        payload = json.loads(
            bytes(raw_body).decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_non_finite,
        )
    except Exception as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request must be one strict UTF-8 JSON object",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request must be a JSON object",
        )
    return payload


def _request_from_raw_body(raw_body: bytes) -> CompanionProfileControlRequestV1:
    try:
        return CompanionProfileControlRequestV1.from_mapping(
            _strict_json_object(raw_body)
        )
    except CompanionProfileIngressError:
        raise
    except (TypeError, ValueError) as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request does not satisfy companion profile control v1",
        ) from exc


def _rollback_request_from_raw_body(
    raw_body: bytes,
) -> CompanionProfileRollbackRequestV1:
    try:
        return CompanionProfileRollbackRequestV1.from_mapping(
            _strict_json_object(raw_body)
        )
    except CompanionProfileIngressError:
        raise
    except (TypeError, ValueError) as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request does not satisfy companion profile rollback v1",
        ) from exc


def _rejection_ref(*, authority: SecureIngressAuthority, reason_code: str) -> str:
    """为一次拒绝派生可复现但不泄露请求内容的 claim 结果引用。"""
    material = {
        "schema_version": "companion_profile_rejection.v1",
        "request_id": str(authority.request_id or ""),
        "claim_ref": str(authority.claim_ref or ""),
        "body_sha256": str(authority.body_sha256 or ""),
        "reason_code": str(reason_code or ""),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "companion-profile-rejection://sha256/" + hashlib.sha256(encoded).hexdigest()


class CompanionProfileIngress:
    """把有作用域的用户偏好试验准入 canonical Life UoW。

    这是 HTTP 认证与领域生命周期之间的闸门：先验证签名、主体种类与租户/
    关系绑定，再把 ingress 审计引用交给生命周期服务。成功和拒绝均尽力
    回填同一个 claim，令客户端重试能恢复结果而不是重新发起状态迁移。
    """

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        complete_claim: Callable[..., None],
        companion_profile_lifecycle_service_provider: Callable[
            [], CompanionProfileLifecycleService | None
        ],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be a SecureIngressAuthorityEvaluator")
        if not callable(complete_claim):
            raise TypeError("complete_claim must be callable")
        if not callable(companion_profile_lifecycle_service_provider):
            raise TypeError("companion_profile_lifecycle_service_provider must be callable")
        self._authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_PROFILE,
        )
        self._rollback_authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        )
        self._complete_claim = complete_claim
        self._companion_profile_lifecycle_service_provider = (
            companion_profile_lifecycle_service_provider
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def submit(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionProfileIngressResult:
        """认证并提交一次显式的低风险偏好灰度请求。

        任何正文解析、主体绑定或证据缺失都会先记录确定性拒绝；只有 Life
        命令已应用且回执与既有 claim 结果一致时，才允许把成功写回 ingress。
        """

        now_ms = max(
            0,
            int(self._clock_ms() if received_at_ms is None else received_at_ms),
        )
        authority = self._authority_adapter.evaluate(
            request=request,
            raw_body=bytes(raw_body),
            received_at_ms=now_ms,
        )
        if not authority.admissible:
            raise CompanionProfileIngressError(authority.reason_code)
        try:
            control = _request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionProfileIngressError as exc:
            self._complete_rejection(
                authority=authority,
                reason_code=exc.reason_code,
                at_ms=now_ms,
            )
            raise

        lifecycle = self._companion_profile_lifecycle_service_provider()
        if not isinstance(lifecycle, CompanionProfileLifecycleService):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "canonical companion profile lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable preference-control evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )
        try:
            lifecycle_result = lifecycle.start_canary(
                workspace_id=control.workspace_id,
                relationship_subject_id=control.relationship_subject_id,
                session_id=control.session_id,
                request_id=request_id,
                preferences=control.preferences,
                evidence_aftereffect_ids=None,
                source_control_ref=audit_ref,
            )
        except (RuntimeError, ValueError) as exc:
            reason_code = self._lifecycle_reason(exc)
            self._complete_rejection(
                authority=authority,
                reason_code=reason_code,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(reason_code) from exc
        receipt = lifecycle_result.receipt
        if receipt is None or receipt.status.value != "APPLIED":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_COMMAND_NOT_APPLIED,
                "canonical companion profile command was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical companion profile receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionProfileIngressResult(
            request=control,
            lifecycle=lifecycle_result,
            receipt=receipt,
            receipt_ref=receipt_ref,
            ingress_audit_ref=audit_ref,
            ingress_claim_ref=claim_ref,
            replayed=bool(authority.duplicate or receipt.replayed),
        )

    def submit_rollback(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionProfileIngressResult:
        """认证一次用户发起的精确回滚，且只能恢复当前版本的前驱。

        生命周期层会再次校验当前 active profile，因此入口层的旧页面、重复
        请求或并发请求不能把关系回退到调用者任意指定的历史版本。
        """

        now_ms = max(
            0,
            int(self._clock_ms() if received_at_ms is None else received_at_ms),
        )
        authority = self._rollback_authority_adapter.evaluate(
            request=request,
            raw_body=bytes(raw_body),
            received_at_ms=now_ms,
        )
        if not authority.admissible:
            raise CompanionProfileIngressError(authority.reason_code)
        try:
            control = _rollback_request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionProfileIngressError as exc:
            self._complete_rejection(
                authority=authority,
                reason_code=exc.reason_code,
                at_ms=now_ms,
            )
            raise

        lifecycle = self._companion_profile_lifecycle_service_provider()
        if not isinstance(lifecycle, CompanionProfileLifecycleService):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "canonical companion profile lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable profile-rollback evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )
        try:
            lifecycle_result = lifecycle.rollback_active(
                workspace_id=control.workspace_id,
                relationship_subject_id=control.relationship_subject_id,
                session_id=control.session_id,
                request_id=request_id,
                expected_active_profile_id=control.expected_active_profile_id,
                source_control_ref=audit_ref,
            )
        except (RuntimeError, ValueError) as exc:
            reason_code = self._lifecycle_reason(exc)
            self._complete_rejection(
                authority=authority,
                reason_code=reason_code,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(reason_code) from exc
        receipt = lifecycle_result.receipt
        if receipt is None or receipt.status.value != "APPLIED":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_COMMAND_NOT_APPLIED,
                "canonical companion profile rollback was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical profile rollback receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionProfileIngressResult(
            request=control,
            lifecycle=lifecycle_result,
            receipt=receipt,
            receipt_ref=receipt_ref,
            ingress_audit_ref=audit_ref,
            ingress_claim_ref=claim_ref,
            replayed=bool(authority.duplicate or receipt.replayed),
        )

    @staticmethod
    def _assert_user_scope(
        *,
        authority: SecureIngressAuthority,
        control: (
            CompanionProfileControlRequestV1 | CompanionProfileRollbackRequestV1
        ),
    ) -> None:
        """禁止代理主体及跨租户/跨关系作用域的已签名控制。"""
        if str(authority.principal_kind or "").strip().lower() != "user":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
                "only a user principal may trial companion expression preferences",
            )
        if (
            str(authority.principal_id or "").strip()
            != control.relationship_subject_id
            or str(authority.tenant_id or "").strip() != control.workspace_id
        ):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_SCOPE_BINDING_DENIED,
                "signed principal and tenant must match the relationship scope",
            )

    @staticmethod
    def _lifecycle_reason(exc: Exception) -> str:
        """将领域拒绝归一为稳定对外原因码，未知细节一律不向外透传。"""
        candidate = str(exc or "").strip().upper()
        if candidate.startswith("COMPANION_PROFILE_"):
            return candidate
        return COMPANION_PROFILE_LIFECYCLE_REJECTED

    def _complete_success(
        self,
        *,
        authority: SecureIngressAuthority,
        receipt_ref: str,
        at_ms: int,
    ) -> None:
        """把唯一 Life 回执附回 claim；不匹配即视为恢复一致性故障。"""
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "Secure Ingress claim is unavailable",
            )
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=receipt_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "canonical profile result could not be attached to Secure Ingress",
            ) from exc

    def _complete_rejection(
        self,
        *,
        authority: SecureIngressAuthority,
        reason_code: str,
        at_ms: int,
    ) -> None:
        """尽力持久化拒绝结果，避免同一 claim 重试后出现相互矛盾的结论。"""
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            return
        result_ref = _rejection_ref(authority=authority, reason_code=reason_code)
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != result_ref:
            return
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=result_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "rejected profile control result could not be attached to Secure Ingress",
            ) from exc


__all__ = [
    "COMPANION_PROFILE_BODY_INVALID",
    "COMPANION_PROFILE_COMMAND_NOT_APPLIED",
    "COMPANION_PROFILE_EVIDENCE_UNAVAILABLE",
    "COMPANION_PROFILE_INGRESS_UNAVAILABLE",
    "COMPANION_PROFILE_LIFECYCLE_REJECTED",
    "COMPANION_PROFILE_PRINCIPAL_KIND_DENIED",
    "COMPANION_PROFILE_RESULT_CONFLICT",
    "COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE",
    "COMPANION_PROFILE_SCOPE_BINDING_DENIED",
    "CompanionProfileIngress",
    "CompanionProfileIngressError",
    "CompanionProfileIngressResult",
]
