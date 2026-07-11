"""Legacy Agent job registry with opt-in durable shadow dual-write.

The in-memory registry remains the sole read authority in TASK-0467.  A
``DurableTaskStore`` may be attached to mirror mutations, run shadow reads, and
produce explainable reconciliation without changing API behavior.
"""

from __future__ import annotations

import threading
from typing import Any, Literal

from host_brain.agent_runtime.task_store import DurableTaskStore, TaskStoreError
from host_brain.core.ids import new_request_id, now_ms


class AgentJobRegistry:
    def __init__(
        self,
        *,
        task_store: DurableTaskStore | None = None,
        read_mode: Literal["legacy", "shadow"] = "legacy",
    ) -> None:
        if read_mode not in {"legacy", "shadow"}:
            raise ValueError("TASK-0467 permits only legacy or shadow read mode")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._task_store = task_store
        self._read_mode: Literal["legacy", "shadow"] = read_mode
        self._mirror_errors: dict[str, dict[str, str]] = {}
        self._reconciliation: dict[str, dict[str, Any]] = {}

    @property
    def read_authority(self) -> str:
        """Return the immutable authority for this compatibility phase."""

        return "LEGACY"

    def configure_shadow_store(
        self,
        task_store: DurableTaskStore | None,
        *,
        read_mode: Literal["legacy", "shadow"] = "shadow",
    ) -> None:
        """Attach a migrated store without enabling durable authority."""

        if read_mode not in {"legacy", "shadow"}:
            raise ValueError("TASK-0467 permits only legacy or shadow read mode")
        with self._lock:
            self._task_store = task_store
            self._read_mode = read_mode
            self._mirror_errors.clear()
            self._reconciliation.clear()

    def create(self, *, kind: str, work_order: dict[str, Any]) -> dict[str, Any]:
        job_id = f"agent-job::{new_request_id()}"
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
        self._mirror(job)
        return dict(job)

    def update(self, job_id: str, **updates: Any) -> dict[str, Any] | None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return None
            current.update(updates)
            current["updated_at_ms"] = now_ms()
            self._jobs[job_id] = current
            updated = dict(current)
        self._mirror(updated)
        return updated

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            current = self._jobs.get(job_id)
            legacy = dict(current) if isinstance(current, dict) else None
            task_store = self._task_store
            read_mode = self._read_mode
        if read_mode == "shadow" and task_store is not None:
            report = self.reconcile(job_id, legacy_job=legacy)
            with self._lock:
                self._reconciliation[job_id] = report
        return legacy

    def reconcile(
        self,
        job_id: str,
        *,
        legacy_job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task_store = self._task_store
            mirror_error = self._mirror_errors.get(job_id)
            current = self._jobs.get(job_id)
            legacy = (
                dict(legacy_job)
                if isinstance(legacy_job, dict)
                else dict(current)
                if isinstance(current, dict)
                else None
            )
        if task_store is None:
            return {
                "job_id": str(job_id),
                "authority": "LEGACY",
                "classification": "SHADOW_DISABLED",
                "match": False,
                "differences": [],
                "legacy_summary": _job_summary(legacy),
                "durable_shadow": None,
                "reason_code": "TASK_STORE_SHADOW_DISABLED",
            }
        if mirror_error is not None:
            return {
                "job_id": str(job_id),
                "authority": "LEGACY",
                "classification": "MIRROR_WRITE_FAILED",
                "match": False,
                "differences": [],
                "legacy_summary": _job_summary(legacy),
                "durable_shadow": None,
                "reason_code": mirror_error["reason_code"],
                "detail": mirror_error["detail"],
            }
        try:
            return task_store.reconcile_legacy_job(job_id, legacy)
        except TaskStoreError as exc:
            error = exc.to_dict()
        except Exception as exc:  # a shadow read cannot change legacy read behavior
            error = {
                "reason_code": "TASK_STORE_SHADOW_READ_FAILED",
                "detail": str(exc),
            }
        return {
            "job_id": str(job_id),
            "authority": "LEGACY",
            "classification": "SHADOW_READ_FAILED",
            "match": False,
            "differences": [],
            "legacy_summary": _job_summary(legacy),
            "durable_shadow": None,
            **error,
        }

    def get_reconciliation(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            report = self._reconciliation.get(job_id)
            return dict(report) if isinstance(report, dict) else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(job)
                for _, job in sorted(self._jobs.items(), key=lambda item: item[0])
            ]

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._mirror_errors.clear()
            self._reconciliation.clear()

    def _mirror(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "")
        with self._lock:
            task_store = self._task_store
        if task_store is None:
            return
        try:
            task_store.mirror_legacy_job(job)
        except TaskStoreError as exc:
            error = exc.to_dict()
            with self._lock:
                self._mirror_errors[job_id] = error
            return
        except Exception as exc:  # shadow failures must not change legacy behavior
            with self._lock:
                self._mirror_errors[job_id] = {
                    "reason_code": "TASK_STORE_SHADOW_WRITE_FAILED",
                    "detail": str(exc),
                }
            return
        try:
            report = task_store.reconcile_legacy_job(job_id, job)
        except Exception as exc:  # shadow reads remain non-authoritative
            with self._lock:
                self._mirror_errors[job_id] = {
                    "reason_code": "TASK_STORE_SHADOW_READ_FAILED",
                    "detail": str(exc),
                }
            return
        with self._lock:
            self._mirror_errors.pop(job_id, None)
            self._reconciliation[job_id] = report


def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    return {
        "job_id": str(job.get("job_id") or ""),
        "kind": str(job.get("kind") or ""),
        "status": str(job.get("status") or ""),
        "created_at_ms": int(job.get("created_at_ms") or 0),
        "updated_at_ms": int(job.get("updated_at_ms") or 0),
    }


GLOBAL_AGENT_JOB_REGISTRY = AgentJobRegistry()


__all__ = ["AgentJobRegistry", "GLOBAL_AGENT_JOB_REGISTRY"]
