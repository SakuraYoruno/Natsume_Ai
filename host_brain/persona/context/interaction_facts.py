"""Read-only interaction facts shared by persona policy and app assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InteractionFactsPlane:
    """Facts only; reply policy and persistence remain outside this contract."""

    user_message: str = ""
    history_turns: list[dict[str, Any]] = field(default_factory=list)
    older_summary: str = ""
    relationship_stage: str = "FRIEND"
    relationship_state: dict[str, Any] = field(default_factory=dict)
    affect_state: dict[str, Any] = field(default_factory=dict)
    drive_state: dict[str, Any] = field(default_factory=dict)
    conversation_interpretation: dict[str, Any] = field(default_factory=dict)
    ongoing_thread: dict[str, Any] = field(default_factory=dict)
    emotional_act: dict[str, Any] = field(default_factory=dict)
    social_act: dict[str, Any] = field(default_factory=dict)
    perception: dict[str, Any] = field(default_factory=dict)
    memory_snippets: list[str] = field(default_factory=list)
    route_seed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "history_turns": list(self.history_turns or []),
            "older_summary": self.older_summary,
            "relationship_stage": self.relationship_stage,
            "relationship_state": dict(self.relationship_state or {}),
            "affect_state": dict(self.affect_state or {}),
            "drive_state": dict(self.drive_state or {}),
            "conversation_interpretation": dict(self.conversation_interpretation or {}),
            "ongoing_thread": dict(self.ongoing_thread or {}),
            "emotional_act": dict(self.emotional_act or {}),
            "social_act": dict(self.social_act or {}),
            "perception": dict(self.perception or {}),
            "memory_snippets": list(self.memory_snippets or []),
            "route_seed": dict(self.route_seed or {}),
        }


__all__ = ["InteractionFactsPlane"]
