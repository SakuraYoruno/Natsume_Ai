"""由服务端拥有的认证写入/命令表面策略。

客户端只选择受限枚举，不能自报 action、audience 或 scope；构造请求时由此静态表冻结
对应授权坐标，避免把路由参数变成权限提升入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from natsume_contracts.identity import CredentialProof, IngressIdentityEnvelope

from .models import SecureIngressRequest


class IngressSurface(str, Enum):
    """需要 Secure Ingress 保护的已知写入或控制面表面。"""

    HOST = "host"
    COMPANION_CONTACT = "companion_contact"
    COMPANION_PROFILE = "companion_profile"
    COMPANION_PROFILE_ROLLBACK = "companion_profile_rollback"
    COMPANION_FEEDBACK = "companion_feedback"
    TOOL = "tool"
    OBS = "obs"
    GATEWAY = "gateway"
    BRIDGE = "bridge"
    INTERNAL_AGENT = "internal_agent"
    REVIEW_OPERATOR = "review_operator"
    SCHEDULER_ADMIN = "scheduler_admin"


@dataclass(frozen=True, slots=True)
class IngressSurfacePolicy:
    """一个表面的不可变服务端授权坐标和默认路由。"""

    surface: IngressSurface
    surface_id: str
    audience: str
    action: str
    required_scopes: tuple[str, ...]
    default_path: str


SECURE_INGRESS_SURFACE_POLICIES: Mapping[
    IngressSurface,
    IngressSurfacePolicy,
] = MappingProxyType({
    IngressSurface.HOST: IngressSurfacePolicy(
        surface=IngressSurface.HOST,
        surface_id="host.input",
        audience="host",
        action="host:input",
        required_scopes=("host:write",),
        default_path="/v1/host/input",
    ),
    IngressSurface.COMPANION_CONTACT: IngressSurfacePolicy(
        surface=IngressSurface.COMPANION_CONTACT,
        surface_id="host.companion-contact",
        audience="host",
        action="companion:contact:write",
        required_scopes=("companion:contact:write",),
        default_path="/v1/digital-life/relationship/proactive-permission",
    ),
    IngressSurface.COMPANION_PROFILE: IngressSurfacePolicy(
        surface=IngressSurface.COMPANION_PROFILE,
        surface_id="host.companion-profile",
        audience="host",
        action="companion:profile:write",
        required_scopes=("companion:profile:write",),
        default_path="/v1/digital-life/relationship/companion-profile",
    ),
    IngressSurface.COMPANION_PROFILE_ROLLBACK: IngressSurfacePolicy(
        surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        surface_id="host.companion-profile-rollback",
        audience="host",
        action="companion:profile:rollback",
        required_scopes=("companion:profile:rollback",),
        default_path="/v1/digital-life/relationship/companion-profile/rollback",
    ),
    IngressSurface.COMPANION_FEEDBACK: IngressSurfacePolicy(
        surface=IngressSurface.COMPANION_FEEDBACK,
        surface_id="host.companion-feedback",
        audience="host",
        action="companion:feedback:write",
        required_scopes=("companion:feedback:write",),
        default_path="/v1/digital-life/relationship/response-feedback",
    ),
    IngressSurface.TOOL: IngressSurfacePolicy(
        surface=IngressSurface.TOOL,
        surface_id="host.tool",
        audience="host",
        action="host:tool",
        required_scopes=("host:write",),
        default_path="/tools/execute",
    ),
    IngressSurface.OBS: IngressSurfacePolicy(
        surface=IngressSurface.OBS,
        surface_id="obs.command",
        audience="obs",
        action="obs:command",
        required_scopes=("obs:write",),
        default_path="/v1/obs/commands",
    ),
    IngressSurface.GATEWAY: IngressSurfacePolicy(
        surface=IngressSurface.GATEWAY,
        surface_id="gateway.webhook",
        audience="gateway",
        action="gateway:ingest",
        required_scopes=("gateway:write",),
        default_path="/v1/gateway/webhook",
    ),
    IngressSurface.BRIDGE: IngressSurfacePolicy(
        surface=IngressSurface.BRIDGE,
        surface_id="bridge.command",
        audience="bridge",
        action="bridge:command",
        required_scopes=("bridge:write",),
        default_path="/v1/bridge/command",
    ),
    IngressSurface.INTERNAL_AGENT: IngressSurfacePolicy(
        surface=IngressSurface.INTERNAL_AGENT,
        surface_id="internal.agent",
        audience="internal",
        action="internal:agent:execute",
        required_scopes=("internal:agent:execute",),
        default_path="/internal/codex/work-orders/execute",
    ),
    IngressSurface.REVIEW_OPERATOR: IngressSurfacePolicy(
        surface=IngressSurface.REVIEW_OPERATOR,
        surface_id="review.operator",
        audience="internal",
        action="review:decide",
        required_scopes=("review:decide",),
        default_path="/internal/codex/reviews",
    ),
    IngressSurface.SCHEDULER_ADMIN: IngressSurfacePolicy(
        surface=IngressSurface.SCHEDULER_ADMIN,
        surface_id="scheduler.admin",
        audience="scheduler",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        default_path="/scheduler/commands",
    ),
})

# 为 TASK-0487 消费者保留的兼容别名；新组合代码使用上方不绑定生命周期的名称。
M48B_P0_SURFACE_POLICIES = SECURE_INGRESS_SURFACE_POLICIES


def build_surface_request(
    *,
    surface: IngressSurface,
    identity: IngressIdentityEnvelope | None,
    credential: CredentialProof | None,
    method: str,
    normalized_path: str,
    idempotency_key: str,
    nonce: str,
    raw_body: bytes,
    received_at_epoch_ms: int,
    transport_key: str,
    correlation_id: str | None = None,
) -> SecureIngressRequest:
    """将客户端证明绑定到服务端选择的不可变表面策略。

    身份信封的受众必须与策略一致；动作和所需范围从策略复制而非接受调用方参数，令后续
    签名、限流和幂等声明都基于同一份 Host 权威定义。
    """

    if not isinstance(surface, IngressSurface):
        raise ValueError("SECURE_INGRESS_SURFACE_INVALID")
    policy = SECURE_INGRESS_SURFACE_POLICIES[surface]
    if identity is not None and identity.audience != policy.audience:
        raise ValueError("SECURE_INGRESS_AUDIENCE_MISMATCH")
    return SecureIngressRequest(
        identity=identity,
        credential=credential,
        surface=policy.surface_id,
        method=method,
        normalized_path=normalized_path,
        action=policy.action,
        required_scopes=policy.required_scopes,
        idempotency_key=idempotency_key,
        nonce=nonce,
        raw_body=raw_body,
        received_at_epoch_ms=received_at_epoch_ms,
        transport_key=transport_key,
        correlation_id=correlation_id,
    )


__all__ = [
    "IngressSurface",
    "IngressSurfacePolicy",
    "M48B_P0_SURFACE_POLICIES",
    "SECURE_INGRESS_SURFACE_POLICIES",
    "build_surface_request",
]
