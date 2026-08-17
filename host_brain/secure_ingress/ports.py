"""SecureIngressService 使用的依赖端口。

端口将认证事实、限流状态、幂等声明与审计耐久性从准入编排中分离；实现异常会由服务层
失败关闭，因此适配器不得把临时不可用伪装成允许或空白的正常结果。
"""

from __future__ import annotations

from typing import Protocol

from .models import (
    ClaimDecision,
    CredentialDecision,
    IngressAuditEvent,
    PrincipalAuthority,
    RateDecision,
    SecureIngressRequest,
)


class PrincipalStore(Protocol):
    """按主体、密钥与接收时刻解析版本化持久化授权。"""

    def resolve(
        self,
        *,
        principal_id: str,
        key_id: str,
        at_ms: int,
    ) -> PrincipalAuthority | None: ...


class CredentialVerifier(Protocol):
    """验证规范请求证明，并只返回脱敏的凭据裁决。"""

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision: ...


class IngressRateLimiter(Protocol):
    """认证后的权威限流端口，分桶必须覆盖主体、动作和所需范围。"""

    def consume(
        self,
        *,
        principal_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        at_ms: int,
    ) -> RateDecision: ...


class IngressAttemptLimiter(Protocol):
    """认证前尝试限流端口，限制无效凭据或坏信封的探测频率。"""

    def consume_attempt(
        self,
        *,
        transport_key: str,
        claimed_principal_id: str | None,
        key_id: str | None,
        at_ms: int,
    ) -> RateDecision: ...


class IdempotencyStore(Protocol):
    """原子声明、提交审计及撤销未完成声明的持久化事务边界。"""

    def try_claim(
        self,
        *,
        principal_id: str,
        surface: str,
        action: str,
        required_scopes: tuple[str, ...],
        idempotency_key: str,
        nonce: str,
        body_sha256: str,
        request_id: str,
        at_ms: int,
        expires_at_ms: int,
    ) -> ClaimDecision: ...

    def commit_with_audit(
        self,
        *,
        claim_ref: str,
        event: IngressAuditEvent,
    ) -> str: ...

    def abort_claim(self, *, claim_ref: str) -> None: ...


class IngressAuditSink(Protocol):
    """只接受已脱敏入口事件的耐久追加端口。"""

    def append(self, event: IngressAuditEvent) -> str: ...


class SecureIngressStateStore(IdempotencyStore, IngressAuditSink, Protocol):
    """主状态仓储：必须原子提交 ALLOW 声明及其审计，不能单独确认其中一项。"""


__all__ = [
    "CredentialVerifier",
    "IdempotencyStore",
    "IngressAuditSink",
    "IngressAttemptLimiter",
    "IngressRateLimiter",
    "PrincipalStore",
    "SecureIngressStateStore",
]
