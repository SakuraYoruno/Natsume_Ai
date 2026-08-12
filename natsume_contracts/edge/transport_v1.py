"""Edge 传输会话与围栏令牌的版本化契约。

租约不绑定具体传输：重连只能携带完全相同的租约 ID 与围栏令牌续期；运行时替换会
原子推进围栏并得到新租约。旧租约失效后，其命令必须被拒绝，避免网络延迟或旧进程
在新所有者接管设备后继续产生物理副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Any, Mapping, Protocol

from .embodiment_v1 import EmbodimentCommand


EDGE_SESSION_LEASE_SCHEMA_VERSION = "natsume.edge.session-lease.v1"
EDGE_SESSION_BUSY = "EDGE_SESSION_BUSY"
EDGE_SESSION_RECONNECT_INVALID = "EDGE_SESSION_RECONNECT_INVALID"
EDGE_SESSION_LEASE_EXPIRED = "EDGE_SESSION_LEASE_EXPIRED"


@dataclass(frozen=True, slots=True)
class EdgeTransportResult:
    """Transport-neutral receipt returned by a typed Host -> Edge port."""

    success: bool
    status: str
    reason_code: str | None
    detail: str
    event: Mapping[str, Any]
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": bool(self.success),
            "status": str(self.status),
            "reason_code": self.reason_code,
            "detail": str(self.detail),
            "event": dict(self.event),
            "latency_ms": int(self.latency_ms),
        }


class EdgeTransportPort(Protocol):
    """一次、非自动重试的 Host→Edge 提交边界。

    重试策略必须在能理解幂等键和收据语义的上层制定；传输端口自行重试可能把
    “已送达但响应丢失”误当成可安全重复的设备命令。
    """

    async def submit(
        self,
        *,
        path: str,
        payload: Mapping[str, Any],
        request_id: str,
        timeout_ms: int,
    ) -> EdgeTransportResult: ...


@dataclass(frozen=True, slots=True)
class EdgeSessionLease:
    session_id: str
    lease_id: str
    owner_id: str
    fencing_token: int
    issued_at_ms: int
    expires_at_ms: int
    schema_version: str = EDGE_SESSION_LEASE_SCHEMA_VERSION

    @property
    def expired(self) -> bool:
        """仅供展示；授权判断必须使用调用方提供且可控的时钟。"""

        from .v1 import now_ms

        return self.expires_at_ms <= now_ms()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "owner_id": self.owner_id,
            "fencing_token": int(self.fencing_token),
            "issued_at_ms": int(self.issued_at_ms),
            "expires_at_ms": int(self.expires_at_ms),
        }


def stable_lease_id(*, session_id: str, owner_id: str, fencing_token: int) -> str:
    """从会话、所有者和单调围栏导出租约 ID，使篡改能在解析期被发现。"""
    session = _required_text(session_id, "session_id")
    owner = _required_text(owner_id, "owner_id")
    fence = int(fencing_token)
    if fence < 1:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    digest = hashlib.sha256(
        f"{EDGE_SESSION_LEASE_SCHEMA_VERSION}\n{session}\n{owner}\n{fence}".encode("utf-8")
    ).hexdigest()
    return f"edge-lease-{digest[:32]}"


def bind_command_to_lease(
    command: EmbodimentCommand,
    lease: EdgeSessionLease,
) -> EmbodimentCommand:
    """返回绑定当前精确围栏的不可变命令，禁止调用方保留旧围栏字段。"""

    if lease.schema_version != EDGE_SESSION_LEASE_SCHEMA_VERSION:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    return replace(
        command,
        session_id=_required_text(lease.session_id, "session_id"),
        lease_id=_required_text(lease.lease_id, "lease_id"),
        fencing_token=int(lease.fencing_token),
    )


def parse_edge_session_lease(raw: Mapping[str, Any] | Any) -> EdgeSessionLease:
    """恢复租约并重算 ID；格式正确但身份不一致的载荷同样拒绝。"""
    if not isinstance(raw, Mapping):
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    if str(raw.get("schema_version") or "") != EDGE_SESSION_LEASE_SCHEMA_VERSION:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    lease = EdgeSessionLease(
        session_id=_required_text(raw.get("session_id"), "session_id"),
        lease_id=_required_text(raw.get("lease_id"), "lease_id"),
        owner_id=_required_text(raw.get("owner_id"), "owner_id"),
        fencing_token=int(raw.get("fencing_token") or 0),
        issued_at_ms=int(raw.get("issued_at_ms") or 0),
        expires_at_ms=int(raw.get("expires_at_ms") or 0),
    )
    if lease.fencing_token < 1 or lease.expires_at_ms <= lease.issued_at_ms:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    expected = stable_lease_id(
        session_id=lease.session_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
    )
    if lease.lease_id != expected:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    return lease


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{EDGE_SESSION_RECONNECT_INVALID}:{field}")
    return text


__all__ = [
    "EDGE_SESSION_LEASE_SCHEMA_VERSION", "EDGE_SESSION_BUSY",
    "EDGE_SESSION_RECONNECT_INVALID", "EDGE_SESSION_LEASE_EXPIRED",
    "EdgeSessionLease", "stable_lease_id", "bind_command_to_lease",
    "parse_edge_session_lease", "EdgeTransportPort", "EdgeTransportResult",
]
