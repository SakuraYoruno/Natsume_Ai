"""Pure readers for the versioned chat runtime payload."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def get_chat_runtime_emotion_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    emotion = _section(payload, "emotion")
    state = emotion.get("state")
    if isinstance(state, Mapping):
        return dict(state)
    legacy = payload.get("emotion_context")
    return dict(legacy) if isinstance(legacy, Mapping) else {}


def get_chat_runtime_graph(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _section(payload, "graph")


__all__ = ["get_chat_runtime_emotion_state", "get_chat_runtime_graph"]
