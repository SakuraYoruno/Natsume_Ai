"""Digital Life 的运行时无关契约：从版本化命令到可恢复的投影与外部投递。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any


LIFE_COMMAND_SCHEMA_NOT_READY = "LIFE_COMMAND_SCHEMA_NOT_READY"
LIFE_COMMAND_INVALID = "LIFE_COMMAND_INVALID"
LIFE_COMMAND_IDEMPOTENCY_CONFLICT = "LIFE_COMMAND_IDEMPOTENCY_CONFLICT"
LIFE_COMMAND_CLAIM_BUSY = "LIFE_COMMAND_CLAIM_BUSY"
LIFE_COMMAND_CLAIM_FENCED = "LIFE_COMMAND_CLAIM_FENCED"
LIFE_COMMAND_DURABLE_DATA_INVALID = "LIFE_COMMAND_DURABLE_DATA_INVALID"
LIFE_COMMAND_BASELINE_REQUIRED = "LIFE_COMMAND_BASELINE_REQUIRED"
LIFE_AGGREGATE_CLAIM_BUSY = "LIFE_AGGREGATE_CLAIM_BUSY"
LIFE_AGGREGATE_CLAIM_FENCED = "LIFE_AGGREGATE_CLAIM_FENCED"
LIFE_DELIVERY_CLAIM_FENCED = "LIFE_DELIVERY_CLAIM_FENCED"
LIFE_PROJECTION_CLAIM_FENCED = "LIFE_PROJECTION_CLAIM_FENCED"


class LifeCommandRepositoryError(RuntimeError):
    """Stable fail-closed error raised by Digital Life persistence ports."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or LIFE_COMMAND_INVALID)
        self.detail = str(detail or "Digital Life persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class LifeCommandStatus(str, Enum):
    APPLIED = "APPLIED"
    CONFLICT = "CONFLICT"


class LifeIntentKind(str, Enum):
    ORGAN = "ORGAN"
    PROJECTION = "PROJECTION"


class LifeDeliveryStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class LifeCommand:
    """某个人格聚合的一次乐观并发写入请求。

    ``expected_version`` 由持久化端用于拒绝陈旧写入；``command_hash`` 则固定命令
    身份和业务载荷，供幂等重放发现同一 command_id 被替换为不同意图的冲突。构造时
    将映射规范化为 JSON 对象，确保摘要不会受调用方可变容器影响。
    """
    command_id: str
    person_id: str
    command_type: str
    payload: Mapping[str, Any]
    expected_version: int
    created_at_ms: int
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _required(self.command_id, "command_id", 240))
        object.__setattr__(self, "person_id", _required(self.person_id, "person_id", 240))
        object.__setattr__(
            self,
            "command_type",
            _required(self.command_type, "command_type", 120).lower(),
        )
        object.__setattr__(self, "payload", _json_object(self.payload, "payload"))
        object.__setattr__(self, "metadata", _json_object(self.metadata, "metadata"))
        object.__setattr__(
            self,
            "expected_version",
            _non_negative_int(self.expected_version, "expected_version"),
        )
        object.__setattr__(
            self,
            "created_at_ms",
            _non_negative_int(self.created_at_ms, "created_at_ms"),
        )
        object.__setattr__(self, "correlation_id", _optional(self.correlation_id, 240))
        object.__setattr__(self, "causation_id", _optional(self.causation_id, 240))

    @property
    def command_hash(self) -> str:
        """返回可跨进程复算的命令身份摘要，不把执行结果或聚合版本混入其中。"""
        return _digest(
            {
                "command_id": self.command_id,
                "person_id": self.person_id,
                "command_type": self.command_type,
                "payload": dict(self.payload),
                "correlation_id": self.correlation_id,
                "causation_id": self.causation_id,
                "metadata": dict(self.metadata),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_hash": self.command_hash,
            "person_id": self.person_id,
            "command_type": self.command_type,
            "payload": dict(self.payload),
            "expected_version": self.expected_version,
            "created_at_ms": self.created_at_ms,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class LifeSnapshot:
    """聚合当前已提交状态及其事件链锚点；它不是外部投递已经成功的证明。"""
    person_id: str
    version: int
    state: Mapping[str, Any]
    state_digest: str
    last_event_id: str | None = None
    last_event_digest: str | None = None
    updated_at_ms: int = 0

    @classmethod
    def empty(cls, person_id: str) -> "LifeSnapshot":
        """创建版本零基线，使首个命令的乐观并发比较有确定起点。"""
        normalized = _required(person_id, "person_id", 240)
        state = {"person_id": normalized, "version": 0}
        return cls(
            person_id=normalized,
            version=0,
            state=state,
            state_digest=_digest(state),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "version": self.version,
            "state": dict(self.state),
            "state_digest": self.state_digest,
            "last_event_id": self.last_event_id,
            "last_event_digest": self.last_event_digest,
            "updated_at_ms": self.updated_at_ms,
        }


@dataclass(frozen=True)
class LifeEvent:
    """已提交状态跃迁的不可变记录，以前后状态摘要和前序摘要串联审计链。"""
    event_id: str
    command_id: str
    person_id: str
    sequence: int
    event_type: str
    payload: Mapping[str, Any]
    state_before_digest: str
    state_after_digest: str
    previous_event_digest: str | None
    event_digest: str
    created_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "command_id": self.command_id,
            "person_id": self.person_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "state_before_digest": self.state_before_digest,
            "state_after_digest": self.state_after_digest,
            "previous_event_digest": self.previous_event_digest,
            "event_digest": self.event_digest,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True)
class LifeProjectionIntent:
    intent_id: str
    event_id: str
    command_id: str
    person_id: str
    intent_kind: LifeIntentKind
    target: str
    payload: Mapping[str, Any]
    payload_digest: str
    created_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "event_id": self.event_id,
            "command_id": self.command_id,
            "person_id": self.person_id,
            "intent_kind": self.intent_kind.value,
            "target": self.target,
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True)
class LifeProjectionMessage:
    intent_id: str
    event_id: str
    command_id: str
    person_id: str
    intent_kind: LifeIntentKind
    target: str
    payload: Mapping[str, Any]
    payload_digest: str
    attempt_count: int
    fencing_token: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "event_id": self.event_id,
            "command_id": self.command_id,
            "person_id": self.person_id,
            "intent_kind": self.intent_kind.value,
            "target": self.target,
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "attempt_count": self.attempt_count,
            "fencing_token": self.fencing_token,
        }


@dataclass(frozen=True)
class LifeProjectionReceipt:
    intent_id: str
    payload_digest: str
    result: Mapping[str, Any]
    completed_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "payload_digest": self.payload_digest,
            "result": dict(self.result),
            "completed_at_ms": self.completed_at_ms,
        }


@dataclass(frozen=True)
class LifeDeliveryAuthorization:
    """每个外部 Life 投递意图必须携带的持久化授权引用。

    此对象保存授权来源与主体的可核验标识，而非可直接使用的秘密或令牌；投递端仍需
    按 governor grant 进行实际校验，避免 outbox 重放自动扩大原请求的权限。
    """

    request_id: str
    governor_grant_ref: str
    authority_kind: str
    authority_principal_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required(self.request_id, "authorization.request_id", 240))
        object.__setattr__(
            self,
            "governor_grant_ref",
            _required(
                self.governor_grant_ref,
                "authorization.governor_grant_ref",
                512,
            ),
        )
        object.__setattr__(
            self,
            "authority_kind",
            _required(self.authority_kind, "authorization.authority_kind", 120).lower(),
        )
        object.__setattr__(
            self,
            "authority_principal_id",
            _required(
                self.authority_principal_id,
                "authorization.authority_principal_id",
                240,
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LifeDeliveryAuthorization":
        if not isinstance(value, Mapping):
            raise ValueError("delivery.authorization must be an object")
        return cls(
            request_id=str(value.get("request_id") or ""),
            governor_grant_ref=str(value.get("governor_grant_ref") or ""),
            authority_kind=str(value.get("authority_kind") or ""),
            authority_principal_id=str(
                value.get("authority_principal_id") or value.get("principal_id") or ""
            ),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "request_id": self.request_id,
            "governor_grant_ref": self.governor_grant_ref,
            "authority_kind": self.authority_kind,
            "authority_principal_id": self.authority_principal_id,
        }


@dataclass(frozen=True)
class LifeDeliveryIntent:
    destination: str
    payload: Mapping[str, Any]
    authorization: LifeDeliveryAuthorization

    def __post_init__(self) -> None:
        object.__setattr__(self, "destination", _required(self.destination, "destination", 240))
        object.__setattr__(self, "payload", _json_object(self.payload, "delivery.payload"))
        if not isinstance(self.authorization, LifeDeliveryAuthorization):
            raise ValueError("delivery.authorization must be typed")


@dataclass(frozen=True)
class LifeCommandReceipt:
    """命令提交的稳定结果；``replayed`` 只说明命中既有收据，不会重复外部效果。"""
    command_id: str
    command_hash: str
    person_id: str
    status: LifeCommandStatus
    reason_code: str | None
    event_id: str | None
    state_version: int
    state_digest: str
    outbox_count: int
    created_at_ms: int
    replayed: bool = False

    def as_replayed(self) -> "LifeCommandReceipt":
        """以不可变副本标识幂等重放，保留原始事件、版本与摘要作为同一事实。"""
        return replace(self, replayed=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_hash": self.command_hash,
            "person_id": self.person_id,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "event_id": self.event_id,
            "state_version": self.state_version,
            "state_digest": self.state_digest,
            "outbox_count": self.outbox_count,
            "created_at_ms": self.created_at_ms,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class LifeCommandPreview:
    command: LifeCommand
    before: LifeSnapshot
    after: LifeSnapshot
    event: LifeEvent
    projection_intents: tuple[LifeProjectionIntent, ...]
    deliveries: tuple[LifeDeliveryIntent, ...]


@dataclass(frozen=True)
class LifeAggregateClaim:
    person_id: str
    owner_token: str
    fencing_token: int
    lease_expires_at_ms: int
    attempt_count: int


@dataclass(frozen=True)
class LifeCommandClaim:
    command_id: str
    command_hash: str
    owner_token: str
    fencing_token: int
    claim_expires_at_ms: int
    attempt_count: int


@dataclass(frozen=True)
class LifeOutboxMessage:
    """状态提交后待投递的副作用条目。

    payload 与授权摘要同消息一起冻结；``fencing_token`` 让过期 worker 的迟到结果
    无法覆盖新领取者，``status`` 仅表示投递流程而不回写聚合状态。
    """
    outbox_id: str
    event_id: str
    command_id: str
    person_id: str
    destination: str
    delivery_key: str
    payload: Mapping[str, Any]
    payload_digest: str
    authorization: LifeDeliveryAuthorization
    authorization_digest: str
    status: LifeDeliveryStatus
    attempt_count: int
    fencing_token: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "outbox_id": self.outbox_id,
            "event_id": self.event_id,
            "command_id": self.command_id,
            "person_id": self.person_id,
            "destination": self.destination,
            "delivery_key": self.delivery_key,
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "authorization": self.authorization.as_dict(),
            "authorization_digest": self.authorization_digest,
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "fencing_token": self.fencing_token,
        }


@dataclass(frozen=True)
class LifeDeliveryReceipt:
    outbox_id: str
    delivery_key: str
    payload_digest: str
    authorization_digest: str
    result: Mapping[str, Any]
    completed_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "outbox_id", _required(self.outbox_id, "outbox_id", 280))
        object.__setattr__(
            self,
            "delivery_key",
            _required(self.delivery_key, "delivery_key", 280),
        )
        object.__setattr__(self, "payload_digest", _required(self.payload_digest, "payload_digest", 64))
        object.__setattr__(
            self,
            "authorization_digest",
            _required(self.authorization_digest, "authorization_digest", 64),
        )
        object.__setattr__(self, "result", _json_object(self.result, "delivery.result"))
        object.__setattr__(
            self,
            "completed_at_ms",
            _non_negative_int(self.completed_at_ms, "completed_at_ms"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "outbox_id": self.outbox_id,
            "delivery_key": self.delivery_key,
            "payload_digest": self.payload_digest,
            "authorization_digest": self.authorization_digest,
            "result": dict(self.result),
            "completed_at_ms": self.completed_at_ms,
        }


@dataclass(frozen=True)
class LifeDispatchSummary:
    claimed: int
    delivered: int
    failed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "delivered": self.delivered,
            "failed": self.failed,
        }


def canonical_json(value: Any) -> str:
    """生成排序且无空白的 JSON 表示，作为跨语言摘要和持久化比较的共同输入。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return _digest(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{name} must contain 1..{limit} characters")
    return text


def _optional(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"optional text exceeds {limit} characters")
    return text


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    normalized = int(value)
    if normalized < 0 or normalized != value:
        raise ValueError(f"{name} must be a non-negative integer")
    return normalized


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    normalized = json.loads(canonical_json(dict(value)))
    if not isinstance(normalized, dict):
        raise ValueError(f"{name} must be an object")
    return normalized


def normalize_intent_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("intent rows must be a list")
    return tuple(_json_object(row, "intent") for row in value)


__all__ = [
    "LIFE_AGGREGATE_CLAIM_BUSY",
    "LIFE_AGGREGATE_CLAIM_FENCED",
    "LIFE_COMMAND_BASELINE_REQUIRED",
    "LIFE_COMMAND_CLAIM_BUSY",
    "LIFE_COMMAND_CLAIM_FENCED",
    "LIFE_COMMAND_DURABLE_DATA_INVALID",
    "LIFE_COMMAND_IDEMPOTENCY_CONFLICT",
    "LIFE_COMMAND_INVALID",
    "LIFE_COMMAND_SCHEMA_NOT_READY",
    "LIFE_DELIVERY_CLAIM_FENCED",
    "LIFE_PROJECTION_CLAIM_FENCED",
    "LifeCommand",
    "LifeAggregateClaim",
    "LifeCommandClaim",
    "LifeCommandPreview",
    "LifeCommandReceipt",
    "LifeCommandRepositoryError",
    "LifeCommandStatus",
    "LifeDeliveryAuthorization",
    "LifeDeliveryIntent",
    "LifeDeliveryReceipt",
    "LifeDeliveryStatus",
    "LifeDispatchSummary",
    "LifeEvent",
    "LifeIntentKind",
    "LifeOutboxMessage",
    "LifeProjectionIntent",
    "LifeProjectionMessage",
    "LifeProjectionReceipt",
    "LifeSnapshot",
    "canonical_json",
    "digest_json",
    "normalize_intent_rows",
]
