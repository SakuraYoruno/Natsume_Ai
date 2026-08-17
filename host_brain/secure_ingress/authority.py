"""供 Secure Ingress 表面复用的无秘密权威适配器。

模块拥有通用 HTTP 信封/证明解析路径。表面适配器只选择 ``IngressSurface``；调用方不能在
运行时提供 action、audience 或 required-scope 策略，避免传输层输入改变授权定义。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from typing import Any

from natsume_contracts.identity import CredentialProof, parse_identity_envelope

from .adapter import SecureIngressAdapter, SecureIngressMode
from .models import IngressReasonCode, SecureIngressDecision, SecureIngressRequest
from .service import SecureIngressService
from .surfaces import (
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
    build_surface_request,
)


SECURE_INGRESS_ENFORCE_REQUIRED = "SECURE_INGRESS_ENFORCE_REQUIRED"
SECURE_INGRESS_HTTP_CONTRACT_INVALID = "SECURE_INGRESS_HTTP_CONTRACT_INVALID"

_IDENTITY_HEADER = b"x-natsume-identity"
_PROOF_HEADER = b"x-natsume-proof"
_IDEMPOTENCY_HEADER = b"x-natsume-idempotency-key"
_NONCE_HEADER = b"x-natsume-nonce"
_CORRELATION_HEADER = b"x-natsume-correlation-id"
_SECURITY_HEADERS = frozenset(
    {
        _IDENTITY_HEADER,
        _PROOF_HEADER,
        _IDEMPOTENCY_HEADER,
        _NONCE_HEADER,
        _CORRELATION_HEADER,
    }
)
_REQUIRED_HEADERS = frozenset(
    {
        _IDENTITY_HEADER,
        _PROOF_HEADER,
        _IDEMPOTENCY_HEADER,
        _NONCE_HEADER,
    }
)


@dataclass(frozen=True, slots=True)
class SecureIngressAuthority:
    """可跨入命令准入层的无秘密权威材料。

    正常允许与幂等重复分开表达：重复项只能进入耐久重放路径，不能被当成新命令再次执行。
    """

    allowed: bool
    duplicate: bool
    reason_code: str
    request_id: str | None = None
    principal_id: str | None = None
    principal_kind: str | None = None
    tenant_id: str | None = None
    claim_ref: str | None = None
    audit_ref: str | None = None
    body_sha256: str | None = None
    existing_result_ref: str | None = None

    @property
    def admissible(self) -> bool:
        """允许或规范重复均可进入后续耐久处理；后者不代表新的执行许可。"""

        return bool(self.allowed or self.duplicate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "duplicate": self.duplicate,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "principal_id": self.principal_id,
            "principal_kind": self.principal_kind,
            "tenant_id": self.tenant_id,
            "claim_ref": self.claim_ref,
            "audit_ref": self.audit_ref,
            "body_sha256": self.body_sha256,
            "existing_result_ref": self.existing_result_ref,
        }

    @classmethod
    def denied(
        cls,
        reason_code: str,
        *,
        body_sha256: str | None = None,
    ) -> "SecureIngressAuthority":
        return cls(
            allowed=False,
            duplicate=False,
            reason_code=str(reason_code or SECURE_INGRESS_HTTP_CONTRACT_INVALID),
            body_sha256=str(body_sha256 or "").strip() or None,
        )


class SecureIngressAuthorityEvaluator:
    """评估已构造请求，只返回脱敏权威结果。

    此门面要求 ENFORCE 模式；SHADOW/OFF 即使底层服务可评估，也不会将结果升级成命令准入。
    """

    def __init__(
        self,
        *,
        service: SecureIngressService,
        mode: SecureIngressMode,
    ) -> None:
        if not isinstance(service, SecureIngressService):
            raise TypeError("service must be SecureIngressService")
        if not isinstance(mode, SecureIngressMode):
            raise TypeError("mode must be SecureIngressMode")
        self._service = service
        self._adapter = SecureIngressAdapter(service=service, mode=mode)

    @property
    def mode(self) -> SecureIngressMode:
        """暴露冻结的适配模式，供 HTTP 入口在读取不可信头之前先失败关闭。"""
        return self._adapter.mode

    def evaluate(self, request: SecureIngressRequest) -> SecureIngressAuthority:
        """在强制模式下将 SecureIngressDecision 投影为不含凭据的命令权威。"""
        body_sha256 = request.body_sha256
        if self.mode is not SecureIngressMode.ENFORCE:
            return SecureIngressAuthority.denied(
                SECURE_INGRESS_ENFORCE_REQUIRED,
                body_sha256=body_sha256,
            )
        outcome = self._adapter.evaluate(request)
        decision = outcome.decision
        if decision is None:
            return SecureIngressAuthority.denied(
                SECURE_INGRESS_ENFORCE_REQUIRED,
                body_sha256=body_sha256,
            )
        return self._authority_from_decision(
            request=request,
            decision=decision,
            permit_execution=outcome.permit_execution,
        )

    def reject_unparsed(
        self,
        *,
        surface: IngressSurface,
        method: str,
        normalized_path: str,
        received_at_ms: int,
        transport_key: str,
        body_sha256: str,
        correlation_id: str | None = None,
    ) -> SecureIngressAuthority:
        """将未能解析的 HTTP 尝试送入最小限速/审计路径并返回拒绝。"""
        if self.mode is not SecureIngressMode.ENFORCE:
            return SecureIngressAuthority.denied(
                SECURE_INGRESS_ENFORCE_REQUIRED,
                body_sha256=body_sha256,
            )
        policy = SECURE_INGRESS_SURFACE_POLICIES[surface]
        decision = self._service.reject_unparsed_attempt(
            transport_key=str(transport_key or "http:unknown"),
            surface=policy.surface_id,
            method=str(method or "POST").upper(),
            normalized_path=str(normalized_path or "/"),
            action=policy.action,
            required_scopes=policy.required_scopes,
            received_at_epoch_ms=int(received_at_ms),
            correlation_id=str(correlation_id or "").strip() or None,
        )
        return SecureIngressAuthority(
            allowed=False,
            duplicate=False,
            reason_code=decision.reason_code.value,
            audit_ref=decision.audit_ref,
            body_sha256=body_sha256,
        )

    @staticmethod
    def _authority_from_decision(
        *,
        request: SecureIngressRequest,
        decision: SecureIngressDecision,
        permit_execution: bool,
    ) -> SecureIngressAuthority:
        principal_kind = (
            request.identity.principal.kind.value
            if request.identity is not None
            else None
        )
        tenant_id = (
            request.identity.principal.tenant_id
            if request.identity is not None
            else None
        )
        duplicate = decision.reason_code is IngressReasonCode.IDEMPOTENCY_DUPLICATE
        if duplicate:
            canonical_request_id = str(
                decision.canonical_request_id or ""
            ).strip()
            canonical_claim_ref = str(decision.claim_ref or "").strip()
            canonical_audit_ref = str(
                decision.canonical_audit_ref or ""
            ).strip()
            canonical_body_sha256 = str(
                decision.canonical_body_sha256 or ""
            ).strip()
            if not all(
                (
                    canonical_request_id,
                    canonical_claim_ref,
                    canonical_audit_ref,
                    canonical_body_sha256,
                )
            ):
                return SecureIngressAuthority(
                    allowed=False,
                    duplicate=False,
                    reason_code=IngressReasonCode.INTERNAL_ERROR.value,
                    principal_id=request.principal_id,
                    principal_kind=principal_kind,
                    tenant_id=tenant_id,
                    audit_ref=decision.audit_ref,
                    body_sha256=request.body_sha256,
                )
            return SecureIngressAuthority(
                allowed=False,
                duplicate=True,
                reason_code=decision.reason_code.value,
                request_id=canonical_request_id,
                principal_id=request.principal_id,
                principal_kind=principal_kind,
                tenant_id=tenant_id,
                claim_ref=canonical_claim_ref,
                audit_ref=canonical_audit_ref,
                body_sha256=canonical_body_sha256,
                existing_result_ref=decision.existing_result_ref,
            )
        return SecureIngressAuthority(
            allowed=bool(permit_execution),
            duplicate=False,
            reason_code=decision.reason_code.value,
            request_id=request.request_id,
            principal_id=request.principal_id,
            principal_kind=principal_kind,
            tenant_id=tenant_id,
            claim_ref=decision.claim_ref,
            audit_ref=decision.audit_ref,
            body_sha256=request.body_sha256,
            existing_result_ref=decision.existing_result_ref,
        )


class SecureIngressHttpAuthorityAdapter:
    """解析规范安全头并评估一个服务端拥有的表面。

    重复安全头、缺失必需头或身份解码失败均不尝试部分恢复；适配器转而使用不可信传输键
    记录和限速该尝试，防止解析失败成为不可见入口。
    """

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        surface: IngressSurface,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be SecureIngressAuthorityEvaluator")
        if not isinstance(surface, IngressSurface):
            raise TypeError("surface must be IngressSurface")
        self._evaluator = evaluator
        self._surface = surface

    def evaluate(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int,
    ) -> SecureIngressAuthority:
        """从 HTTP 请求构造受策略冻结的信封，再交给权威评估器。"""
        body = bytes(raw_body)
        body_sha256 = hashlib.sha256(body).hexdigest()
        if self._evaluator.mode is not SecureIngressMode.ENFORCE:
            return SecureIngressAuthority.denied(
                SECURE_INGRESS_ENFORCE_REQUIRED,
                body_sha256=body_sha256,
            )
        method = str(getattr(request, "method", "POST") or "POST").upper()
        path = str(
            getattr(getattr(request, "url", None), "path", "")
            or SECURE_INGRESS_SURFACE_POLICIES[self._surface].default_path
        )
        correlation_id: str | None = None
        try:
            headers = _single_security_headers(request)
            correlation_id = headers.get(_CORRELATION_HEADER.decode("ascii")) or None
            identity = parse_identity_envelope(
                _decode_identity_header(headers[_IDENTITY_HEADER.decode("ascii")])
            )
            credential = CredentialProof(
                metadata=identity.credential_proof,
                raw_proof=headers[_PROOF_HEADER.decode("ascii")],
            )
            ingress_request = build_surface_request(
                surface=self._surface,
                identity=identity,
                credential=credential,
                method=method,
                normalized_path=path,
                idempotency_key=headers[_IDEMPOTENCY_HEADER.decode("ascii")],
                nonce=headers[_NONCE_HEADER.decode("ascii")],
                raw_body=body,
                received_at_epoch_ms=int(received_at_ms),
                transport_key=f"principal:{identity.principal.principal_id}",
                correlation_id=correlation_id,
            )
        except Exception:
            return self._evaluator.reject_unparsed(
                surface=self._surface,
                method=method,
                normalized_path=path,
                received_at_ms=int(received_at_ms),
                transport_key=_untrusted_transport_key(request),
                body_sha256=body_sha256,
                correlation_id=correlation_id,
            )
        return self._evaluator.evaluate(ingress_request)


def _single_security_headers(request: Any) -> dict[str, str]:
    """取得唯一的安全头；同名多值拒绝，避免代理与应用解释不一致。"""
    raw_headers = list((getattr(request, "scope", {}) or {}).get("headers") or ())
    selected: dict[bytes, bytes] = {}
    for raw_name, raw_value in raw_headers:
        name = bytes(raw_name).strip().lower()
        if name not in _SECURITY_HEADERS:
            continue
        if name in selected:
            raise ValueError(SECURE_INGRESS_HTTP_CONTRACT_INVALID)
        selected[name] = bytes(raw_value).strip()
    if not _REQUIRED_HEADERS.issubset(selected):
        raise ValueError(SECURE_INGRESS_HTTP_CONTRACT_INVALID)
    if any(not selected[name] for name in _REQUIRED_HEADERS):
        raise ValueError(SECURE_INGRESS_HTTP_CONTRACT_INVALID)
    return {
        name.decode("ascii"): value.decode("ascii")
        for name, value in selected.items()
    }


def _decode_identity_header(value: str) -> bytes:
    """严格解码有长度上限的 URL-safe Base64 身份信封。"""
    encoded = str(value or "").strip()
    if not encoded or len(encoded) > 16_384:
        raise ValueError(SECURE_INGRESS_HTTP_CONTRACT_INVALID)
    padding = "=" * (-len(encoded) % 4)
    return base64.b64decode(
        encoded + padding,
        altchars=b"-_",
        validate=True,
    )


def _untrusted_transport_key(request: Any) -> str:
    """为未解析请求生成仅供尝试限速的运输来源键，绝不把它视作主体身份。"""
    host = str(getattr(getattr(request, "client", None), "host", "") or "").strip()
    return f"http:{host or 'unknown'}"


__all__ = [
    "SECURE_INGRESS_ENFORCE_REQUIRED",
    "SECURE_INGRESS_HTTP_CONTRACT_INVALID",
    "SecureIngressAuthority",
    "SecureIngressAuthorityEvaluator",
    "SecureIngressHttpAuthorityAdapter",
]
