"""Typed immutable payloads for post-Acceptance conversation effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


PREFERENCE_SIGNAL_SCHEMA_V1 = "Natsume.conversation.preference_signal.v1"
LIFE_CONVERSATION_PROJECTION_SCHEMA_V1 = (
    "Natsume.conversation.life_projection.v1"
)
_OPERATION_ID = re.compile(r"^preference-effect://sha256/[0-9a-f]{64}$")


class ConversationAcceptanceEffectError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AcceptedConversationIdentityV1:
    request_id: str
    session_id: str
    person_id: str
    content: str
    ts_ms: int
    lang: str
    metadata_json: str

    @classmethod
    def from_fields(cls, value: Mapping[str, Any]) -> "AcceptedConversationIdentityV1":
        request_id = _identity(value.get("request_id"), "request_id", 180)
        session_id = _identity(value.get("session_id"), "session_id", 180)
        person_id = _identity(value.get("person_id"), "person_id", 120)
        content = value.get("content")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > 4_000
            or content != re.sub(r"\s+", " ", content.strip())
        ):
            raise ConversationAcceptanceEffectError("content is not canonical")
        timestamp = value.get("ts_ms")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ConversationAcceptanceEffectError("ts_ms is not canonical")
        lang = _identity(value.get("lang"), "lang", 32)
        metadata = value.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ConversationAcceptanceEffectError("metadata must be an object")
        return cls(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            content=content,
            ts_ms=timestamp,
            lang=lang,
            metadata_json=_canonical_json(metadata),
        )

    @property
    def metadata(self) -> dict[str, Any]:
        decoded = json.loads(self.metadata_json)
        if not isinstance(decoded, dict):
            raise ConversationAcceptanceEffectError("metadata is not an object")
        return decoded

    @property
    def turn_id(self) -> str:
        return "turn::sha256:" + hashlib.sha256(
            "\x1f".join((self.session_id, self.request_id, "user")).encode("utf-8")
        ).hexdigest()

    def as_fields(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "person_id": self.person_id,
            "content": self.content,
            "ts_ms": self.ts_ms,
            "lang": self.lang,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class AcceptedPreferenceSignalV1:
    operation_id: str
    conversation: AcceptedConversationIdentityV1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AcceptedPreferenceSignalV1":
        payload = dict(value or {})
        required = {
            "schema_version",
            "operation_id",
            "request_id",
            "session_id",
            "person_id",
            "content",
            "ts_ms",
            "lang",
            "metadata",
        }
        if set(payload) != required or payload.get("schema_version") != (
            PREFERENCE_SIGNAL_SCHEMA_V1
        ):
            raise ConversationAcceptanceEffectError(
                "preference signal payload fields drifted"
            )
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise ConversationAcceptanceEffectError("operation_id is not canonical")
        return cls(
            operation_id=operation_id,
            conversation=AcceptedConversationIdentityV1.from_fields(payload),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": PREFERENCE_SIGNAL_SCHEMA_V1,
            "operation_id": self.operation_id,
            **self.conversation.as_fields(),
        }

    @property
    def payload_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(
            _canonical_json(self.as_mapping()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AcceptedLifeConversationProjectionV1:
    command_id: str
    conversation: AcceptedConversationIdentityV1

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "AcceptedLifeConversationProjectionV1":
        payload = dict(value or {})
        required = {
            "schema_version",
            "command_id",
            "request_id",
            "session_id",
            "person_id",
            "content",
            "ts_ms",
            "lang",
            "metadata",
        }
        if set(payload) != required or payload.get("schema_version") != (
            LIFE_CONVERSATION_PROJECTION_SCHEMA_V1
        ):
            raise ConversationAcceptanceEffectError(
                "Life conversation projection payload fields drifted"
            )
        conversation = AcceptedConversationIdentityV1.from_fields(payload)
        command_id = _identity(payload.get("command_id"), "command_id", 240)
        if command_id != conversation_life_command_id(conversation.request_id):
            raise ConversationAcceptanceEffectError("Life command_id drifted")
        return cls(command_id=command_id, conversation=conversation)

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": LIFE_CONVERSATION_PROJECTION_SCHEMA_V1,
            "command_id": self.command_id,
            **self.conversation.as_fields(),
        }


def preference_operation_id(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    request_id: str,
) -> str:
    material = {
        "graph_id": str(graph_id),
        "graph_revision": int(graph_revision),
        "node_id": str(node_id),
        "attempt_id": str(attempt_id),
        "request_id": str(request_id),
    }
    return "preference-effect://sha256/" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()


def conversation_life_command_id(request_id: str) -> str:
    candidate = f"life::conversation_turn::{request_id}::user"
    if len(candidate) <= 240:
        return candidate
    digest = hashlib.sha256(
        _canonical_json({"request_id": request_id, "role": "user"}).encode("utf-8")
    ).hexdigest()
    return f"life::conversation_turn::request::{digest}"


def _identity(value: Any, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise ConversationAcceptanceEffectError(f"{field} is not canonical")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConversationAcceptanceEffectError("payload is not canonical JSON") from exc


__all__ = [
    "AcceptedConversationIdentityV1",
    "AcceptedLifeConversationProjectionV1",
    "AcceptedPreferenceSignalV1",
    "ConversationAcceptanceEffectError",
    "LIFE_CONVERSATION_PROJECTION_SCHEMA_V1",
    "PREFERENCE_SIGNAL_SCHEMA_V1",
    "conversation_life_command_id",
    "preference_operation_id",
]
