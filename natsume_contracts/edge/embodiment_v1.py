"""Host→Edge 物理具身命令的版本化契约。

Host 可以生成并提交命令，但只有 Edge 能把命令解释为设备副作用。授权引用、租约
和围栏字段因此都是命令语义的一部分，而不能由设备适配器自行补全或忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol

from .v1 import now_ms


EMBODIMENT_COMMAND_SCHEMA_VERSION = "natsume.edge.embodiment-command.v1"
EMBODIMENT_RECEIPT_SCHEMA_VERSION = "natsume.edge.embodiment-receipt.v1"
EMBODIMENT_COMMAND_INVALID = "EMBODIMENT_COMMAND_INVALID"
EMBODIMENT_COMMAND_EXPIRED = "EMBODIMENT_COMMAND_EXPIRED"
EMBODIMENT_COMMAND_DUPLICATE = "EMBODIMENT_COMMAND_DUPLICATE"
EMBODIMENT_COMMAND_CONFLICT = "EMBODIMENT_COMMAND_CONFLICT"
EMBODIMENT_COMMAND_IN_DOUBT = "EMBODIMENT_COMMAND_IN_DOUBT"
# Receipt queries now carry the lease holder identity as well as the mutable
# lease fence.  This is deliberately a new schema revision: accepting a v1
# query would leave the Edge unable to prove that the caller is the durable
# command authority owner.
EMBODIMENT_RECEIPT_QUERY_SCHEMA_VERSION = "natsume.edge.embodiment-receipt-query.v2"
EMBODIMENT_RECEIPT_QUERY_INVALID = "EMBODIMENT_RECEIPT_QUERY_INVALID"
EMBODIMENT_RECEIPT_NOT_FOUND = "EMBODIMENT_RECEIPT_NOT_FOUND"
EMBODIMENT_LEASE_REQUIRED = "EMBODIMENT_LEASE_REQUIRED"
EMBODIMENT_LEASE_INVALID = "EMBODIMENT_LEASE_INVALID"
EMBODIMENT_LEASE_EXPIRED = "EMBODIMENT_LEASE_EXPIRED"
EMBODIMENT_FENCE_STALE = "EMBODIMENT_FENCE_STALE"
EMBODIMENT_KIND_VOICE = "voice"
EMBODIMENT_KIND_AVATAR = "avatar"
EMBODIMENT_KIND_MATEENGINE = "mateengine"
EMBODIMENT_KIND_DESKTOP = "desktop"
EMBODIMENT_KINDS = frozenset(
    {
        EMBODIMENT_KIND_VOICE,
        EMBODIMENT_KIND_AVATAR,
        EMBODIMENT_KIND_MATEENGINE,
        EMBODIMENT_KIND_DESKTOP,
    }
)


@dataclass(frozen=True, slots=True)
class EmbodimentCommand:
    """一次具身副作用的可传输描述，绑定授权、截止时间与幂等身份。"""
    command_id: str
    kind: str
    action: str
    payload: dict[str, Any]
    authority_ref: str
    grant_ref: str
    deadline_ms: int
    idempotency_key: str
    issued_at_ms: int
    request_id: str
    session_id: str = ""
    lease_id: str = ""
    fencing_token: int = 0
    schema_version: str = EMBODIMENT_COMMAND_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "kind": self.kind,
            "action": self.action,
            "payload": dict(self.payload),
            "authority_ref": self.authority_ref,
            "grant_ref": self.grant_ref,
            "deadline_ms": int(self.deadline_ms),
            "idempotency_key": self.idempotency_key,
            "issued_at_ms": int(self.issued_at_ms),
            "request_id": self.request_id,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "fencing_token": int(self.fencing_token),
        }


@dataclass(frozen=True, slots=True)
class EmbodimentReceipt:
    """Edge 对命令接收/完成状态的持久化收据。

    ``success`` 将可接受的异步中间状态统一为成功语义；调用方仍应结合 ``status``
    和收据查询决定是否已经产生最终设备效果。
    """
    command_id: str
    idempotency_key: str
    accepted: bool
    status: str
    reason_code: str | None
    detail: str
    received_at_ms: int
    completed_at_ms: int | None = None
    event: dict[str, Any] | None = None
    receipt_ref: str | None = None
    schema_version: str = EMBODIMENT_RECEIPT_SCHEMA_VERSION

    @property
    def success(self) -> bool:
        return self.accepted and self.status.upper() in {
            "ACCEPTED", "APPLIED", "COMPLETED", "QUEUED", "DUPLICATE", "SENT",
        }

    @property
    def latency_ms(self) -> int:
        if self.completed_at_ms is None:
            return 0
        return max(0, int(self.completed_at_ms) - int(self.received_at_ms))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "accepted": bool(self.accepted),
            "success": self.success,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "received_at_ms": int(self.received_at_ms),
            "completed_at_ms": self.completed_at_ms,
            "latency_ms": self.latency_ms,
            "event": dict(self.event or {}),
            "receipt_ref": self.receipt_ref,
        }


@dataclass(frozen=True, slots=True)
class EmbodimentReceiptQuery:
    """A read-only, lease-fenced request for one durable Edge receipt.

    The query deliberately binds both the stable command identifier and its
    idempotency key and the non-secret owner identity from the current lease.
    It is subsequently carried inside the authenticated Edge RPC envelope; the
    Edge store must prove in one read boundary that the supplied lease is live,
    its live owner equals ``owner_id``, and the durable command was admitted by
    that same owner before returning any receipt.
    """

    command_id: str
    idempotency_key: str
    session_id: str
    lease_id: str
    owner_id: str
    fencing_token: int
    schema_version: str = EMBODIMENT_RECEIPT_QUERY_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "session_id": self.session_id,
            "lease_id": self.lease_id,
            "owner_id": self.owner_id,
            "fencing_token": int(self.fencing_token),
        }


class EmbodimentCommandPort(Protocol):
    async def submit(self, command: EmbodimentCommand) -> EmbodimentReceipt: ...


def stable_command_id(*, idempotency_key: str) -> str:
    """由幂等键稳定导出命令 ID，令超时重试能命中同一设备操作。"""
    key = _required_text(idempotency_key, "idempotency_key")
    digest = hashlib.sha256(
        f"{EMBODIMENT_COMMAND_SCHEMA_VERSION}\n{key}".encode("utf-8")
    ).hexdigest()
    return f"emb_{digest[:32]}"


def build_embodiment_command(
    *,
    kind: str,
    action: str,
    payload: Mapping[str, Any] | None,
    authority_ref: str,
    grant_ref: str,
    deadline_ms: int,
    idempotency_key: str,
    request_id: str,
    issued_at_ms: int | None = None,
    session_id: str = "",
    lease_id: str = "",
    fencing_token: int = 0,
) -> EmbodimentCommand:
    """验证并规范化命令，拒绝已过期、未知种类和非有限 JSON 载荷。

    此处只证明命令在契约层可提交；Edge 接收端仍必须校验 grant、活跃租约及围栏，
    因为这些授权状态可能在 Host 创建命令后发生变化。
    """
    normalized_kind = _required_text(kind, "kind").lower()
    if normalized_kind not in EMBODIMENT_KINDS:
        raise ValueError(EMBODIMENT_COMMAND_INVALID)
    issued = int(issued_at_ms if issued_at_ms is not None else now_ms())
    deadline = int(deadline_ms)
    if deadline <= issued:
        raise ValueError(EMBODIMENT_COMMAND_EXPIRED)
    key = _required_text(idempotency_key, "idempotency_key")
    body = dict(payload or {})
    json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return EmbodimentCommand(
        command_id=stable_command_id(idempotency_key=key),
        kind=normalized_kind,
        action=_required_text(action, "action").lower(),
        payload=body,
        authority_ref=_required_text(authority_ref, "authority_ref"),
        grant_ref=_required_text(grant_ref, "grant_ref"),
        deadline_ms=deadline,
        idempotency_key=key,
        issued_at_ms=issued,
        request_id=_required_text(request_id, "request_id"),
        session_id=str(session_id or "").strip(),
        lease_id=str(lease_id or "").strip(),
        fencing_token=int(fencing_token),
    )


def parse_embodiment_command(raw: Mapping[str, Any] | Any) -> EmbodimentCommand:
    """通过重新构造并比对稳定 ID 恢复命令，防止载荷与命令身份脱节。"""
    if not isinstance(raw, Mapping):
        raise ValueError(EMBODIMENT_COMMAND_INVALID)
    if str(raw.get("schema_version") or "") != EMBODIMENT_COMMAND_SCHEMA_VERSION:
        raise ValueError(EMBODIMENT_COMMAND_INVALID)
    command = build_embodiment_command(
        kind=str(raw.get("kind") or ""),
        action=str(raw.get("action") or ""),
        payload=raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {},
        authority_ref=str(raw.get("authority_ref") or ""),
        grant_ref=str(raw.get("grant_ref") or ""),
        deadline_ms=int(raw.get("deadline_ms") or 0),
        idempotency_key=str(raw.get("idempotency_key") or ""),
        request_id=str(raw.get("request_id") or ""),
        issued_at_ms=int(raw.get("issued_at_ms") or 0),
        session_id=str(raw.get("session_id") or ""),
        lease_id=str(raw.get("lease_id") or ""),
        fencing_token=int(raw.get("fencing_token") or 0),
    )
    if str(raw.get("command_id") or "") != command.command_id:
        raise ValueError(EMBODIMENT_COMMAND_INVALID)
    return command


def parse_embodiment_receipt(raw: Mapping[str, Any] | Any) -> EmbodimentReceipt:
    if not isinstance(raw, Mapping):
        raise ValueError(EMBODIMENT_COMMAND_INVALID)
    return EmbodimentReceipt(
        command_id=_required_text(raw.get("command_id"), "command_id"),
        idempotency_key=_required_text(raw.get("idempotency_key"), "idempotency_key"),
        accepted=bool(raw.get("accepted", raw.get("success", False))),
        status=_required_text(raw.get("status"), "status"),
        reason_code=(str(raw.get("reason_code") or "").strip() or None),
        detail=str(raw.get("detail") or ""),
        received_at_ms=int(raw.get("received_at_ms") or now_ms()),
        completed_at_ms=(
            int(raw["completed_at_ms"]) if raw.get("completed_at_ms") is not None else None
        ),
        event=dict(raw.get("event") or {}),
        receipt_ref=(str(raw.get("receipt_ref") or "").strip() or None),
    )


def build_embodiment_receipt_query(
    *,
    command_id: str,
    idempotency_key: str,
    session_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
) -> EmbodimentReceiptQuery:
    """构造精确、按所有者和会话围栏的收据查询，避免旧持有者读取新会话结果。"""

    key = _required_text(idempotency_key, "idempotency_key")
    resolved_command_id = _required_text(command_id, "command_id")
    if resolved_command_id != stable_command_id(idempotency_key=key):
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID)
    try:
        fence = int(fencing_token)
    except (TypeError, ValueError) as exc:
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID) from exc
    if isinstance(fencing_token, bool) or fence < 1:
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID)
    return EmbodimentReceiptQuery(
        command_id=resolved_command_id,
        idempotency_key=key,
        session_id=_required_text(session_id, "session_id"),
        lease_id=_required_text(lease_id, "lease_id"),
        owner_id=_required_text(owner_id, "owner_id"),
        fencing_token=fence,
    )


def parse_embodiment_receipt_query(
    raw: Mapping[str, Any] | Any,
) -> EmbodimentReceiptQuery:
    if not isinstance(raw, Mapping):
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID)
    if str(raw.get("schema_version") or "") != EMBODIMENT_RECEIPT_QUERY_SCHEMA_VERSION:
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID)
    try:
        return build_embodiment_receipt_query(
            command_id=str(raw.get("command_id") or ""),
            idempotency_key=str(raw.get("idempotency_key") or ""),
            session_id=str(raw.get("session_id") or ""),
            lease_id=str(raw.get("lease_id") or ""),
            owner_id=str(raw.get("owner_id") or ""),
            fencing_token=raw.get("fencing_token"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(EMBODIMENT_RECEIPT_QUERY_INVALID) from exc


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{EMBODIMENT_COMMAND_INVALID}:{field}")
    return text


__all__ = [
    "EMBODIMENT_COMMAND_SCHEMA_VERSION", "EMBODIMENT_RECEIPT_SCHEMA_VERSION",
    "EMBODIMENT_COMMAND_INVALID", "EMBODIMENT_COMMAND_EXPIRED",
    "EMBODIMENT_COMMAND_DUPLICATE", "EMBODIMENT_COMMAND_CONFLICT",
    "EMBODIMENT_COMMAND_IN_DOUBT", "EMBODIMENT_RECEIPT_QUERY_SCHEMA_VERSION",
    "EMBODIMENT_RECEIPT_QUERY_INVALID", "EMBODIMENT_RECEIPT_NOT_FOUND",
    "EMBODIMENT_LEASE_REQUIRED",
    "EMBODIMENT_LEASE_INVALID", "EMBODIMENT_LEASE_EXPIRED",
    "EMBODIMENT_FENCE_STALE", "EMBODIMENT_KIND_VOICE",
    "EMBODIMENT_KIND_AVATAR", "EMBODIMENT_KIND_MATEENGINE",
    "EMBODIMENT_KIND_DESKTOP", "EMBODIMENT_KINDS", "EmbodimentCommand",
    "EmbodimentReceipt", "EmbodimentCommandPort", "stable_command_id",
    "build_embodiment_command", "parse_embodiment_command",
    "parse_embodiment_receipt", "EmbodimentReceiptQuery",
    "build_embodiment_receipt_query", "parse_embodiment_receipt_query",
]
