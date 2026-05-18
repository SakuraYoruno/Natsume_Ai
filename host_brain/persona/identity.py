"""Canonical identity helpers for Natsume's own persona state.

Bond and memory ids describe the human side of a conversation.  Inner-world,
default-mode and persistent-self ids describe Natsume herself.  Keeping those
namespaces separate prevents live runs from writing self state under generic
human ids such as ``default`` or legacy fixture ids such as ``user_1``.
"""

from __future__ import annotations

import os
from typing import Any


DEFAULT_PERSONA_SELF_ID = "natsume_ai"
RESERVED_PERSONA_SELF_IDS = frozenset({"", "default", "system", "user_1"})


def get_canonical_persona_self_id() -> str:
    value = str(
        os.environ.get("NATSUME_PERSONA_SELF_ID")
        or os.environ.get("HOST_PERSONA_SELF_ID")
        or ""
    ).strip()
    if value.lower() in RESERVED_PERSONA_SELF_IDS:
        return DEFAULT_PERSONA_SELF_ID
    return value or DEFAULT_PERSONA_SELF_ID


def resolve_persona_self_id(value: Any = None) -> str:
    text = str(value or "").strip()
    if text.lower() in RESERVED_PERSONA_SELF_IDS:
        return get_canonical_persona_self_id()
    return text


def is_reserved_persona_self_id(value: Any) -> bool:
    return str(value or "").strip().lower() in RESERVED_PERSONA_SELF_IDS


__all__ = [
    "DEFAULT_PERSONA_SELF_ID",
    "RESERVED_PERSONA_SELF_IDS",
    "get_canonical_persona_self_id",
    "is_reserved_persona_self_id",
    "resolve_persona_self_id",
]
