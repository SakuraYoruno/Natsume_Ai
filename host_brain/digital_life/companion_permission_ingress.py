"""主动陪伴联系的失败关闭式结构化用户控制。

此边界明确不接受从自然语言推断同意。只有来自准确用户及工作区的已签名
Secure Ingress 请求，才能改变 ``relationship_v2.proactive_permission``。
canonical Life 命令仍是唯一状态写入者；Secure Ingress 只负责认证、作用域
绑定及让该用户控制可安全回放。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
)
from host_brain.secure_ingress.authority import (
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)
from host_brain.secure_ingress.surfaces import IngressSurface
from natsume_contracts.life import (
    CompanionProactivePermissionRequestV1,
    LifeCommandReceipt,
)


COMPANION_PERMISSION_INGRESS_UNAVAILABLE = (
    "COMPANION_PERMISSION_INGRESS_UNAVAILABLE"
)
COMPANION_PERMISSION_BODY_INVALID = "COMPANION_PERMISSION_BODY_INVALID"
COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED = (
    "COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED"
)
COMPANION_PERMISSION_SCOPE_BINDING_DENIED = (
    "COMPANION_PERMISSION_SCOPE_BINDING_DENIED"
)
COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE = (
    "COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE"
)
COMPANION_PERMISSION_RESULT_CONFLICT = "COMPANION_PERMISSION_RESULT_CONFLICT"
COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE = (
    "COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE"
)
COMPANION_PERMISSION_COMMAND_NOT_APPLIED = (
    "COMPANION_PERMISSION_COMMAND_NOT_APPLIED"
)


class CompanionPermissionIngressError(RuntimeError):
    """可供 Host HTTP 适配器使用、且不包含秘密的拒绝。"""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = str(reason_code or COMPANION_PERMISSION_BODY_INVALID)
        self.detail = str(detail or self.reason_code)
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class CompanionPermissionIngressResult:
    """已受理的 canonical 回执，不暴露原始同意内容。

    结果仅投影用户最终许可值和可追溯引用，供界面恢复或幂等重试；签名正文
    与 Secure Ingress 内部凭据始终留在各自的信任边界内。
    """

    request: CompanionProactivePermissionRequestV1
    receipt: LifeCommandReceipt
    receipt_ref: str
    ingress_audit_ref: str
    ingress_claim_ref: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.receipt.status.value,
            "reason_code": self.receipt.reason_code,
            "workspace_id": self.request.workspace_id,
            "relationship_subject_id": self.request.relationship_subject_id,
            "session_id": self.request.session_id,
            "proactive_permission": self.request.proactive_permission,
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
    """严格解析受签名字节覆盖的 JSON，防止同一请求出现歧义语义。"""
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key")
            value[key] = item
        return value

    def _reject_non_finite(_value: str) -> None:
        raise ValueError("non-finite JSON value")

    try:
        decoded = bytes(raw_body).decode("utf-8")
        payload = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except Exception as exc:
        raise CompanionPermissionIngressError(
            COMPANION_PERMISSION_BODY_INVALID,
            "request must be one strict UTF-8 JSON object",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CompanionPermissionIngressError(
            COMPANION_PERMISSION_BODY_INVALID,
            "request must be a JSON object",
        )
    return payload


def _request_from_raw_body(raw_body: bytes) -> CompanionProactivePermissionRequestV1:
    payload = _strict_json_object(raw_body)
    try:
        return CompanionProactivePermissionRequestV1.from_mapping(payload)
    except (TypeError, ValueError) as exc:
        raise CompanionPermissionIngressError(
            COMPANION_PERMISSION_BODY_INVALID,
            "request does not satisfy companion proactive permission v1",
        ) from exc


def _rejection_ref(*, authority: SecureIngressAuthority, reason_code: str) -> str:
    """生成不含同意正文的确定性拒绝引用，供 claim 的重试恢复。"""
    material = {
        "schema_version": "companion_permission_rejection.v1",
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
    return "companion-permission-rejection://sha256/" + hashlib.sha256(encoded).hexdigest()


class CompanionPermissionIngress:
    """从已认证用户控制通往 Life authority 的应用层桥接。

    主动联系具有关系边界，因此该桥接先把签名主体和租户锁定到同一关系，才
    把审计引用交给 RelationshipLifecycleService。无论成功还是拒绝，结果都
    尽力回填原 claim，防止网络中断后同一控制被当作新的同意决定。
    """

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        complete_claim: Callable[..., None],
        relationship_lifecycle_service_provider: Callable[
            [], RelationshipLifecycleService | None
        ],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be a SecureIngressAuthorityEvaluator")
        if not callable(complete_claim):
            raise TypeError("complete_claim must be callable")
        if not callable(relationship_lifecycle_service_provider):
            raise TypeError("relationship_lifecycle_service_provider must be callable")
        self._authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_CONTACT,
        )
        self._complete_claim = complete_claim
        self._relationship_lifecycle_service_provider = (
            relationship_lifecycle_service_provider
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def submit(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionPermissionIngressResult:
        """认证、绑定作用域并入账一次显式用户决定。

        入口缺失审计证据、主体不是用户、或主体与关系作用域不一致时一律拒绝；
        只有 canonical 命令已应用且与既有 claim 结果相符，才确认成功。
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
            raise CompanionPermissionIngressError(authority.reason_code)
        try:
            control = _request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionPermissionIngressError as exc:
            self._complete_rejection(authority=authority, reason_code=exc.reason_code, at_ms=now_ms)
            raise

        lifecycle = self._relationship_lifecycle_service_provider()
        if not isinstance(lifecycle, RelationshipLifecycleService):
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
                "canonical relationship lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable consent evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )

        receipt = lifecycle.record_proactive_permission(
            workspace_id=control.workspace_id,
            relationship_subject_id=control.relationship_subject_id,
            session_id=control.session_id,
            request_id=request_id,
            proactive_permission=control.proactive_permission,
            source_ref=audit_ref,
        )
        if receipt.status.value != "APPLIED":
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_COMMAND_NOT_APPLIED,
                "canonical relationship command was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical relationship receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionPermissionIngressResult(
            request=control,
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
        control: CompanionProactivePermissionRequestV1,
    ) -> None:
        """确认同意只能由该关系中的用户本人在所属工作区作出。"""
        if str(authority.principal_kind or "").strip().lower() != "user":
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED,
                "only a user principal may change proactive contact permission",
            )
        if (
            str(authority.principal_id or "").strip()
            != control.relationship_subject_id
            or str(authority.tenant_id or "").strip() != control.workspace_id
        ):
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
                "signed principal and tenant must match the relationship scope",
            )

    def _complete_success(
        self,
        *,
        authority: SecureIngressAuthority,
        receipt_ref: str,
        at_ms: int,
    ) -> None:
        """将唯一账本回执绑定到 ingress claim，维持请求/结果的一一对应。"""
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE,
                "Secure Ingress claim is unavailable",
            )
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=receipt_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE,
                "canonical consent result could not be attached to Secure Ingress",
            ) from exc

    def _complete_rejection(
        self,
        *,
        authority: SecureIngressAuthority,
        reason_code: str,
        at_ms: int,
    ) -> None:
        """在可用时记录确定性拒绝，避免重试得到与首请求不同的结论。"""
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
            raise CompanionPermissionIngressError(
                COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE,
                "rejected user-control result could not be attached to Secure Ingress",
            ) from exc


__all__ = [
    "COMPANION_PERMISSION_BODY_INVALID",
    "COMPANION_PERMISSION_COMMAND_NOT_APPLIED",
    "COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE",
    "COMPANION_PERMISSION_INGRESS_UNAVAILABLE",
    "COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED",
    "COMPANION_PERMISSION_RESULT_CONFLICT",
    "COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE",
    "COMPANION_PERMISSION_SCOPE_BINDING_DENIED",
    "CompanionPermissionIngress",
    "CompanionPermissionIngressError",
    "CompanionPermissionIngressResult",
]
