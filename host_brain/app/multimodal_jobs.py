from __future__ import annotations

import threading
import time
import uuid
from typing import Any


_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def create_multimodal_job(
    *,
    request_id: str,
    conversation_id: str,
    platform: str,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job_id = f"mmjob_{uuid.uuid4().hex[:12]}"
    now_ms = int(time.time() * 1000)
    job = {
        "job_id": job_id,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "platform": platform,
        "job_type": job_type,
        "status": "PENDING",
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "payload": dict(payload or {}),
        "result": {},
        "error": None,
    }
    with _JOB_LOCK:
        _JOBS[job_id] = job
    return dict(job)


def update_multimodal_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated_at_ms"] = int(time.time() * 1000)
        return dict(job)


def get_multimodal_job(job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


__all__ = [
    "create_multimodal_job",
    "get_multimodal_job",
    "update_multimodal_job",
]
