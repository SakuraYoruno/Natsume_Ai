"""冻结情感表达计划的持久化 Host 投递协议。

契约描述 Host 对单个句子及其语音/头像表达的接受结果，但不声称 Edge 已播放音频或
改变模型表情；该物理事实必须由绑定同一 delivery identity 的后续通道收据证明。
这里禁止原始媒体、提示词、私有记忆和模型推理；可重放的已门控句子仅留在规范 Life
命令中。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any


_SELF_ID = "natsume_ai"
_CHANNELS = frozenset({"VOICE", "AVATAR"})
_ACTIONS = frozenset({"SPEAK", "TRANSITION", "CANCEL", "RELEASE"})
_RECEIPT_STATUSES = frozenset(
    {
        "HOST_PROTOCOL_STAGED",
        "HOST_CANCEL_STAGED",
        "HOST_RELEASE_STAGED",
        "CHANNEL_DEGRADED",
        "EDGE_APPLIED",
        "EDGE_REJECTED",
    }
)


def _required(value: Any, field_name: str, *, limit: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return result


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        serialized = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be canonical JSON") from exc
    parsed = json.loads(serialized)
    if not isinstance(parsed, dict):  # pragma: no cover - json object guarantee
        raise ValueError(f"{field_name} must be an object")
    return parsed


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ExpressionDeliveryIntentV1:
    """由不可变回合计划导出的单通道投递意图。

    ``synchronization_id`` 将语音和头像的同一表达关联起来，``self_affect_revision``
    防止新情感状态覆盖已冻结的旧计划；意图本身仍不等于设备已执行。
    """

    delivery_id: str
    plan_id: str
    synchronization_id: str
    self_affect_revision: int
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    channel: str
    action: str
    sentence_index: int
    payload: Mapping[str, Any]
    created_at_ms: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        channel = _required(self.channel, "channel", limit=32).upper()
        if channel not in _CHANNELS:
            raise ValueError(f"channel must be one of {sorted(_CHANNELS)}")
        action = _required(self.action, "action", limit=32).upper()
        if action not in _ACTIONS:
            raise ValueError(f"action must be one of {sorted(_ACTIONS)}")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        object.__setattr__(self, "delivery_id", _required(self.delivery_id, "delivery_id"))
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(
            self,
            "synchronization_id",
            _required(self.synchronization_id, "synchronization_id"),
        )
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "sentence_index", _non_negative_int(self.sentence_index, "sentence_index"))
        object.__setattr__(self, "payload", _json_object(self.payload, "payload"))
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "self_id", _SELF_ID)

    @property
    def payload_digest(self) -> str:
        """计算规范载荷摘要，供传输与收据链检测同一 delivery 的内容漂移。"""
        return _digest(self.payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "expression_delivery_intent.v1",
            "delivery_id": self.delivery_id,
            "plan_id": self.plan_id,
            "synchronization_id": self.synchronization_id,
            "self_id": self.self_id,
            "self_affect_revision": self.self_affect_revision,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "action": self.action,
            "sentence_index": self.sentence_index,
            "payload": dict(self.payload),
            "payload_digest": self.payload_digest,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ExpressionDeliveryReceiptV1:
    """Host 暂存或后续物理通道结果的有界收据。

    ``HOST_*_STAGED`` 只表示 Host 已安排协议步骤；只有带物理收据引用的 Edge 结果
    才能证明外部副作用。区分两者让中断恢复不会把“已排队”误读为“已送达”。
    """

    receipt_id: str
    delivery_id: str
    synchronization_id: str
    channel: str
    action: str
    status: str
    created_at_ms: int
    reason_code: str | None = None
    physical_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        channel = _required(self.channel, "channel", limit=32).upper()
        action = _required(self.action, "action", limit=32).upper()
        status = _required(self.status, "status", limit=64).upper()
        if channel not in _CHANNELS:
            raise ValueError(f"channel must be one of {sorted(_CHANNELS)}")
        if action not in _ACTIONS:
            raise ValueError(f"action must be one of {sorted(_ACTIONS)}")
        if status not in _RECEIPT_STATUSES:
            raise ValueError(f"status must be one of {sorted(_RECEIPT_STATUSES)}")
        object.__setattr__(self, "receipt_id", _required(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "delivery_id", _required(self.delivery_id, "delivery_id"))
        object.__setattr__(
            self,
            "synchronization_id",
            _required(self.synchronization_id, "synchronization_id"),
        )
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(
            self,
            "reason_code",
            (str(self.reason_code or "").strip()[:160] or None),
        )
        object.__setattr__(
            self,
            "physical_receipt_ref",
            (str(self.physical_receipt_ref or "").strip()[:512] or None),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "expression_delivery_receipt.v1",
            "receipt_id": self.receipt_id,
            "delivery_id": self.delivery_id,
            "synchronization_id": self.synchronization_id,
            "channel": self.channel,
            "action": self.action,
            "status": self.status,
            "reason_code": self.reason_code,
            "physical_receipt_ref": self.physical_receipt_ref,
            "created_at_ms": self.created_at_ms,
        }


__all__ = [
    "ExpressionDeliveryIntentV1",
    "ExpressionDeliveryReceiptV1",
]
