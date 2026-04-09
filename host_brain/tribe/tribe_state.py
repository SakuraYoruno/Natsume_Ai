from __future__ import annotations

import threading
from typing import Any

from host_brain.core.ids import now_ms


class TribeStateRegistry:
    """最小 Tribe 状态注册表：按命名空间保存状态快照。"""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def set_state(self, namespace: str, state: dict[str, Any], *, writer: str | None = None) -> dict[str, Any]:
        ns = str(namespace).strip() or "default"
        with self._lock:
            snapshot = {
                "namespace": ns,
                "state": dict(state or {}),
                "writer": (str(writer).strip() or None) if writer is not None else None,
                "updated_at_ms": now_ms(),
            }
            self._states[ns] = snapshot
            return dict(snapshot)

    def get_state(self, namespace: str) -> dict[str, Any] | None:
        ns = str(namespace).strip() or "default"
        with self._lock:
            item = self._states.get(ns)
            return dict(item) if isinstance(item, dict) else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {ns: dict(data) for ns, data in self._states.items()}


__all__ = ["TribeStateRegistry"]
