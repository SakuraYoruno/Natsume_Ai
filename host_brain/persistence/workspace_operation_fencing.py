"""Cross-process fencing for workspace side effects.

This module owns no SQLite schema or connection.  Its mixin calls the
repository's short-transaction hooks before and after an external callback,
keeping ``WorkspaceRepository`` as the sole durable authority.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, TypeVar


WORKSPACE_AUTHORITY_INVALID = "WORKSPACE_AUTHORITY_INVALID"
WORKSPACE_STALE_FENCE = "WORKSPACE_STALE_FENCE"
WORKSPACE_OPERATION_TIMEOUT = "WORKSPACE_OPERATION_TIMEOUT"

_LIVE_STATUSES = frozenset({"ACTIVE", "CLEANUP_PENDING"})
_LOCK_NAMESPACE = re.compile(r"^[A-Za-z0-9_-]+$")
_THREAD_FILE_LOCKS: dict[str, threading.RLock] = {}
_THREAD_FILE_LOCKS_GUARD = threading.Lock()

T = TypeVar("T")
ErrorFactory = Callable[[str, str], BaseException]


@dataclass(frozen=True, slots=True)
class WorkspaceOperationContext:
    """Exact pre-authority plus the one deadline shared by all side effects."""

    authority: Any
    deadline_monotonic: float
    _error_factory: ErrorFactory = field(repr=False, compare=False)

    def remaining_s(self) -> float:
        remaining = self.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise self._error_factory(
                WORKSPACE_OPERATION_TIMEOUT,
                "workspace operation exhausted its lease-bounded deadline",
            )
        return remaining


def _thread_file_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _THREAD_FILE_LOCKS_GUARD:
        return _THREAD_FILE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _process_file_lock(
    path: Path,
    *,
    deadline_monotonic: float,
    error_factory: ErrorFactory,
) -> Iterator[None]:
    """Deadline-aware standard-library advisory lock for Windows and POSIX."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    thread_lock = _thread_file_lock(path)
    while not thread_lock.acquire(blocking=False):
        if time.monotonic() >= deadline_monotonic:
            raise error_factory(
                WORKSPACE_OPERATION_TIMEOUT,
                "workspace cross-process lock deadline was exhausted",
            )
        time.sleep(0.01)
    handle = None
    try:
        handle = path.open("a+b", buffering=0)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline_monotonic:
                        raise error_factory(
                            WORKSPACE_OPERATION_TIMEOUT,
                            "workspace cross-process lock deadline was exhausted",
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif os.name == "posix":
            import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline_monotonic:
                        raise error_factory(
                            WORKSPACE_OPERATION_TIMEOUT,
                            "workspace cross-process lock deadline was exhausted",
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:  # pragma: no cover - unsupported systems must never run unlocked
            raise error_factory(
                WORKSPACE_AUTHORITY_INVALID,
                "workspace cross-process locking is unsupported on this platform",
            )
    finally:
        if handle is not None:
            handle.close()
        thread_lock.release()


class WorkspaceOperationFencingMixin:
    """Duck-typed orchestration around repository-owned short transactions."""

    _operation_lock_root: Path

    def _workspace_operation_error(self, reason_code: str, detail: str) -> BaseException:
        raise NotImplementedError

    @contextmanager
    def operation_lock(
        self,
        *,
        namespace: str,
        key: str,
        deadline_monotonic: float,
    ) -> Iterator[None]:
        normalized_namespace = str(namespace or "").strip()
        normalized_key = str(key or "").strip()
        if (
            not normalized_namespace
            or len(normalized_namespace) > 80
            or _LOCK_NAMESPACE.fullmatch(normalized_namespace) is None
        ):
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "operation lock namespace is invalid",
            )
        if not normalized_key or len(normalized_key) > 4096:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "operation lock key is invalid",
            )
        digest = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
        path = self._operation_lock_root / f"{normalized_namespace}-{digest}.lock"
        with _process_file_lock(
            path,
            deadline_monotonic=deadline_monotonic,
            error_factory=self._workspace_operation_error,
        ):
            yield

    def run_fenced(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        expected_status: str,
        max_operation_ms: int,
        operation: Callable[[WorkspaceOperationContext], T],
    ) -> T:
        """Run a side effect between two exact, short lease validations."""

        if not callable(operation):
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "operation must be callable",
            )
        reference, owner, token_digest, fence = self._lease_arguments(
            workspace_id,
            lease_owner,
            lease_token,
            lease_fencing_token,
        )
        status = str(expected_status or "").strip()
        if status not in _LIVE_STATUSES:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "fenced operation requires one live expected status",
            )
        if isinstance(max_operation_ms, bool):
            duration_ms = 0
        else:
            try:
                duration_ms = int(max_operation_ms)
            except (TypeError, ValueError):
                duration_ms = 0
        if duration_ms < 1:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "max_operation_ms must be a positive integer",
            )

        started_monotonic = time.monotonic()
        initial, ts_ms = self._read_exact_operation_authority(
            reference=reference,
            owner=owner,
            token_digest=token_digest,
            fence=fence,
            status=status,
        )
        initial_remaining_ms = int(initial.lease_expires_ts_ms or 0) - ts_ms
        completion_reserve_ms = min(
            100,
            max(1, initial_remaining_ms // 10),
        )
        safe_budget_ms = min(
            duration_ms,
            initial_remaining_ms - completion_reserve_ms,
        )
        if safe_budget_ms <= 0:
            raise self._workspace_operation_error(
                WORKSPACE_OPERATION_TIMEOUT,
                "workspace lease has no safe external-operation window",
            )
        deadline = started_monotonic + safe_budget_ms / 1000.0

        with self.operation_lock(
            namespace="workspace",
            key=reference,
            deadline_monotonic=deadline,
        ):
            before, ts_ms = self._read_exact_operation_authority(
                reference=reference,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                status=status,
            )
            remaining_lease_ms = (
                int(before.lease_expires_ts_ms or 0)
                - ts_ms
                - completion_reserve_ms
            )
            deadline = min(
                deadline,
                time.monotonic() + max(0, remaining_lease_ms) / 1000.0,
            )
            context = WorkspaceOperationContext(
                authority=before,
                deadline_monotonic=deadline,
                _error_factory=self._workspace_operation_error,
            )
            context.remaining_s()
            result = operation(context)
            context.remaining_s()
            self._read_exact_operation_authority(
                reference=reference,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                status=status,
            )
            context.remaining_s()
            return result

    def _read_exact_operation_authority(
        self,
        *,
        reference: str,
        owner: str,
        token_digest: str,
        fence: int,
        status: str,
    ) -> tuple[Any, int]:
        with self._transaction(write=False) as conn:
            ts_ms = self._now()
            row = self._require_row(conn, reference)
            self._assert_live_lease(
                row,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                ts_ms=ts_ms,
            )
            authority = self._authority(row)
            if authority.status != status:
                raise self._workspace_operation_error(
                    WORKSPACE_STALE_FENCE,
                    f"workspace operation requires {status} authority",
                )
            return authority, ts_ms


__all__ = [
    "WORKSPACE_AUTHORITY_INVALID",
    "WORKSPACE_OPERATION_TIMEOUT",
    "WORKSPACE_STALE_FENCE",
    "WorkspaceOperationContext",
    "WorkspaceOperationFencingMixin",
]
