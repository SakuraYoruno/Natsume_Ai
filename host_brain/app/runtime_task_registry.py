from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.ids import now_ms

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_RECOVERING = "RECOVERING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


@dataclass
class RuntimeTaskSnapshot:
    task_id: str
    task_kind: str
    target_id: str
    request_id: str
    session_id: str
    person_id: str
    user_message: str
    source: str = "dispatch_agent"
    status: str = STATUS_PENDING
    detail: str = ""
    progress_message: str = ""
    result_code: str | None = None
    error: str | None = None
    result_text: str = ""
    recovery_plan: dict[str, Any] | None = None
    recovery_status: str | None = None
    recovery_error: str | None = None
    recovery_result_text: str = ""
    child_task_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_ts_ms: int = field(default_factory=now_ms)
    updated_ts_ms: int = field(default_factory=now_ms)
    finished_ts_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_kind": self.task_kind,
            "target_id": self.target_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "person_id": self.person_id,
            "user_message": self.user_message,
            "source": self.source,
            "status": self.status,
            "detail": self.detail,
            "progress_message": self.progress_message,
            "result_code": self.result_code,
            "error": self.error,
            "result_text": self.result_text,
            "recovery_plan": dict(self.recovery_plan or {}),
            "recovery_status": self.recovery_status,
            "recovery_error": self.recovery_error,
            "recovery_result_text": self.recovery_result_text,
            "child_task_ids": list(self.child_task_ids),
            "meta": dict(self.meta or {}),
            "created_ts_ms": self.created_ts_ms,
            "updated_ts_ms": self.updated_ts_ms,
            "finished_ts_ms": self.finished_ts_ms,
        }


class RuntimeTaskRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, RuntimeTaskSnapshot] = {}
        self._request_to_task: dict[str, str] = {}

    def create_task(
        self,
        *,
        task_kind: str,
        target_id: str,
        request_id: str,
        session_id: str,
        person_id: str,
        user_message: str,
        source: str = "dispatch_agent",
        detail: str = "",
        progress_message: str = "",
        meta: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
    ) -> RuntimeTaskSnapshot:
        snapshot = RuntimeTaskSnapshot(
            task_id=f"task_{uuid.uuid4().hex}",
            task_kind=str(task_kind or "task").strip() or "task",
            target_id=str(target_id or "").strip(),
            request_id=str(request_id or "").strip(),
            session_id=str(session_id or "").strip(),
            person_id=str(person_id or "").strip(),
            user_message=str(user_message or "").strip(),
            source=str(source or "dispatch_agent").strip() or "dispatch_agent",
            detail=str(detail or "").strip(),
            progress_message=str(progress_message or detail or "").strip(),
            meta=dict(meta or {}),
            status=STATUS_RUNNING,
        )
        with self._lock:
            self._tasks[snapshot.task_id] = snapshot
            if snapshot.request_id:
                self._request_to_task[snapshot.request_id] = snapshot.task_id
            if parent_task_id and parent_task_id in self._tasks:
                parent = self._tasks[parent_task_id]
                if snapshot.task_id not in parent.child_task_ids:
                    parent.child_task_ids.append(snapshot.task_id)
                    parent.updated_ts_ms = now_ms()
        return self.get(snapshot.task_id) or snapshot

    def get(self, task_id: str) -> RuntimeTaskSnapshot | None:
        with self._lock:
            snapshot = self._tasks.get(str(task_id or "").strip())
            return None if snapshot is None else RuntimeTaskSnapshot(**snapshot.as_dict())

    def get_by_request_id(self, request_id: str) -> RuntimeTaskSnapshot | None:
        with self._lock:
            task_id = self._request_to_task.get(str(request_id or "").strip())
        return self.get(task_id) if task_id else None

    def update_task(self, task_id: str, **fields: Any) -> RuntimeTaskSnapshot | None:
        key = str(task_id or "").strip()
        if not key:
            return None
        with self._lock:
            snapshot = self._tasks.get(key)
            if snapshot is None:
                return None
            for field_name, value in fields.items():
                if not hasattr(snapshot, field_name):
                    continue
                setattr(snapshot, field_name, value)
            snapshot.updated_ts_ms = now_ms()
            if snapshot.status in {STATUS_COMPLETED, STATUS_FAILED} and snapshot.finished_ts_ms is None:
                snapshot.finished_ts_ms = snapshot.updated_ts_ms
            return RuntimeTaskSnapshot(**snapshot.as_dict())

    def mark_completed(self, task_id: str, *, detail: str = "", result_text: str = "", result_code: str | None = None) -> RuntimeTaskSnapshot | None:
        return self.update_task(
            task_id,
            status=STATUS_COMPLETED,
            detail=str(detail or "").strip(),
            progress_message=str(detail or "").strip(),
            result_text=str(result_text or ""),
            result_code=(str(result_code).strip() if result_code else None),
        )

    def mark_failed(self, task_id: str, *, detail: str = "", error: str = "", result_code: str | None = None, result_text: str = "") -> RuntimeTaskSnapshot | None:
        return self.update_task(
            task_id,
            status=STATUS_FAILED,
            detail=str(detail or "").strip(),
            progress_message=str(detail or "").strip(),
            error=str(error or "").strip() or None,
            result_code=(str(result_code).strip() if result_code else None),
            result_text=str(result_text or ""),
        )

    def mark_recovering(self, task_id: str, *, detail: str = "", recovery_plan: dict[str, Any] | None = None) -> RuntimeTaskSnapshot | None:
        return self.update_task(
            task_id,
            status=STATUS_RECOVERING,
            detail=str(detail or "").strip(),
            progress_message=str(detail or "").strip(),
            recovery_plan=dict(recovery_plan or {}),
            recovery_status="RUNNING",
        )

    def attach_recovery_result(self, task_id: str, *, recovery_status: str, recovery_result_text: str = "", recovery_error: str = "") -> RuntimeTaskSnapshot | None:
        return self.update_task(
            task_id,
            recovery_status=str(recovery_status or "").strip() or None,
            recovery_result_text=str(recovery_result_text or ""),
            recovery_error=str(recovery_error or "").strip() or None,
        )

    def find_recent_tasks(
        self,
        *,
        person_id: str,
        session_id: str | None = None,
        statuses: set[str] | None = None,
        exclude_request_id: str | None = None,
        limit: int = 5,
    ) -> list[RuntimeTaskSnapshot]:
        normalized_person = str(person_id or "").strip()
        normalized_session = str(session_id or "").strip()
        normalized_exclude_request = str(exclude_request_id or "").strip()
        wanted_statuses = {str(s).strip().upper() for s in set(statuses or set()) if str(s).strip()}
        with self._lock:
            tasks = list(self._tasks.values())
        matched: list[RuntimeTaskSnapshot] = []
        for snapshot in tasks:
            if normalized_person and snapshot.person_id != normalized_person:
                continue
            if normalized_exclude_request and snapshot.request_id == normalized_exclude_request:
                continue
            if wanted_statuses and str(snapshot.status).upper() not in wanted_statuses:
                continue
            if normalized_session and snapshot.session_id == normalized_session:
                matched.append(RuntimeTaskSnapshot(**snapshot.as_dict()))
                continue
            matched.append(RuntimeTaskSnapshot(**snapshot.as_dict()))
        matched.sort(key=lambda item: (-(item.updated_ts_ms or 0), -(item.created_ts_ms or 0), item.task_id))
        return matched[: max(1, int(limit))]

    def summarize_for_chat(
        self,
        *,
        person_id: str,
        session_id: str,
        exclude_request_id: str | None = None,
    ) -> dict[str, Any] | None:
        active = self.find_recent_tasks(
            person_id=person_id,
            session_id=session_id,
            statuses={STATUS_RUNNING, STATUS_RECOVERING},
            exclude_request_id=exclude_request_id,
            limit=1,
        )
        if active:
            task = active[0]
            elapsed_ms = max(0, now_ms() - int(task.created_ts_ms or now_ms()))
            child_tasks = [self.get(child_id) for child_id in list(task.child_task_ids or [])]
            child_tasks = [child for child in child_tasks if child is not None]
            return {
                "found": True,
                "task_id": task.task_id,
                "status": task.status,
                "target_id": task.target_id,
                "task_kind": task.task_kind,
                "detail": task.detail,
                "progress_message": task.progress_message,
                "elapsed_ms": elapsed_ms,
                "recovery_plan": dict(task.recovery_plan or {}),
                "recovery_status": task.recovery_status,
                "recovery_error": task.recovery_error,
                "recovery_result_text": task.recovery_result_text,
                "child_tasks": [child.as_dict() for child in child_tasks],
            }
        recent = self.find_recent_tasks(
            person_id=person_id,
            session_id=session_id,
            statuses={STATUS_COMPLETED, STATUS_FAILED},
            exclude_request_id=exclude_request_id,
            limit=1,
        )
        if not recent:
            return None
        task = recent[0]
        child_tasks = [self.get(child_id) for child_id in list(task.child_task_ids or [])]
        child_tasks = [child for child in child_tasks if child is not None]
        return {
            "found": True,
            "task_id": task.task_id,
            "status": task.status,
            "target_id": task.target_id,
            "task_kind": task.task_kind,
            "detail": task.detail,
            "progress_message": task.progress_message,
            "elapsed_ms": max(0, int((task.finished_ts_ms or task.updated_ts_ms or now_ms()) - (task.created_ts_ms or now_ms()))),
            "result_code": task.result_code,
            "error": task.error,
            "result_text": task.result_text,
            "recovery_plan": dict(task.recovery_plan or {}),
            "recovery_status": task.recovery_status,
            "recovery_error": task.recovery_error,
            "recovery_result_text": task.recovery_result_text,
            "child_tasks": [child.as_dict() for child in child_tasks],
        }
