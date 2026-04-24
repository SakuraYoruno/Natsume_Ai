from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

TurnRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class Turn:
    """Immutable conversation turn stored for short-term continuity."""

    turn_id: str
    session_id: str
    person_id: str
    request_id: str
    role: TurnRole
    content: str
    ts_ms: int
    turn_index: int
    lang: str = "zh"
    token_count: int | None = None
    corrupted: bool = False
    metadata: dict[str, Any] | None = None

