"""Secure Ingress 的凭据验证器。

验证器短暂接收运行时证明材料，但只返回可审计摘要，绝不持久化或暴露原始签名/token。
算法与凭据种类必须和主体授权精确匹配，防止跨机制替换。
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable, Mapping

from natsume_contracts.identity import CredentialProofKind

from .models import (
    CredentialDecision,
    IngressReasonCode,
    PrincipalAuthority,
    SecureIngressRequest,
    digest_identifier,
)


SecretResolver = Callable[[PrincipalAuthority], str | bytes | None]
TokenDigestResolver = Callable[[PrincipalAuthority], str | None]
HMAC_SHA256_ALGORITHM = "hmac-sha256"
OPAQUE_BEARER_ALGORITHM = "opaque-sha256"


def build_hmac_sha256_signature(request: SecureIngressRequest, secret: str | bytes) -> str:
    """基于规范认证字节生成 HMAC；调用方不能自行选择未覆盖的字段。"""
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(key, request.canonical_auth_bytes(), hashlib.sha256).hexdigest()


def _metadata_matches(request: SecureIngressRequest, authority: PrincipalAuthority) -> bool:
    """确认请求证明元数据与持久化授权同属一个主体和认证机制。"""
    if request.identity is None or request.credential is None:
        return False
    metadata = request.identity.credential_proof
    return (
        request.credential.metadata == metadata
        and metadata.key_id == authority.key_id
        and metadata.kind is authority.credential_kind
        and metadata.algorithm.lower() == authority.algorithm.lower()
        and request.identity.principal.principal_id == authority.principal_id
    )


class HmacSha256CredentialVerifier:
    """验证 HMAC 证明，并使用恒定时间比较避免签名比较侧信道。

    规范认证字节覆盖正文摘要、范围、路径和幂等坐标；因此验证通过只能说明这一次完整请求
    被持钥主体认可，不能把签名移植到另一动作或正文。
    """
    def __init__(self, *, secret_resolver: SecretResolver) -> None:
        self._secret_resolver = secret_resolver

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        """验证算法、元数据和 HMAC；所有失败归一为无效而不泄露比对细节。"""
        if authority.credential_kind is not CredentialProofKind.HMAC_SHA256:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if authority.algorithm != HMAC_SHA256_ALGORITHM:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if not _metadata_matches(request, authority) or request.credential is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        secret = self._secret_resolver(authority)
        if not secret:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        raw = request.credential.raw_proof
        try:
            supplied = raw.decode("ascii") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        expected = build_hmac_sha256_signature(request, secret)
        if len(supplied) != len(expected) or not hmac.compare_digest(expected, supplied):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return CredentialDecision(
            True,
            None,
            credential_ref_digest=digest_identifier(request.identity.credential_proof.proof_id),
        )


class OpaqueBearerCredentialVerifier:
    """只比较 bearer 的摘要；原 token 从不作为审计结果或异常内容返回。

    摘要比较同样使用恒定时间操作，解析得到的原 token 只在本栈帧存活。
    """
    def __init__(self, *, token_digest_resolver: TokenDigestResolver) -> None:
        self._token_digest_resolver = token_digest_resolver

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        """计算请求 bearer 摘要并与权威期望值比对，不记录原 token。"""
        if authority.credential_kind is not CredentialProofKind.BEARER_OPAQUE:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if authority.algorithm != OPAQUE_BEARER_ALGORITHM:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if not _metadata_matches(request, authority) or request.credential is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        raw = request.credential.raw_proof
        token = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        supplied_digest = f"sha256:{hashlib.sha256(token).hexdigest()}"
        expected_digest = str(self._token_digest_resolver(authority) or "")
        if not expected_digest or not hmac.compare_digest(expected_digest, supplied_digest):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return CredentialDecision(
            True,
            None,
            credential_ref_digest=digest_identifier(request.identity.credential_proof.proof_id),
        )


class CredentialVerifierRegistry:
    """按声明的凭据种类分派验证器；未知/异常 verifier 一律视作无效。

    注册表不做跨种类回退：例如 bearer 不能在 HMAC 验证器失败后尝试其他机制，以免
    造成算法混淆或扩大攻击面的探测反馈。
    """
    def __init__(
        self,
        verifiers: Mapping[CredentialProofKind, object],
    ) -> None:
        self._verifiers = dict(verifiers)

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        """调用匹配验证器并将插件异常、错误返回形态统一失败关闭。"""
        if request.identity is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_MISSING)
        verifier = self._verifiers.get(request.identity.credential_proof.kind)
        if verifier is None or not hasattr(verifier, "verify"):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        try:
            decision = verifier.verify(request=request, authority=authority)
        except Exception:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return decision if isinstance(decision, CredentialDecision) else CredentialDecision(
            False,
            IngressReasonCode.CREDENTIAL_INVALID,
        )


__all__ = [
    "CredentialVerifierRegistry",
    "HMAC_SHA256_ALGORITHM",
    "HmacSha256CredentialVerifier",
    "OpaqueBearerCredentialVerifier",
    "OPAQUE_BEARER_ALGORITHM",
    "build_hmac_sha256_signature",
]
