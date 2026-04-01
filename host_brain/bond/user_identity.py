"""Helpers for single-user Bond identity normalization."""

from __future__ import annotations

import os
from typing import Iterable


DEFAULT_BOND_USER_ID = "default"


def get_canonical_bond_user_id() -> str:
    value = str(os.environ.get("HOST_DEFAULT_USER_ID") or "").strip()
    return value or DEFAULT_BOND_USER_ID


def resolve_person_id(
    person_id: str | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    platform: str | None = None,
    from_id: str | None = None,
    conversation_id: str | None = None,
) -> str:
    explicit = str(person_id or "").strip()
    if explicit:
        return explicit

    normalized_platform = str(platform or "").strip().lower()
    normalized_from_id = str(from_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    normalized_conversation_id = str(conversation_id or "").strip()

    upstream_identity = (
        normalized_user_id
        or (f"{normalized_platform}:{normalized_from_id}" if normalized_platform and normalized_from_id else "")
        or normalized_from_id
        or normalized_conversation_id
        or normalized_session_id
    )
    return resolve_bond_user_id(
        upstream_identity or None,
        session_id=normalized_session_id or None,
    )


def resolve_bond_user_id(
    user_id: str | None = None,
    *,
    session_id: str | None = None,
) -> str:
    # This deployment runs Bond as a single-user system, so all inbound
    # identities collapse into one canonical user id.
    _ = user_id, session_id
    return get_canonical_bond_user_id()


def collapse_bond_user_ids(user_ids: Iterable[str] | None) -> list[str]:
    has_any = any(str(user_id or "").strip() for user_id in (user_ids or []))
    if not has_any:
        return []
    return [get_canonical_bond_user_id()]
