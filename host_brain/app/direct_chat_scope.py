"""Server-owned identity scope for the local ``POST /v1/chat`` surface.

``/v1/chat`` is the personal Host UI surface, not an alternative Gateway
ingress.  It has no signed multi-user identity envelope, so caller-selected
``person_id`` and ``workspace_id`` cannot be allowed to select a relationship
or Life aggregate.  The durable Gateway path owns that responsibility through
Secure Ingress at ``/v1/host/input``.

This module deliberately does *not* manufacture an ingress claim, audit ref,
or tenant.  The resulting authority is useful only for deriving one local
personal Runtime Context; it must never be mistaken for authenticated external
transport authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from host_brain.bond.user_identity import get_canonical_bond_user_id


DIRECT_CHAT_SCOPE_SCHEMA = "natsume.direct-chat-scope.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


def _stable_identifier(value: Any, *, prefix: str) -> str:
    """Return an opaque bounded identifier without trusting request fields."""

    text = str(value or "").strip()
    if _IDENTIFIER.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class DirectChatScopeV1:
    """One server-derived local-personal conversation scope.

    ``relationship_subject_id`` and ``principal_id`` happen to be the same on
    the unauthenticated single-user surface.  They remain distinct fields so a
    signed Gateway transport cannot accidentally inherit this shortcut.
    """

    principal_id: str
    relationship_subject_id: str
    workspace_id: str
    session_id: str
    client_person_overridden: bool
    client_workspace_overridden: bool

    def as_command_authority(self) -> dict[str, str]:
        """Return non-Secure-Ingress context provenance for the Host runtime."""

        return {
            "direct_chat_scope_schema": DIRECT_CHAT_SCOPE_SCHEMA,
            "direct_chat_scope_kind": "LOCAL_PERSONAL_UNATTESTED",
            "principal_id": self.principal_id,
        }

    def as_trace(self) -> dict[str, Any]:
        """Expose secret-free provenance without preserving client identities."""

        return {
            "schema_version": DIRECT_CHAT_SCOPE_SCHEMA,
            "kind": "LOCAL_PERSONAL_UNATTESTED",
            "principal_id": self.principal_id,
            "relationship_subject_id": self.relationship_subject_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "client_person_overridden": self.client_person_overridden,
            "client_workspace_overridden": self.client_workspace_overridden,
        }


def bind_direct_chat_scope(
    request_body: Any,
    *,
    session_id: str,
) -> DirectChatScopeV1:
    """Bind a mutable ChatRequest to the configured local companion identity.

    The explicit mutation is intentional: AffectFusion, retrieval prefetch,
    Runtime Context, Surface, and accepted conversation effects all consume
    the same normalized request object.  Keeping a parallel scope side-channel
    would reintroduce a pre-freeze workspace selection gap.
    """

    requested_person = str(getattr(request_body, "person_id", "") or "").strip()
    requested_workspace = str(
        getattr(request_body, "workspace_id", "") or ""
    ).strip()
    principal_id = _stable_identifier(
        get_canonical_bond_user_id(),
        prefix="local-person",
    )
    normalized_session = _stable_identifier(
        session_id,
        prefix="session",
    )
    workspace_id = f"personal:{principal_id}"
    scope = DirectChatScopeV1(
        principal_id=principal_id,
        relationship_subject_id=principal_id,
        workspace_id=workspace_id,
        session_id=normalized_session,
        client_person_overridden=bool(
            requested_person and requested_person != principal_id
        ),
        client_workspace_overridden=bool(
            requested_workspace and requested_workspace != workspace_id
        ),
    )
    # ChatRequest is the typed local request model.  Do not accept a generic
    # mapping here: any other ingress must bind a signed identity instead.
    for field_name, value in (
        ("person_id", scope.relationship_subject_id),
        ("workspace_id", scope.workspace_id),
        ("session_id", scope.session_id),
    ):
        try:
            setattr(request_body, field_name, value)
        except (AttributeError, TypeError) as exc:
            raise TypeError("direct chat request must expose mutable typed scope fields") from exc
    return scope


__all__ = [
    "DIRECT_CHAT_SCOPE_SCHEMA",
    "DirectChatScopeV1",
    "bind_direct_chat_scope",
]
