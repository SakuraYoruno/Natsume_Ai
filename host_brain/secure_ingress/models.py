"""闭合失败的 Secure Ingress 决策链内部契约。

对象分离主体授权、凭据、速率、幂等 claim、审计和最终决定；每阶段均可携带稳定原因码，
但原始请求正文、token 和签名不进入持久化决策模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    IngressIdentityEnvelope,
    PrincipalKind,
)


class IngressReasonCode(str, Enum):
    """入口链对外稳定暴露的终态原因码，避免以异常文本作为安全协议。"""

    ALLOWED = "SECURE_INGRESS_ALLOWED"
    SCHEMA_INVALID = "SECURE_INGRESS_SCHEMA_INVALID"
    PRINCIPAL_INVALID = "SECURE_INGRESS_PRINCIPAL_INVALID"
    CREDENTIAL_MISSING = "SECURE_INGRESS_CREDENTIAL_MISSING"
    CREDENTIAL_INVALID = "SECURE_INGRESS_CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "SECURE_INGRESS_CREDENTIAL_EXPIRED"
    SCOPE_DENIED = "SECURE_INGRESS_SCOPE_DENIED"
    RATE_LIMITED = "SECURE_INGRESS_RATE_LIMITED"
    RATE_BACKEND_UNAVAILABLE = "SECURE_INGRESS_RATE_BACKEND_UNAVAILABLE"
    REPLAY_DETECTED = "SECURE_INGRESS_REPLAY_DETECTED"
    IDEMPOTENCY_KEY_MISSING = "SECURE_INGRESS_IDEMPOTENCY_KEY_MISSING"
    IDEMPOTENCY_DUPLICATE = "SECURE_INGRESS_IDEMPOTENCY_DUPLICATE"
    IDEMPOTENCY_CONFLICT = "SECURE_INGRESS_IDEMPOTENCY_CONFLICT"
    AUDIT_UNAVAILABLE = "SECURE_INGRESS_AUDIT_UNAVAILABLE"
    INTERNAL_ERROR = "SECURE_INGRESS_INTERNAL_ERROR"


class IngressStage(str, Enum):
    """拒绝或允许发生的最早安全阶段，用于审计定位而非重试指令。"""

    SCHEMA = "SCHEMA"
    PRINCIPAL = "PRINCIPAL"
    CREDENTIAL = "CREDENTIAL"
    SCOPE = "SCOPE"
    RATE = "RATE"
    CLAIM = "CLAIM"
    AUDIT = "AUDIT"
    COMPLETE = "COMPLETE"


class AuditStatus(str, Enum):
    """审计耐久性状态；非主耐久状态不能支撑允许决定。"""

    PRIMARY_DURABLE = "PRIMARY_DURABLE"
    EMERGENCY_DURABLE = "EMERGENCY_DURABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ClaimStatus(str, Enum):
    """幂等与 nonce 绑定尝试的原子结果。"""

    NEW = "NEW"
    DUPLICATE_SAME = "DUPLICATE_SAME"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class PrincipalAuthority:
    """持久化主体授权记录：身份、密钥、受众、动作与范围必须整体匹配。"""

    principal_id: str
    kind: PrincipalKind
    tenant_id: str
    key_id: str
    credential_kind: CredentialProofKind
    algorithm: str
    granted_scopes: tuple[str, ...]
    allowed_audiences: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    version: int
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class CredentialDecision:
    """凭据校验的最小结果，仅携带可审计摘要而不含 bearer 值或签名。"""

    valid: bool
    reason_code: IngressReasonCode | None
    credential_ref_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RateDecision:
    """某个入口限流分桶的观测；``available=False`` 必须由服务失败关闭。"""

    allowed: bool
    current_count: int
    limit: int
    window_ms: int
    backend: str
    available: bool = True
    lane: str = "AUTHORITY"


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    """幂等声明返回值；重复请求只可关联规范历史引用，不能获得新执行权。"""

    status: ClaimStatus
    claim_ref: str | None = None
    existing_result_ref: str | None = None
    canonical_request_id: str | None = None
    canonical_body_sha256: str | None = None
    canonical_audit_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SecureIngressRequest:
    """认证期间的传输请求；原始 body 仅用于摘要和验签，不能跨越到后续持久化层。"""
    identity: IngressIdentityEnvelope | None
    credential: CredentialProof | None
    surface: str
    method: str
    normalized_path: str
    action: str
    required_scopes: tuple[str, ...]
    idempotency_key: str
    nonce: str
    raw_body: bytes
    received_at_epoch_ms: int
    transport_key: str
    correlation_id: str | None = None

    @property
    def body_sha256(self) -> str:
        """计算原始正文摘要，供验签、幂等绑定和审计关联复用。"""
        return hashlib.sha256(self.raw_body).hexdigest()

    @property
    def request_id(self) -> str | None:
        """从已解析身份信封读取请求标识；未认证请求不伪造该字段。"""
        return self.identity.request_id if self.identity is not None else None

    @property
    def principal_id(self) -> str | None:
        """从已解析身份信封读取主体；实际授权仍需与持久化 authority 匹配。"""
        return self.identity.principal.principal_id if self.identity is not None else None

    def canonical_auth_bytes(self) -> bytes:
        """确定性编码所有授权相关字段，防止字段排序或范围集合产生验签分叉。"""
        if self.identity is None:
            raise ValueError("identity is required for canonical auth input")
        payload = {
            "action": self.action,
            "audience": self.identity.audience,
            "body_sha256": self.body_sha256,
            "correlation_id": self.correlation_id,
            "credential": self.identity.credential_proof.to_dict(),
            "idempotency_key": self.idempotency_key,
            "identity_schema": self.identity.schema_version.value,
            "method": self.method.upper(),
            "nonce": self.nonce,
            "path": self.normalized_path,
            "principal": self.identity.principal.to_dict(),
            "request_id": self.identity.request_id,
            "requested_scopes": list(self.identity.requested_scopes),
            "required_scopes": sorted(set(self.required_scopes)),
            "schema_version": "natsume.secure-ingress.signature.v1",
            "surface": self.surface,
            "transport_key_digest": digest_identifier(self.transport_key),
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class IngressAuditEvent:
    """脱敏的入口审计事件；摘要与阶段足以追溯决定而不泄露凭据/正文。"""
    request_id: str | None
    correlation_id: str | None
    surface: str
    action: str
    method: str
    normalized_path: str
    principal_id: str | None
    tenant_id: str | None
    key_id: str | None
    credential_kind: str | None
    credential_ref_digest: str | None
    requested_scopes: tuple[str, ...]
    required_scopes: tuple[str, ...]
    body_sha256: str
    idempotency_digest: str | None
    nonce_digest: str | None
    transport_key_digest: str | None
    decision: str
    reason_code: str
    stage: str
    claim_ref: str | None
    rate_snapshot: dict[str, Any] | None
    side_effects_executed: bool
    ts_ms: int


@dataclass(frozen=True, slots=True)
class SecureIngressDecision:
    """入口链的终态决定；重复/重放关联只通过规范引用暴露。"""
    allowed: bool
    stage: IngressStage
    reason_code: IngressReasonCode
    audit_ref: str | None
    audit_status: AuditStatus
    primary_reason_code: IngressReasonCode
    claim_ref: str | None
    existing_result_ref: str | None
    canonical_request_id: str | None = None
    canonical_body_sha256: str | None = None
    canonical_audit_ref: str | None = None
    side_effects_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """导出可返回给调用方的裁决摘要，不泄露规范请求或审计事件正文。"""
        return {
            "allowed": self.allowed,
            "audit_ref": self.audit_ref,
            "audit_status": self.audit_status.value,
            "claim_ref": self.claim_ref,
            "existing_result_ref": self.existing_result_ref,
            "reason_code": self.reason_code.value,
            "primary_reason_code": self.primary_reason_code.value,
            "side_effects_executed": False,
            "stage": self.stage.value,
        }


def digest_identifier(value: str | None) -> str | None:
    """把可识别但敏感的标识转成审计安全摘要；空值保持为空。"""
    text = str(value or "").strip()
    if not text:
        return None
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


__all__ = [
    "ClaimDecision",
    "ClaimStatus",
    "AuditStatus",
    "CredentialDecision",
    "IngressAuditEvent",
    "IngressReasonCode",
    "IngressStage",
    "PrincipalAuthority",
    "RateDecision",
    "SecureIngressDecision",
    "SecureIngressRequest",
    "digest_identifier",
]
