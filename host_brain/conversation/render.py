from __future__ import annotations

from typing import Any, Mapping

from .schema import Turn


def turns_to_messages(turns: list[Turn | Mapping[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in turns:
        corrupted = bool(turn.corrupted) if isinstance(turn, Turn) else bool(turn.get("corrupted"))
        if corrupted:
            continue
        content = str(turn.content if isinstance(turn, Turn) else turn.get("content") or "").strip()
        if not content:
            continue
        raw_role = turn.role if isinstance(turn, Turn) else turn.get("role")
        role = "assistant" if str(raw_role or "").strip().lower() == "assistant" else "user"
        messages.append({"role": role, "content": content})
    return messages


def build_chat_messages(
    *,
    system_prompt: str,
    history_turns: list[Turn | Mapping[str, Any]],
    user_message: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    base_system = str(system_prompt or "").strip()
    if base_system:
        messages.append({"role": "system", "content": base_system})
    messages.extend(turns_to_messages(history_turns))
    messages.append({"role": "user", "content": str(user_message or "")})
    return messages
