"""App-local read dependencies for prompt and interaction context assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ContextProvider = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ConversationContextReadPorts:
    """Providers captured by one Host lifespan for context-only reads."""

    conversation_history: ContextProvider
    conversation_turn_store: ContextProvider
    session_topic_ledger_store: ContextProvider
    session_tactic_ledger_store: ContextProvider

    def __post_init__(self) -> None:
        for field_name in (
            "conversation_history",
            "conversation_turn_store",
            "session_topic_ledger_store",
            "session_tactic_ledger_store",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


__all__ = ["ContextProvider", "ConversationContextReadPorts"]
