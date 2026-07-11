"""Server-owned policies for the four TASK-0487 write surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from natsume_contracts.identity import CredentialProof, IngressIdentityEnvelope

from .models import SecureIngressRequest


class IngressSurface(str, Enum):
    HOST = "host"
    OBS = "obs"
    GATEWAY = "gateway"
    BRIDGE = "bridge"


@dataclass(frozen=True, slots=True)
class IngressSurfacePolicy:
    surface: IngressSurface
    surface_id: str
    audience: str
    action: str
    required_scopes: tuple[str, ...]


M48B_P0_SURFACE_POLICIES: Mapping[IngressSurface, IngressSurfacePolicy] = MappingProxyType({
    IngressSurface.HOST: IngressSurfacePolicy(
        surface=IngressSurface.HOST,
        surface_id="host.input",
        audience="host",
        action="host:input",
        required_scopes=("host:write",),
    ),
    IngressSurface.OBS: IngressSurfacePolicy(
        surface=IngressSurface.OBS,
        surface_id="obs.command",
        audience="obs",
        action="obs:command",
        required_scopes=("obs:write",),
    ),
    IngressSurface.GATEWAY: IngressSurfacePolicy(
        surface=IngressSurface.GATEWAY,
        surface_id="gateway.webhook",
        audience="gateway",
        action="gateway:ingest",
        required_scopes=("gateway:write",),
    ),
    IngressSurface.BRIDGE: IngressSurfacePolicy(
        surface=IngressSurface.BRIDGE,
        surface_id="bridge.command",
        audience="bridge",
        action="bridge:command",
        required_scopes=("bridge:write",),
    ),
})


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
    """Bind client proof to the immutable policy selected by server code."""

    if not isinstance(surface, IngressSurface):
        raise ValueError("SECURE_INGRESS_SURFACE_INVALID")
    policy = M48B_P0_SURFACE_POLICIES[surface]
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
    "build_surface_request",
]
