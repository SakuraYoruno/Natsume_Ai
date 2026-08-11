"""Server-derived conversation scope shared by Gateway ingress and Runtime Context.

The signed Gateway envelope authenticates the transport principal and tenant,
but its message body still contains transport identifiers such as ``from_id``
and ``conversation_id``.  Those fields must be bound exactly once *before*
any affect, relationship, memory, or Scene consumer runs.  This module keeps
the resulting scope secret-free and deterministic: raw transport identifiers
are used only as input to opaque digests and never appear in the frozen scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA = (
    "natsume.secure-gateway-conversation-scope.v1"
)
SECURE_GATEWAY_CONVERSATION_SCOPE_KIND = "SECURE_GATEWAY"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_OPAQUE_IDENTIFIER_RE = re.compile(r"^(?:subject|session)-[0-9a-f]{40}$")


class GatewayConversationScopeError(ValueError):
    """A Gateway request cannot be assigned a safe relationship scope."""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identifier(value: Any, *, prefix: str, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise GatewayConversationScopeError(
                f"GATEWAY_CONVERSATION_SCOPE_{prefix.upper()}_REQUIRED"
            )
        return ""
    if _IDENTIFIER_RE.fullmatch(text):
        return text
    return f"{prefix}-{_sha256(text)[:40]}"


def _opaque_identifier(*, prefix: str, namespace: str, value: str) -> str:
    return f"{prefix}-{_sha256(f'{namespace}|{value}')[:40]}"


@dataclass(frozen=True, slots=True)
class SecureGatewayConversationScopeV1:
    """A transport-attested scope for one Gateway conversation.

    ``relationship_subject_id`` stays stable for the same signed sender across
    conversations.  ``session_id`` is deliberately narrower and changes with
    the signed conversation.  Both are opaque so a durable Life/World journal
    never needs to retain raw platform identifiers.
    """

    principal_id: str
    tenant_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    scope_digest: str

    @classmethod
    def derive(
        cls,
        *,
        principal_id: Any,
        tenant_id: Any,
        platform: Any,
        from_id: Any = None,
        person_id: Any = None,
        conversation_id: Any,
    ) -> "SecureGatewayConversationScopeV1":
        """Derive a scope from trusted ingress authority and signed body IDs.

        ``from_id`` is the preferred transport subject.  A signed adapter may
        supply ``person_id`` when its platform has no separate sender field;
        neither field is copied into the resulting scope.  A conversation ID
        is not a safe replacement for a sender, because group conversations
        would otherwise merge distinct relationships.
        """

        principal = _safe_identifier(principal_id, prefix="principal")
        tenant = _safe_identifier(tenant_id, prefix="tenant")
        normalized_platform = _safe_identifier(platform, prefix="platform")
        raw_sender = str(from_id or "").strip() or str(person_id or "").strip()
        if not raw_sender:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_TRANSPORT_SUBJECT_REQUIRED"
            )
        raw_conversation = str(conversation_id or "").strip()
        if not raw_conversation:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_CONVERSATION_REQUIRED"
            )

        sender_namespace = "|".join(
            (
                SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA,
                principal,
                tenant,
                normalized_platform.lower(),
            )
        )
        relationship_subject_id = _opaque_identifier(
            prefix="subject",
            namespace=sender_namespace,
            value=raw_sender,
        )
        session_id = _opaque_identifier(
            prefix="session",
            namespace=sender_namespace,
            value=raw_conversation,
        )
        workspace_id = f"tenant:{tenant}"
        payload_without_digest = {
            "schema": SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA,
            "kind": SECURE_GATEWAY_CONVERSATION_SCOPE_KIND,
            "principal_id": principal,
            "tenant_id": tenant,
            "workspace_id": workspace_id,
            "relationship_subject_id": relationship_subject_id,
            "session_id": session_id,
        }
        return cls(
            principal_id=principal,
            tenant_id=tenant,
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
            scope_digest="sha256:" + _sha256(_canonical_json(payload_without_digest)),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SecureGatewayConversationScopeV1":
        """Reopen and validate an already-derived command authority scope."""

        raw = dict(payload)
        if raw.get("schema") != SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_SCHEMA_INVALID"
            )
        if raw.get("kind") != SECURE_GATEWAY_CONVERSATION_SCOPE_KIND:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_KIND_INVALID"
            )
        try:
            principal = _safe_identifier(raw.get("principal_id"), prefix="principal")
            tenant = _safe_identifier(raw.get("tenant_id"), prefix="tenant")
            workspace = _safe_identifier(raw.get("workspace_id"), prefix="workspace")
            subject = _safe_identifier(
                raw.get("relationship_subject_id"),
                prefix="subject",
            )
            session = _safe_identifier(raw.get("session_id"), prefix="session")
        except GatewayConversationScopeError as exc:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_PAYLOAD_INVALID"
            ) from exc
        if workspace != f"tenant:{tenant}":
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_WORKSPACE_DRIFT"
            )
        if not _OPAQUE_IDENTIFIER_RE.fullmatch(subject) or not _OPAQUE_IDENTIFIER_RE.fullmatch(session):
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_OPAQUE_ID_INVALID"
            )
        payload_without_digest = {
            "schema": SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA,
            "kind": SECURE_GATEWAY_CONVERSATION_SCOPE_KIND,
            "principal_id": principal,
            "tenant_id": tenant,
            "workspace_id": workspace,
            "relationship_subject_id": subject,
            "session_id": session,
        }
        expected_digest = "sha256:" + _sha256(_canonical_json(payload_without_digest))
        actual_digest = str(raw.get("scope_digest") or "").strip()
        if actual_digest != expected_digest:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_DIGEST_DRIFT"
            )
        return cls(
            principal_id=principal,
            tenant_id=tenant,
            workspace_id=workspace,
            relationship_subject_id=subject,
            session_id=session,
            scope_digest=actual_digest,
        )

    @classmethod
    def from_command_authority(
        cls,
        authority: Mapping[str, Any] | None,
    ) -> "SecureGatewayConversationScopeV1 | None":
        raw = dict(authority or {}).get("secure_gateway_conversation_scope_v1")
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_AUTHORITY_INVALID"
            )
        return cls.from_dict(raw)

    def assert_bound_to_authority(self, authority: Mapping[str, Any] | None) -> None:
        """Reject a scope copied across a different Secure Ingress authority."""

        raw = dict(authority or {})
        principal = _safe_identifier(raw.get("principal_id"), prefix="principal")
        tenant = _safe_identifier(raw.get("tenant_id"), prefix="tenant")
        if principal != self.principal_id or tenant != self.tenant_id:
            raise GatewayConversationScopeError(
                "GATEWAY_CONVERSATION_SCOPE_INGRESS_AUTHORITY_DRIFT"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA,
            "kind": SECURE_GATEWAY_CONVERSATION_SCOPE_KIND,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "scope_digest": self.scope_digest,
        }

    def as_command_authority(self) -> dict[str, dict[str, str]]:
        return {"secure_gateway_conversation_scope_v1": self.to_dict()}


__all__ = [
    "GatewayConversationScopeError",
    "SECURE_GATEWAY_CONVERSATION_SCOPE_KIND",
    "SECURE_GATEWAY_CONVERSATION_SCOPE_SCHEMA",
    "SecureGatewayConversationScopeV1",
]
