"""每次 Tool、Skill、MCP 与领域能力调用的不可变执行契约。

请求、授权、尝试、证据和收据将发现能力与实际执行分离：能力名称本身不构成许可，只有
与完整请求摘要绑定、未过期且可追溯的 Grant 才能进入 Broker 的效果路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = "Natsume.capability.execution.v1"


class CapabilityKind(str, Enum):
    """能力来源类别；用于阻止同名跨平面能力在授权时混淆。"""

    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"
    DOMAIN = "domain"


class GrantDecision(str, Enum):
    """授权权威对单次能力请求作出的二元结论。"""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ReceiptStatus(str, Enum):
    """能力调用的终态或不确定终态；IN_DOUBT 不可被重试器当作成功。"""

    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    IN_DOUBT = "IN_DOUBT"


def _text(value: Any, name: str) -> str:
    """规范化必须存在的标识字段，拒绝空白值进入摘要或持久化键。"""
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _json_mapping(value: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """深复制为 JSON 兼容只读映射，切断外部可变对象对已授权请求的影响。"""
    source = dict(value or {})
    try:
        canonical = json.loads(json.dumps(source, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-compatible") from exc
    if not isinstance(canonical, dict):
        raise ValueError(f"{name} must be an object")
    return MappingProxyType(canonical)


# 请求在构造时即被冻结并规范化为 JSON 值，防止授权之后调用参数被外部可变对象悄然改写。
@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    request_id: str
    attempt_id: str
    idempotency_key: str
    capability_id: str
    kind: CapabilityKind
    args: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict, repr=False)
    created_at_ms: int = 0
    deadline_at_ms: int | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # 幂等键约束“同一意图只能执行一次”；它和请求摘要共同防止键被复用于不同操作。
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        object.__setattr__(self, "attempt_id", _text(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "idempotency_key", _text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "capability_id", _text(self.capability_id, "capability_id"))
        if not isinstance(self.kind, CapabilityKind):
            object.__setattr__(self, "kind", CapabilityKind(str(self.kind)))
        if int(self.created_at_ms) < 0:
            raise ValueError("created_at_ms must be non-negative")
        object.__setattr__(self, "created_at_ms", int(self.created_at_ms))
        if self.deadline_at_ms is not None:
            object.__setattr__(self, "deadline_at_ms", int(self.deadline_at_ms))
        object.__setattr__(self, "args", _json_mapping(self.args, "args"))
        object.__setattr__(self, "context", _json_mapping(self.context, "context"))

    @property
    def request_hash(self) -> str:
        # 授权凭据绑定完整调用意图（含上下文），而不是仅绑定能力名称。
        payload = {
            "capability_id": self.capability_id,
            "kind": self.kind.value,
            "args": dict(self.args),
            "context": dict(self.context),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# Grant 是带有效期的最小授权证明；批准必须可追溯到外部权威来源。
@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    request_hash: str
    capability_id: str
    kind: CapabilityKind
    decision: GrantDecision
    reason_code: str
    authority_ref: str
    issued_at_ms: int
    expires_at_ms: int
    constraints: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _text(self.grant_id, "grant_id"))
        object.__setattr__(self, "request_hash", _text(self.request_hash, "request_hash"))
        object.__setattr__(self, "capability_id", _text(self.capability_id, "capability_id"))
        if not isinstance(self.kind, CapabilityKind):
            object.__setattr__(self, "kind", CapabilityKind(str(self.kind)))
        if not isinstance(self.decision, GrantDecision):
            object.__setattr__(self, "decision", GrantDecision(str(self.decision)))
        if self.decision is GrantDecision.APPROVE and not str(self.authority_ref or "").strip():
            raise ValueError("approved grant requires authority_ref")
        object.__setattr__(self, "reason_code", str(self.reason_code or "").strip())
        object.__setattr__(self, "authority_ref", str(self.authority_ref or "").strip())
        object.__setattr__(self, "issued_at_ms", int(self.issued_at_ms))
        object.__setattr__(self, "expires_at_ms", int(self.expires_at_ms))
        object.__setattr__(self, "constraints", _json_mapping(self.constraints, "constraints"))


@dataclass(frozen=True, slots=True)
class CapabilityAttempt:
    """一次实际或已终结的执行尝试的时间坐标，不含调用输出正文。"""

    attempt_id: str
    request_id: str
    capability_id: str
    kind: CapabilityKind
    grant_id: str | None
    status: str
    started_at_ms: int
    finished_at_ms: int


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """与尝试关联的输出摘要、时延与错误分类，供审计而非重放内容。"""

    evidence_id: str
    attempt_id: str
    output_digest: str | None
    latency_ms: int
    error_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# Receipt 是幂等重放时可返回的终态事实，不应被调用方当成新的执行请求。
@dataclass(frozen=True, slots=True)
class CapabilityReceipt:
    receipt_id: str
    request_id: str
    attempt_id: str
    idempotency_key: str
    capability_id: str
    kind: CapabilityKind
    status: ReceiptStatus
    reason_code: str | None
    detail: str
    grant_id: str | None
    evidence_ref: str
    output: Any = None
    replayed: bool = False

    @property
    def success(self) -> bool:
        """是否已获得确定成功终态；重放标志不改变该终态。"""
        return self.status is ReceiptStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        """导出收据快照；调用方应将 ``replayed`` 视为历史结果而非新执行。"""
        return {
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "capability_id": self.capability_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "grant_id": self.grant_id,
            "evidence_ref": self.evidence_ref,
            "output": self.output,
            "replayed": self.replayed,
        }


__all__ = [
    "CapabilityAttempt", "CapabilityEvidence", "CapabilityGrant", "CapabilityKind",
    "CapabilityReceipt", "CapabilityRequest", "GrantDecision", "ReceiptStatus", "SCHEMA_VERSION",
]
