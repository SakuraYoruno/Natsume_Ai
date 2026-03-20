"""Audit retention policy runner with replay-chain protection."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AUDIT_RETENTION_APPLIED,
    AUDIT_RETENTION_DRY_RUN,
    AUDIT_RETENTION_PROTECTED_CHAIN,
)
from host_brain.core.ids import now_ms
from host_brain.governor.tokens import TokenService
from host_brain.lsp.vault import ArtifactVault


S_AUDIT_RETENTION = "S_AUDIT_RETENTION"

MS_DAY = 24 * 60 * 60 * 1000

DEFAULT_KEEP_MS_BY_TABLE: dict[str, int] = {
    "error_log": 14 * MS_DAY,
    "qa_violation_log": 14 * MS_DAY,
    "service_health_log": 14 * MS_DAY,
    "airlock_log": 30 * MS_DAY,
    "channel_io_log": 30 * MS_DAY,
    "avatar_action_log": 30 * MS_DAY,
    "clawd_action_log": 30 * MS_DAY,
    "voice_event_log": 7 * MS_DAY,
    "retrieval_event_log": 7 * MS_DAY,
    "vision_event_log": 7 * MS_DAY,
    "bond_update_log": 30 * MS_DAY,
}

PROTECTED_TRIPTYCH_TABLES: tuple[str, ...] = (
    "intent_log",
    "governor_log",
    "skill_exec_log",
)


@dataclass(frozen=True)
class RetentionPolicy:
    policy_profile_id: str = "audit_default"
    keep_ms_by_table: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_KEEP_MS_BY_TABLE))
    protected_tables: tuple[str, ...] = PROTECTED_TRIPTYCH_TABLES
    vault_retention_ms: int = 7 * MS_DAY
    dry_run: bool = False


@dataclass(frozen=True)
class RetentionResult:
    success: bool
    stage: str
    reason_code: str
    policy_profile_id: str
    candidate_counts: dict[str, int]
    deleted_counts: dict[str, int]
    protected_counts: dict[str, int]
    vault_gc: dict[str, Any] | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "policy_profile_id": self.policy_profile_id,
            "candidate_counts": dict(self.candidate_counts),
            "deleted_counts": dict(self.deleted_counts),
            "protected_counts": dict(self.protected_counts),
            "vault_gc": self.vault_gc,
            "detail": self.detail,
        }


def run_retention_policy(
    *,
    ledger: SQLiteLedger,
    policy: RetentionPolicy | None = None,
    request_id: str | None = None,
    now_ts_ms: int | None = None,
    vault: ArtifactVault | None = None,
    token_service: TokenService | None = None,
    evidence_refs: list[str] | None = None,
) -> RetentionResult:
    active = policy or RetentionPolicy()
    now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
    # 使用 ledger._conn 但所有写操作必须通过 _write_lock 保护（见 _delete_older_than）
    conn = ledger._conn
    write_lock = ledger._write_lock

    candidate_counts: dict[str, int] = {}
    deleted_counts: dict[str, int] = {}
    protected_counts: dict[str, int] = {}

    for table in active.protected_tables:
        if not _has_ts_ms_column(conn, table):
            continue
        cutoff = now_value - int(active.keep_ms_by_table.get(table, 0))
        count = _count_older_than(conn, table=table, cutoff_ts_ms=cutoff)
        protected_counts[table] = count

    for table, keep_ms in active.keep_ms_by_table.items():
        if table in active.protected_tables:
            continue
        if not _has_ts_ms_column(conn, table):
            continue
        cutoff = now_value - int(keep_ms)
        count = _count_older_than(conn, table=table, cutoff_ts_ms=cutoff)
        candidate_counts[table] = count
        if active.dry_run or count <= 0:
            deleted_counts[table] = 0
            continue
        deleted = _delete_older_than(conn, write_lock=write_lock, table=table, cutoff_ts_ms=cutoff)
        deleted_counts[table] = deleted

    vault_gc = None
    if vault is not None:
        vault_gc = vault.garbage_collect(
            policy_profile_id=active.policy_profile_id,
            evidence_refs=list(evidence_refs or []),
            retention_ms=int(active.vault_retention_ms),
            request_id=request_id,
            token_service=token_service,
            now_ts_ms=now_value,
        )

    reason_code = AUDIT_RETENTION_DRY_RUN if active.dry_run else AUDIT_RETENTION_APPLIED
    _log_retention(
        ledger=ledger,
        request_id=request_id,
        reason_code=reason_code,
        detail_payload={
            "policy_profile_id": active.policy_profile_id,
            "candidate_counts": candidate_counts,
            "deleted_counts": deleted_counts,
            "protected_counts": protected_counts,
            "vault_gc": vault_gc,
        },
    )

    if any(value > 0 for value in protected_counts.values()):
        _log_retention(
            ledger=ledger,
            request_id=request_id,
            reason_code=AUDIT_RETENTION_PROTECTED_CHAIN,
            detail_payload={"protected_counts": protected_counts},
        )

    detail = f"retention {'dry-run' if active.dry_run else 'applied'}"
    return RetentionResult(
        success=True,
        stage=S_AUDIT_RETENTION,
        reason_code=reason_code,
        policy_profile_id=active.policy_profile_id,
        candidate_counts=candidate_counts,
        deleted_counts=deleted_counts,
        protected_counts=protected_counts,
        vault_gc=vault_gc,
        detail=detail,
    )


def _count_older_than(conn: Any, *, table: str, cutoff_ts_ms: int) -> int:
    row = conn.execute(
        f"SELECT COUNT(1) AS c FROM {table} WHERE ts_ms < ?",
        (int(cutoff_ts_ms),),
    ).fetchone()
    if row is None:
        return 0
    return int(row["c"])


def _delete_older_than(conn: Any, *, write_lock: Any, table: str, cutoff_ts_ms: int) -> int:
    # 通过 _write_lock 保护并发写入安全，与 SQLiteLedger._insert 保持一致
    with write_lock:
        with conn:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE ts_ms < ?",
                (int(cutoff_ts_ms),),
            )
    return int(cursor.rowcount or 0)


def _has_ts_ms_column(conn: Any, table: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    if not rows:
        return False
    names = {str(row["name"]) for row in rows}
    return "ts_ms" in names


def _log_retention(
    *,
    ledger: SQLiteLedger,
    request_id: str | None,
    reason_code: str,
    detail_payload: dict[str, Any],
) -> None:
    detail = json.dumps(detail_payload, ensure_ascii=False, sort_keys=True)
    ledger.log_error(
        request_id=request_id,
        stage=S_AUDIT_RETENTION,
        reason_code=reason_code,
        detail=detail,
    )

