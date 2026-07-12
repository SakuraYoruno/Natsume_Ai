"""Versioned Edge transport session and fencing contracts.

The lease is deliberately transport-neutral.  A reconnect may renew the same
lease only while presenting its exact lease id and fencing token.  Once a
lease expires, the next owner receives a higher fence and every command bound
to the previous fence is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Any, Mapping

from .embodiment_v1 import EmbodimentCommand


EDGE_SESSION_LEASE_SCHEMA_VERSION = "natsume.edge.session-lease.v1"
EDGE_SESSION_BUSY = "EDGE_SESSION_BUSY"
EDGE_SESSION_RECONNECT_INVALID = "EDGE_SESSION_RECONNECT_INVALID"
EDGE_SESSION_LEASE_EXPIRED = "EDGE_SESSION_LEASE_EXPIRED"


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
        """Only a convenience view; authority checks must use a supplied clock."""

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
    """Return an immutable command bound to the exact live session fence."""

    if lease.schema_version != EDGE_SESSION_LEASE_SCHEMA_VERSION:
        raise ValueError(EDGE_SESSION_RECONNECT_INVALID)
    return replace(
        command,
        session_id=_required_text(lease.session_id, "session_id"),
        lease_id=_required_text(lease.lease_id, "lease_id"),
        fencing_token=int(lease.fencing_token),
    )


def parse_edge_session_lease(raw: Mapping[str, Any] | Any) -> EdgeSessionLease:
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
    "parse_edge_session_lease",
]
