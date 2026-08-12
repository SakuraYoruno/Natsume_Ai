"""Secure authority boundary for scheduler HTTP and clock commands.

Clock-triggered authority is deliberately minted through a composition-owned
credential provider.  This module has no built-in principal, key, secret, or
caller-metadata fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from natsume_contracts.identity import (
    CredentialProof,
    IngressIdentityEnvelope,
    PrincipalKind,
)

from host_brain.secure_ingress import (
    SECURE_INGRESS_ENFORCE_REQUIRED,
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
    SecureIngressMode,
    SecureIngressRequest,
    build_surface_request,
)


SCHEDULER_CLOCK_CREDENTIAL_REQUIRED = "SCHEDULER_CLOCK_CREDENTIAL_REQUIRED"
SCHEDULER_CLOCK_CREDENTIAL_INVALID = "SCHEDULER_CLOCK_CREDENTIAL_INVALID"
SCHEDULER_CLOCK_OCCURRENCE_INVALID = "SCHEDULER_CLOCK_OCCURRENCE_INVALID"
SCHEDULER_CLOCK_PATH = "/internal/scheduler/clock"
SCHEDULER_CLOCK_SCHEMA_VERSION = "natsume.scheduler.clock-authority.v1"

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")


# 时钟发生项的签名上下文完全由服务端构造；调度载荷不能自带身份或作用域来扩大权限。
@dataclass(frozen=True, slots=True)
class SchedulerClockSigningContext:
    """Server-built context given to a composition-owned credential provider."""

    task_id: str
    definition_revision: int
    scheduled_at_epoch_ms: int
    received_at_epoch_ms: int
    request_id: str
    audience: str
    surface: str
    action: str
    required_scopes: tuple[str, ...]
    normalized_path: str
    body_sha256: str


class SchedulerClockCredentialProvider(Protocol):
    """Composition port backed by a registered service identity and credential."""

    def identity_for(
        self,
        *,
        context: SchedulerClockSigningContext,
    ) -> IngressIdentityEnvelope: ...

    def issue_proof(
        self,
        *,
        request: SecureIngressRequest,
    ) -> CredentialProof: ...


class SchedulerCommandIngress:
    """Evaluate scheduler commands without exposing proof material."""

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        clock_credential_provider: SchedulerClockCredentialProvider | None = None,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be SecureIngressAuthorityEvaluator")
        self._evaluator = evaluator
        self._clock_credential_provider = clock_credential_provider
        self._http = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.SCHEDULER_ADMIN,
        )

    def evaluate_http(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int,
    ) -> SecureIngressAuthority:
        """Bind scheduler HTTP requests to the server-owned admin policy."""

        return self._http.evaluate(
            request=request,
            raw_body=raw_body,
            received_at_ms=received_at_ms,
        )

    def evaluate_clock_occurrence(
        self,
        *,
        task_id: str,
        definition_revision: int,
        scheduled_at_epoch_ms: int,
        payload: Mapping[str, object],
        received_at_ms: int,
    ) -> SecureIngressAuthority:
        """Verify a clock occurrence using only an injected service credential."""

        # 先固定发生项的规范字节，签名、幂等键和审计摘要都绑定同一表示，避免等价 JSON 产生不同授权。
        raw_body = _canonical_clock_body(
            task_id=task_id,
            definition_revision=definition_revision,
            scheduled_at_epoch_ms=scheduled_at_epoch_ms,
            payload=payload,
        )
        if raw_body is None or type(received_at_ms) is not int or received_at_ms < 0:
            return self._reject_clock(
                reason_code=SCHEDULER_CLOCK_OCCURRENCE_INVALID,
                raw_body=raw_body or b"",
                received_at_ms=received_at_ms,
            )

        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        policy = SECURE_INGRESS_SURFACE_POLICIES[IngressSurface.SCHEDULER_ADMIN]
        occurrence_ref = _clock_occurrence_ref(
            task_id=task_id,
            definition_revision=definition_revision,
            scheduled_at_epoch_ms=scheduled_at_epoch_ms,
        )
        request_id = f"scheduler.clock.request.{occurrence_ref}"
        context = SchedulerClockSigningContext(
            task_id=task_id,
            definition_revision=definition_revision,
            scheduled_at_epoch_ms=scheduled_at_epoch_ms,
            received_at_epoch_ms=received_at_ms,
            request_id=request_id,
            audience=policy.audience,
            surface=policy.surface_id,
            action=policy.action,
            required_scopes=policy.required_scopes,
            normalized_path=SCHEDULER_CLOCK_PATH,
            body_sha256=body_sha256,
        )
        provider = self._clock_credential_provider
        if provider is None:
            # 时钟不是系统内建超权主体；生产组合未注入服务凭据时必须拒绝。
            return self._reject_clock(
                reason_code=SCHEDULER_CLOCK_CREDENTIAL_REQUIRED,
                raw_body=raw_body,
                received_at_ms=received_at_ms,
            )

        try:
            # Provider 仅接收服务器构造的上下文，并且证明元数据必须与返回身份信封严格一致。
            identity = provider.identity_for(context=context)
            if not _valid_clock_identity(identity=identity, context=context):
                raise ValueError(SCHEDULER_CLOCK_CREDENTIAL_INVALID)
            unsigned = build_surface_request(
                surface=IngressSurface.SCHEDULER_ADMIN,
                identity=identity,
                credential=None,
                method="POST",
                normalized_path=SCHEDULER_CLOCK_PATH,
                idempotency_key=f"scheduler.clock.idem.{occurrence_ref}",
                nonce=f"scheduler.clock.nonce.{occurrence_ref}",
                raw_body=raw_body,
                received_at_epoch_ms=received_at_ms,
                transport_key=f"principal:{identity.principal.principal_id}",
                correlation_id=f"scheduler.clock.{occurrence_ref}",
            )
            credential = provider.issue_proof(request=unsigned)
            if not isinstance(credential, CredentialProof):
                raise TypeError(SCHEDULER_CLOCK_CREDENTIAL_INVALID)
            if credential.metadata != identity.credential_proof:
                raise ValueError(SCHEDULER_CLOCK_CREDENTIAL_INVALID)
            request = build_surface_request(
                surface=IngressSurface.SCHEDULER_ADMIN,
                identity=identity,
                credential=credential,
                method=unsigned.method,
                normalized_path=unsigned.normalized_path,
                idempotency_key=unsigned.idempotency_key,
                nonce=unsigned.nonce,
                raw_body=unsigned.raw_body,
                received_at_epoch_ms=unsigned.received_at_epoch_ms,
                transport_key=unsigned.transport_key,
                correlation_id=unsigned.correlation_id,
            )
        except Exception:
            return self._reject_clock(
                reason_code=SCHEDULER_CLOCK_CREDENTIAL_INVALID,
                raw_body=raw_body,
                received_at_ms=received_at_ms,
            )
        return self._evaluator.evaluate(request)

    def _reject_clock(
        self,
        *,
        reason_code: str,
        raw_body: bytes,
        received_at_ms: object,
    ) -> SecureIngressAuthority:
        # 拒绝路径同样保留载荷摘要及审计引用，但从不回显或持久化原始时钟输入。
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        if self._evaluator.mode is not SecureIngressMode.ENFORCE:
            return SecureIngressAuthority.denied(
                SECURE_INGRESS_ENFORCE_REQUIRED,
                body_sha256=body_sha256,
            )
        if type(received_at_ms) is not int or received_at_ms < 0:
            return SecureIngressAuthority.denied(
                reason_code,
                body_sha256=body_sha256,
            )
        denied = self._evaluator.reject_unparsed(
            surface=IngressSurface.SCHEDULER_ADMIN,
            method="POST",
            normalized_path=SCHEDULER_CLOCK_PATH,
            received_at_ms=received_at_ms,
            transport_key="scheduler:clock:unverified",
            body_sha256=body_sha256,
        )
        return SecureIngressAuthority(
            allowed=False,
            duplicate=False,
            reason_code=reason_code,
            audit_ref=denied.audit_ref,
            body_sha256=body_sha256,
        )


def _valid_clock_identity(
    *,
    identity: object,
    context: SchedulerClockSigningContext,
) -> bool:
    return bool(
        isinstance(identity, IngressIdentityEnvelope)
        and identity.principal.kind is PrincipalKind.SERVICE
        and identity.request_id == context.request_id
        and identity.audience == context.audience
        and identity.requested_scopes == context.required_scopes
    )


def _canonical_clock_body(
    *,
    task_id: object,
    definition_revision: object,
    scheduled_at_epoch_ms: object,
    payload: object,
) -> bytes | None:
    # 仅接纳可稳定序列化的发生项；畸形标识、时间或负载在凭据签发之前就被隔离。
    if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
        return None
    if type(definition_revision) is not int or definition_revision < 1:
        return None
    if type(scheduled_at_epoch_ms) is not int or scheduled_at_epoch_ms < 0:
        return None
    if not isinstance(payload, Mapping) or not all(
        isinstance(key, str) for key in payload
    ):
        return None
    try:
        return json.dumps(
            {
                "definition_revision": definition_revision,
                "payload": dict(payload),
                "scheduled_at_epoch_ms": scheduled_at_epoch_ms,
                "schema_version": SCHEDULER_CLOCK_SCHEMA_VERSION,
                "task_id": task_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None


def _clock_occurrence_ref(
    *,
    task_id: str,
    definition_revision: int,
    scheduled_at_epoch_ms: int,
) -> str:
    """Identify a schedule occurrence independently from its command payload."""

    # 发生项标识故意不含业务 payload：同一计划时刻的重复投递必须落在同一幂等域。
    material = json.dumps(
        {
            "definition_revision": definition_revision,
            "scheduled_at_epoch_ms": scheduled_at_epoch_ms,
            "schema_version": SCHEDULER_CLOCK_SCHEMA_VERSION,
            "task_id": task_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:48]


__all__ = [
    "SCHEDULER_CLOCK_CREDENTIAL_INVALID",
    "SCHEDULER_CLOCK_CREDENTIAL_REQUIRED",
    "SCHEDULER_CLOCK_OCCURRENCE_INVALID",
    "SCHEDULER_CLOCK_PATH",
    "SCHEDULER_CLOCK_SCHEMA_VERSION",
    "SchedulerClockCredentialProvider",
    "SchedulerClockSigningContext",
    "SchedulerCommandIngress",
]
