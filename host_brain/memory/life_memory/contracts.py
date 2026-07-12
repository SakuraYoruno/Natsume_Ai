"""Typed contracts for the governed LifeMemory chat admission boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any


LIFE_MEMORY_CONTRACT_VERSION = "life-memory.chat.v1"


class MemoryAdmissionKind(str, Enum):
    SKIP = "SKIP"
    WRITE_LIGHT = "WRITE_LIGHT"
    WRITE = "WRITE"


@dataclass(frozen=True)
class MemoryAdmissionClaim:
    """Durable fencing lease for the one policy evaluator of a request."""

    request_id: str
    command_hash: str
    owner_token: str
    claim_expires_ts_ms: int
    attempt_count: int

    def __post_init__(self) -> None:
        request_id = _required_text(self.request_id, "request_id", limit=240)
        command_hash = _required_text(self.command_hash, "command_hash", limit=64)
        if len(command_hash) != 64:
            raise ValueError("command_hash must be a SHA-256 digest")
        owner_token = _required_text(self.owner_token, "owner_token", limit=240)
        expires = _exact_non_negative_int(
            self.claim_expires_ts_ms,
            "claim_expires_ts_ms",
        )
        attempts = _exact_non_negative_int(self.attempt_count, "attempt_count")
        if attempts < 1:
            raise ValueError("attempt_count must be >= 1")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "command_hash", command_hash)
        object.__setattr__(self, "owner_token", owner_token)
        object.__setattr__(self, "claim_expires_ts_ms", expires)
        object.__setattr__(self, "attempt_count", attempts)


@dataclass(frozen=True)
class ChatMemoryCandidate:
    """One scrubbed, committed chat turn offered to LifeMemory.

    The contract intentionally owns no database or projection objects.  Its
    canonical hash is the durable idempotency fingerprint used for replay and
    conflict detection.
    """

    request_id: str
    session_id: str
    person_id: str
    user_message: str
    assistant_reply: str
    ts_ms: int
    intent_id: str | None = None
    emotion_tag: str = "NEUTRAL"
    emotion_valence: float = 0.0
    emotion_arousal: float = 0.3
    turn_count: int = 1
    attachments: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    multimodal_observations: Mapping[str, Any] = field(default_factory=dict)
    attachment_summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = LIFE_MEMORY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        request_id = _required_text(self.request_id, "request_id", limit=240)
        session_id = _required_text(self.session_id, "session_id", limit=240)
        person_id = _required_text(self.person_id or "default", "person_id", limit=240)
        user_message = str(self.user_message or "").strip()
        assistant_reply = str(self.assistant_reply or "").strip()
        if not user_message and not assistant_reply:
            raise ValueError("LIFE_MEMORY_EMPTY_CHAT_CANDIDATE")
        ts_ms = _exact_non_negative_int(self.ts_ms, "ts_ms")
        turn_count = _exact_non_negative_int(self.turn_count, "turn_count")
        if turn_count < 1:
            raise ValueError("turn_count must be >= 1")
        emotion_tag = str(self.emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        valence = _bounded_float(self.emotion_valence, "emotion_valence", -1.0, 1.0)
        arousal = _bounded_float(self.emotion_arousal, "emotion_arousal", 0.0, 1.0)
        attachments = tuple(
            _json_mapping(item, field_name="attachments")
            for item in tuple(self.attachments or ())
        )
        observations = _json_mapping(
            self.multimodal_observations,
            field_name="multimodal_observations",
        )
        metadata = _json_mapping(self.metadata, field_name="metadata")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "person_id", person_id)
        object.__setattr__(self, "user_message", user_message)
        object.__setattr__(self, "assistant_reply", assistant_reply)
        object.__setattr__(self, "intent_id", _optional_text(self.intent_id, limit=240))
        object.__setattr__(self, "ts_ms", ts_ms)
        object.__setattr__(self, "turn_count", turn_count)
        object.__setattr__(self, "emotion_tag", emotion_tag)
        object.__setattr__(self, "emotion_valence", valence)
        object.__setattr__(self, "emotion_arousal", arousal)
        object.__setattr__(self, "attachments", attachments)
        object.__setattr__(self, "multimodal_observations", observations)
        object.__setattr__(self, "attachment_summary", str(self.attachment_summary or "").strip())
        object.__setattr__(self, "metadata", metadata)
        if str(self.schema_version) != LIFE_MEMORY_CONTRACT_VERSION:
            raise ValueError("LIFE_MEMORY_CONTRACT_VERSION_UNSUPPORTED")

    @property
    def command_hash(self) -> str:
        # Processing timestamps, latency traces and other volatile diagnostics
        # must not turn an otherwise exact request replay into a conflict.
        stable_metadata = {
            key: self.metadata.get(key)
            for key in (
                "source",
                "reply_channel",
                "reply_target",
                "gateway_message_id",
                "scene_type",
                "input_mode",
                "has_image",
            )
            if key in self.metadata
        }
        payload = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "person_id": self.person_id,
            "intent_id": self.intent_id,
            "user_message": self.user_message,
            "assistant_reply": self.assistant_reply,
            "emotion_tag": self.emotion_tag,
            "emotion_valence": self.emotion_valence,
            "emotion_arousal": self.emotion_arousal,
            "turn_count": self.turn_count,
            "attachments": [dict(item) for item in self.attachments],
            "multimodal_observations": dict(self.multimodal_observations),
            "attachment_summary": self.attachment_summary,
            "metadata": stable_metadata,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "person_id": self.person_id,
            "intent_id": self.intent_id,
            "user_message": self.user_message,
            "assistant_reply": self.assistant_reply,
            "ts_ms": self.ts_ms,
            "emotion_tag": self.emotion_tag,
            "emotion_valence": self.emotion_valence,
            "emotion_arousal": self.emotion_arousal,
            "turn_count": self.turn_count,
            "attachments": [dict(item) for item in self.attachments],
            "multimodal_observations": dict(self.multimodal_observations),
            "attachment_summary": self.attachment_summary,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    kind: MemoryAdmissionKind
    reason_code: str
    worth_score: float
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, MemoryAdmissionKind) else MemoryAdmissionKind(str(self.kind))
        reason_code = _required_text(self.reason_code, "reason_code", limit=240)
        score = _bounded_float(self.worth_score, "worth_score", 0.0, 1.0)
        tags = tuple(dict.fromkeys(_required_text(tag, "tag", limit=120) for tag in self.tags))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "worth_score", score)
        object.__setattr__(self, "tags", tags)

    @property
    def emits_projection(self) -> bool:
        return self.kind is MemoryAdmissionKind.WRITE

    @property
    def writes_episode(self) -> bool:
        return self.kind is not MemoryAdmissionKind.SKIP

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.kind.value,
            "reason_code": self.reason_code,
            "worth_score": round(self.worth_score, 6),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class MemoryWriteReceipt:
    request_id: str
    command_hash: str
    decision: MemoryAdmissionKind
    status: str
    reason_code: str
    worth_score: float
    tags: tuple[str, ...]
    episode_id: str | None
    outbox_id: str | None
    created_ts_ms: int
    replayed: bool = False
    projection_status: str | None = None

    def replay(self, *, projection_status: str | None = None) -> "MemoryWriteReceipt":
        return replace(
            self,
            replayed=True,
            projection_status=projection_status or self.projection_status,
        )

    def with_projection_status(self, status: str | None) -> "MemoryWriteReceipt":
        return replace(self, projection_status=_optional_text(status, limit=40))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "command_hash": self.command_hash,
            "decision": self.decision.value,
            "worth_score": round(self.worth_score, 6),
            "tags": list(self.tags),
            "episode_id": self.episode_id,
            "memory_id": self.episode_id,
            "outbox_id": self.outbox_id,
            "projection_status": self.projection_status,
            "created_ts_ms": self.created_ts_ms,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class LifeMemoryEvidenceWrite:
    evidence_id: str
    evidence_kind: str
    source_ref: str | None
    request_id: str
    content: str
    payload: Mapping[str, Any]
    trust_level: str
    created_ts_ms: int


@dataclass(frozen=True)
class ChatMemoryWritePlan:
    episode_id: str
    episode_kind: str
    thread_id: str
    person_id: str
    session_id: str
    actor_id: str
    intent: str | None
    action: str
    result: str
    primary_ref: str
    summary: str
    salience: float
    memory_strength: float
    decay_profile_id: str
    retention_state: str
    truth_state: str
    status: str
    created_ts_ms: int
    metadata: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    evidence: tuple[LifeMemoryEvidenceWrite, ...]


def _required_text(value: Any, field_name: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _optional_text(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"value exceeds {limit} characters")
    return text


def _exact_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an exact integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _bounded_float(value: Any, field_name: str, lower: float, upper: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if number < lower or number > upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}")
    return number


def _json_mapping(value: Mapping[str, Any] | None, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=_reject_json_value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON values") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return decoded


def _reject_json_value(value: Any) -> Any:
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


__all__ = [
    "LIFE_MEMORY_CONTRACT_VERSION",
    "ChatMemoryCandidate",
    "ChatMemoryWritePlan",
    "LifeMemoryEvidenceWrite",
    "MemoryAdmissionClaim",
    "MemoryAdmissionDecision",
    "MemoryAdmissionKind",
    "MemoryWriteReceipt",
]
