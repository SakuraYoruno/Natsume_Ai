"""Helpers for single-user Bond identity normalization."""

from __future__ import annotations

import os
from typing import Iterable


DEFAULT_BOND_USER_ID = "default"


def get_canonical_bond_user_id() -> str:
    value = str(os.environ.get("HOST_DEFAULT_USER_ID") or "").strip()
    return value or DEFAULT_BOND_USER_ID


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
