"""Exercise Task Runtime activation rollback failure on an isolated real DB.

The canary composes the production ProcessTaskRuntimeContainer and replaces
only its not-yet-started cancellation worker with a one-shot start fault.  The
bound coordinator then rejects its first two close attempts.  This forces the
real lifecycle code through activation rollback failure, retry fencing, failed
container close, successful close retry, and idempotent exact replay.

No user database, provider, device, or external effect is used.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import (
    TASK_RUNTIME_ACTIVATION_FAILED,
    TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED,
    TaskRuntimeContainerCloseError,
    TaskRuntimeContainerError,
    build_process_task_runtime_container,
)


CANARY_SCHEMA_VERSION = "natsume.task-runtime-rollback-failure-canary.v1"


class _StartFailingContinuationWorker:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        raise RuntimeError("CANARY_CONTINUATION_START_FAILURE")

    async def stop(self) -> None:
        self.stop_calls += 1
        await self.delegate.stop()

    async def join(self) -> None:
        self.join_calls += 1
        await self.delegate.join()


class _RollbackFailingCoordinator:
    def __init__(self) -> None:
        self.activate_calls = 0
        self.close_admission_calls = 0
        self.drain_calls = 0
        self.close_calls = 0
        self.fail_close = True

    async def activate(self) -> None:
        self.activate_calls += 1

    def close_admission(self) -> None:
        self.close_admission_calls += 1

    async def drain(self) -> None:
        self.drain_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("CANARY_COORDINATOR_CLOSE_FAILURE")


def _commit_sha(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("CANARY_COMMIT_SHA_INVALID")
    return value


def _migration_head(migration_result: Any) -> tuple[str, int]:
    summary = dict(getattr(migration_result, "summary", {}) or {})
    current_version = int(summary.get("current_version") or 0)
    latest_version = int(summary.get("latest_version") or 0)
    if current_version < 1 or current_version != latest_version:
        raise RuntimeError("CANARY_MIGRATION_HEAD_NOT_CURRENT")
    prefix = f"{latest_version:04d}_"
    candidates = sorted(
        str(record.migration_id)
        for record in tuple(getattr(migration_result, "records", ()) or ())
        if str(record.migration_id).startswith(prefix)
    )
    if not candidates:
        raise RuntimeError("CANARY_MIGRATION_HEAD_ASSET_MISSING")
    return candidates[-1] + ".sql", latest_version


def _db_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


async def _exercise(container: Any) -> dict[str, Any]:
    coordinator = _RollbackFailingCoordinator()
    worker = _StartFailingContinuationWorker(
        container.cancellation_continuation_worker
    )
    container.cancellation_continuation_worker = worker
    container.bind_runtime(coordinator)

    try:
        await container.activate_runtime()
    except TaskRuntimeContainerError as exc:
        activation_reason = exc.reason_code
    else:
        raise RuntimeError("CANARY_ACTIVATION_FAILURE_NOT_RAISED")
    if activation_reason != TASK_RUNTIME_ACTIVATION_FAILED:
        raise RuntimeError("CANARY_ACTIVATION_REASON_INVALID")
    if container.closed or container.activated or container.bound_runtime is not coordinator:
        raise RuntimeError("CANARY_ROLLBACK_RETRY_HANDLE_NOT_RETAINED")

    try:
        await container.activate_runtime()
    except TaskRuntimeContainerError as exc:
        retry_reason = exc.reason_code
    else:
        raise RuntimeError("CANARY_REACTIVATION_NOT_FENCED")
    if retry_reason != TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED:
        raise RuntimeError("CANARY_REACTIVATION_REASON_INVALID")
    if coordinator.activate_calls != 1 or worker.start_calls != 1:
        raise RuntimeError("CANARY_FAILED_ACTIVATION_REPLAYED")

    try:
        await container.aclose()
    except TaskRuntimeContainerCloseError as exc:
        first_close_failures = [
            {"component": item.component, "reason_code": item.reason_code}
            for item in exc.failures
        ]
    else:
        raise RuntimeError("CANARY_FIRST_CLOSE_FAILURE_NOT_RAISED")
    if container.closed:
        raise RuntimeError("CANARY_INFRASTRUCTURE_CLOSED_AFTER_FAILED_ROLLBACK")
    if not any(
        item["component"] == "runtime_coordinator_quiescence"
        for item in first_close_failures
    ):
        raise RuntimeError("CANARY_CLOSE_FAILURE_COMPONENT_INVALID")

    coordinator.fail_close = False
    await container.aclose()
    if not container.closed:
        raise RuntimeError("CANARY_CLOSE_RETRY_DID_NOT_COMPLETE")
    close_calls_before_replay = coordinator.close_calls
    await container.aclose()
    if coordinator.close_calls != close_calls_before_replay:
        raise RuntimeError("CANARY_TERMINAL_CLOSE_REPLAYED")

    return {
        "activation_failure": {
            "status": "PASS",
            "reason_code": activation_reason,
            "coordinator_activate_calls": coordinator.activate_calls,
            "continuation_start_calls": worker.start_calls,
        },
        "rollback_failure_fence": {
            "status": "PASS",
            "reactivation_reason_code": retry_reason,
            "bound_runtime_retained": True,
            "infrastructure_closed_after_failure": False,
            "first_close_failures": first_close_failures,
        },
        "close_retry": {
            "status": "PASS",
            "coordinator_close_calls": coordinator.close_calls,
            "close_admission_calls": coordinator.close_admission_calls,
            "drain_calls": coordinator.drain_calls,
            "continuation_stop_calls": worker.stop_calls,
            "continuation_join_calls": worker.join_calls,
            "container_closed": container.closed,
        },
        "terminal_replay": {
            "status": "PASS",
            "additional_coordinator_close_calls": 0,
        },
    }


def run_canary(*, repository_root: Path, run_id: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="natsume-task-rollback-canary-") as raw:
        root = Path(raw).resolve()
        db_path = root / "canonical.sqlite3"
        ledger = SQLiteLedger(str(db_path), check_same_thread=False)
        container = None
        try:
            container = build_process_task_runtime_container(
                ledger=ledger,
                canonical_db_path=db_path,
            )
            migration_head, migration_head_version = _migration_head(
                container.migration_result
            )
            lifecycle = asyncio.run(_exercise(container))
            connection = sqlite3.connect(db_path)
            try:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                graph_count = int(
                    connection.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone()[0]
                )
                outbox_count = int(
                    connection.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone()[0]
                )
            finally:
                connection.close()
            if integrity != "ok" or graph_count != 0 or outbox_count != 0:
                raise RuntimeError("CANARY_DURABLE_STATE_INVALID")
            digest = _db_digest(db_path)
            receipt = {
                "schema_version": CANARY_SCHEMA_VERSION,
                "run_id": str(run_id).strip(),
                "commit_sha": _commit_sha(repository_root),
                "database_kind": "ISOLATED_REAL_SQLITE",
                "migration_head": migration_head,
                "migration_head_version": migration_head_version,
                **lifecycle,
                "durable_state": {
                    "status": "PASS",
                    "integrity_check": integrity,
                    "task_graph_count": graph_count,
                    "task_outbox_count": outbox_count,
                    "database_digest": digest,
                },
                "fault_scope": "PROCESS_LOCAL_LIFECYCLE_BOUNDARY",
                "production_failpoint_added": False,
                "external_side_effects": 0,
                "private_or_user_data_used": False,
                "temporary_database_removed": True,
                "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
                "status": "PASS",
            }
        finally:
            if container is not None and not container.closed:
                bound = container.bound_runtime
                if isinstance(bound, _RollbackFailingCoordinator):
                    bound.fail_close = False
                asyncio.run(container.aclose())
            ledger.close()
            # Several SQLite-backed components own short-lived connection
            # objects.  Drop the composed graph before TemporaryDirectory
            # removes the database so Windows does not retain a file handle
            # until the next cyclic-GC pass.
            container = None
            ledger = None
            gc.collect()
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated Task Runtime rollback failure canary",
    )
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_canary(
        repository_root=args.repository_root.resolve(),
        run_id=args.run_id,
    )
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.receipt is not None:
        target = args.receipt.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
