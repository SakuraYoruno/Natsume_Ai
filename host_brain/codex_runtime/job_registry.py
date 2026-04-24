"""In-memory Codex job registry for internal APIs."""

from __future__ import annotations

import threading
from typing import Any

from host_brain.core.ids import new_request_id, now_ms


class CodexJobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, *, kind: str, work_order: dict[str, Any]) -> dict[str, Any]:
        job_id = f"codex-job::{new_request_id()}"
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "QUEUED",
            "work_order": dict(work_order),
            "result": None,
            "created_at_ms": now_ms(),
            "updated_at_ms": now_ms(),
        }
        with self._lock:
            self._jobs[job_id] = dict(job)
        return dict(job)

    def update(self, job_id: str, **updates: Any) -> dict[str, Any] | None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return None
            current.update(updates)
            current["updated_at_ms"] = now_ms()
            self._jobs[job_id] = current
            return dict(current)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            current = self._jobs.get(job_id)
            return dict(current) if isinstance(current, dict) else None

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()


GLOBAL_CODEX_JOB_REGISTRY = CodexJobRegistry()


__all__ = ["CodexJobRegistry", "GLOBAL_CODEX_JOB_REGISTRY"]
