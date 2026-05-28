"""Audit retention policy runner with replay-chain protection."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
import json
import os
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
    "model_control_log": 30 * MS_DAY,
    "memory_ops_log": 30 * MS_DAY,
    "service_health_log": 14 * MS_DAY,
    "migration_log": 90 * MS_DAY,
    "config_snapshot_log": 30 * MS_DAY,
    "airlock_log": 30 * MS_DAY,
    "channel_io_log": 30 * MS_DAY,
    "avatar_action_log": 30 * MS_DAY,
    "clawd_action_log": 30 * MS_DAY,
    "agent_loop_log": 7 * MS_DAY,
    "voice_event_log": 7 * MS_DAY,
    "retrieval_event_log": 7 * MS_DAY,
    "vision_event_log": 7 * MS_DAY,
    "sandbox_exec_log": 7 * MS_DAY,
    "scheduler_event_log": 30 * MS_DAY,
    "capability_forge_proposal_log": 90 * MS_DAY,
    "supervisor_decision_log": 14 * MS_DAY,
    "p8_subgraph_failure_log": 14 * MS_DAY,
    "p8_graph_run_log": 14 * MS_DAY,
    "graph_run_trace": 7 * MS_DAY,
    "llm_route_log": 7 * MS_DAY,
    "llm_route_config_log": 30 * MS_DAY,
    "llm_node_call_log": 7 * MS_DAY,
    "llm_pool_call_log": 7 * MS_DAY,
    "llm_token_usage_log": 7 * MS_DAY,
    "vault_gc_log": 30 * MS_DAY,
    "bond_update_log": 30 * MS_DAY,
}

DEFAULT_MAX_ROWS_BY_TABLE: dict[str, int] = {
    "error_log": 200_000,
    "service_health_log": 100_000,
    "llm_node_call_log": 200_000,
    "llm_pool_call_log": 200_000,
    "llm_token_usage_log": 200_000,
    "llm_route_log": 100_000,
    "graph_run_trace": 10_000,
    "agent_loop_log": 50_000,
    "retrieval_event_log": 100_000,
    "vision_event_log": 100_000,
    "voice_event_log": 100_000,
    "scheduler_event_log": 100_000,
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
    max_rows_by_table: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_MAX_ROWS_BY_TABLE))
    protected_tables: tuple[str, ...] = PROTECTED_TRIPTYCH_TABLES
    audit_cutoff_ts_ms: int | None = None
    protected_chain_keep_ms: int | None = None
    protected_chain_cutoff_ts_ms: int | None = None
    life_memory_keep_ms: int | None = 90 * MS_DAY
    life_memory_cutoff_ts_ms: int | None = None
    life_memory_min_salience: float = 0.40
    life_memory_episode_kinds: tuple[str, ...] = (
        "background_episode",
        "inner_episode",
        "perception_episode",
        "reflection_episode",
    )
    vault_retention_ms: int = 7 * MS_DAY
    dry_run: bool = False
    vacuum: bool = False


@dataclass(frozen=True)
class RetentionResult:
    success: bool
    stage: str
    reason_code: str
    policy_profile_id: str
    candidate_counts: dict[str, int]
    deleted_counts: dict[str, int]
    max_row_candidate_counts: dict[str, int]
    max_row_deleted_counts: dict[str, int]
    protected_counts: dict[str, int]
    protected_deleted_counts: dict[str, int]
    life_memory_candidate_count: int
    life_memory_deleted_count: int
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
            "max_row_candidate_counts": dict(self.max_row_candidate_counts),
            "max_row_deleted_counts": dict(self.max_row_deleted_counts),
            "protected_counts": dict(self.protected_counts),
            "protected_deleted_counts": dict(self.protected_deleted_counts),
            "life_memory_candidate_count": int(self.life_memory_candidate_count),
            "life_memory_deleted_count": int(self.life_memory_deleted_count),
            "vault_gc": self.vault_gc,
            "detail": self.detail,
        }


def build_retention_policy_from_env(
    environ: dict[str, str] | None = None,
    *,
    now_ts_ms: int | None = None,
) -> RetentionPolicy:
    """Build the scheduled retention policy from environment variables.

    Supported coarse controls:
      HOST_AUDIT_RETENTION_KEEP_DAYS
      HOST_AUDIT_RETENTION_CUTOFF_DATE=YYYY-MM-DD
      HOST_AUDIT_RETENTION_MAX_ROWS
      HOST_AUDIT_RETENTION_MAX_ROWS_ERROR_LOG
      HOST_AUDIT_RETENTION_PROTECTED_CHAIN_KEEP_DAYS
      HOST_AUDIT_RETENTION_PROTECTED_CHAIN_CUTOFF_DATE=YYYY-MM-DD
      HOST_MEMORY_RETENTION_KEEP_DAYS
      HOST_MEMORY_RETENTION_CUTOFF_DATE=YYYY-MM-DD
      HOST_MEMORY_RETENTION_MIN_SALIENCE
      HOST_MEMORY_RETENTION_INCLUDE_CHAT=true
      HOST_RETENTION_DRY_RUN=true
      HOST_RETENTION_VACUUM=true
    """

    env = environ if environ is not None else os.environ
    now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
    keep_ms_by_table = dict(DEFAULT_KEEP_MS_BY_TABLE)
    audit_keep_days = _parse_env_float(env, "HOST_AUDIT_RETENTION_KEEP_DAYS")
    if audit_keep_days is not None:
        keep_ms_by_table = {table: int(max(0.0, audit_keep_days) * MS_DAY) for table in keep_ms_by_table}
    max_rows_by_table = _max_rows_by_table_from_env(env)

    include_chat = _parse_env_bool(env, "HOST_MEMORY_RETENTION_INCLUDE_CHAT", default=False)
    life_kinds = [
        "background_episode",
        "inner_episode",
        "perception_episode",
        "reflection_episode",
    ]
    if include_chat:
        life_kinds.append("chat_episode")

    memory_keep_days = _parse_env_float(env, "HOST_MEMORY_RETENTION_KEEP_DAYS")
    life_memory_keep_ms = (
        int(max(0.0, memory_keep_days) * MS_DAY)
        if memory_keep_days is not None
        else 90 * MS_DAY
    )
    protected_days = _parse_env_float(env, "HOST_AUDIT_RETENTION_PROTECTED_CHAIN_KEEP_DAYS")

    return RetentionPolicy(
        policy_profile_id=str(env.get("HOST_RETENTION_POLICY_PROFILE_ID") or "audit_default").strip() or "audit_default",
        keep_ms_by_table=keep_ms_by_table,
        max_rows_by_table=max_rows_by_table,
        audit_cutoff_ts_ms=_parse_env_cutoff_ts(env, "HOST_AUDIT_RETENTION_CUTOFF_DATE", now_ts_ms=now_value),
        protected_chain_keep_ms=(
            int(max(0.0, protected_days) * MS_DAY)
            if protected_days is not None
            else None
        ),
        protected_chain_cutoff_ts_ms=_parse_env_cutoff_ts(
            env,
            "HOST_AUDIT_RETENTION_PROTECTED_CHAIN_CUTOFF_DATE",
            now_ts_ms=now_value,
        ),
        life_memory_keep_ms=life_memory_keep_ms,
        life_memory_cutoff_ts_ms=_parse_env_cutoff_ts(env, "HOST_MEMORY_RETENTION_CUTOFF_DATE", now_ts_ms=now_value),
        life_memory_min_salience=_parse_env_float(env, "HOST_MEMORY_RETENTION_MIN_SALIENCE", default=0.40),
        life_memory_episode_kinds=tuple(life_kinds),
        vault_retention_ms=int(
            max(0.0, _parse_env_float(env, "HOST_VAULT_RETENTION_DAYS", default=7.0)) * MS_DAY
        ),
        dry_run=_parse_env_bool(env, "HOST_RETENTION_DRY_RUN", default=False),
        vacuum=_parse_env_bool(env, "HOST_RETENTION_VACUUM", default=False),
    )


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
    max_row_candidate_counts: dict[str, int] = {}
    max_row_deleted_counts: dict[str, int] = {}
    protected_counts: dict[str, int] = {}
    protected_deleted_counts: dict[str, int] = {}

    for table in active.protected_tables:
        if not _has_ts_ms_column(conn, table):
            continue
        cutoff = _protected_chain_cutoff(active, table=table, now_ts_ms=now_value)
        if cutoff is None:
            cutoff = now_value - int(active.keep_ms_by_table.get(table, 0))
        count = _count_older_than(conn, table=table, cutoff_ts_ms=cutoff)
        protected_counts[table] = count
        if _protected_chain_cutoff(active, table=table, now_ts_ms=now_value) is None:
            protected_deleted_counts[table] = 0
            continue
        if active.dry_run or count <= 0:
            protected_deleted_counts[table] = 0
            continue
        protected_deleted_counts[table] = _delete_older_than(
            conn,
            write_lock=write_lock,
            table=table,
            cutoff_ts_ms=cutoff,
        )

    for table, keep_ms in active.keep_ms_by_table.items():
        if table in active.protected_tables:
            continue
        if not _has_ts_ms_column(conn, table):
            continue
        cutoff = int(active.audit_cutoff_ts_ms) if active.audit_cutoff_ts_ms is not None else now_value - int(keep_ms)
        count = _count_older_than(conn, table=table, cutoff_ts_ms=cutoff)
        candidate_counts[table] = count
        if active.dry_run or count <= 0:
            deleted_counts[table] = 0
            continue
        deleted = _delete_older_than(conn, write_lock=write_lock, table=table, cutoff_ts_ms=cutoff)
        deleted_counts[table] = deleted

    for table, max_rows in active.max_rows_by_table.items():
        if table in active.protected_tables:
            continue
        if not _has_column(conn, table, "id") or not _has_ts_ms_column(conn, table):
            continue
        candidate_count = _count_over_max_rows(conn, table=table, max_rows=max_rows)
        max_row_candidate_counts[table] = candidate_count
        if active.dry_run or candidate_count <= 0:
            max_row_deleted_counts[table] = 0
            continue
        max_row_deleted_counts[table] = _delete_over_max_rows(
            conn,
            write_lock=write_lock,
            table=table,
            max_rows=max_rows,
        )

    life_memory_candidate_count = _count_life_memory_delete_candidates(
        conn,
        policy=active,
        now_ts_ms=now_value,
    )
    if active.dry_run or life_memory_candidate_count <= 0:
        life_memory_deleted_count = 0
    else:
        life_memory_deleted_count = _delete_life_memory_candidates(
            conn,
            write_lock=write_lock,
            policy=active,
            now_ts_ms=now_value,
        )

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
            "max_row_candidate_counts": max_row_candidate_counts,
            "max_row_deleted_counts": max_row_deleted_counts,
            "protected_counts": protected_counts,
            "protected_deleted_counts": protected_deleted_counts,
            "life_memory_candidate_count": life_memory_candidate_count,
            "life_memory_deleted_count": life_memory_deleted_count,
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

    if active.vacuum and not active.dry_run and (
        any(value > 0 for value in deleted_counts.values())
        or any(value > 0 for value in max_row_deleted_counts.values())
        or any(value > 0 for value in protected_deleted_counts.values())
        or life_memory_deleted_count > 0
    ):
        _vacuum(conn, write_lock=write_lock)

    detail = f"retention {'dry-run' if active.dry_run else 'applied'}"
    return RetentionResult(
        success=True,
        stage=S_AUDIT_RETENTION,
        reason_code=reason_code,
        policy_profile_id=active.policy_profile_id,
        candidate_counts=candidate_counts,
        deleted_counts=deleted_counts,
        max_row_candidate_counts=max_row_candidate_counts,
        max_row_deleted_counts=max_row_deleted_counts,
        protected_counts=protected_counts,
        protected_deleted_counts=protected_deleted_counts,
        life_memory_candidate_count=life_memory_candidate_count,
        life_memory_deleted_count=life_memory_deleted_count,
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


def _count_over_max_rows(conn: Any, *, table: str, max_rows: int) -> int:
    cap = int(max_rows or 0)
    if cap <= 0:
        return 0
    row = conn.execute(f"SELECT COUNT(1) AS c FROM {table}").fetchone()
    if row is None:
        return 0
    return max(0, int(row["c"]) - cap)


def _delete_over_max_rows(conn: Any, *, write_lock: Any, table: str, max_rows: int) -> int:
    cap = int(max_rows or 0)
    if cap <= 0:
        return 0
    boundary = conn.execute(
        f"""
        SELECT ts_ms, id
        FROM {table}
        ORDER BY ts_ms DESC, id DESC
        LIMIT 1 OFFSET ?
        """,
        (cap,),
    ).fetchone()
    if boundary is None:
        return 0
    cutoff_ts = int(boundary["ts_ms"] or 0)
    cutoff_id = int(boundary["id"] or 0)
    with write_lock:
        with conn:
            cursor = conn.execute(
                f"""
                DELETE FROM {table}
                WHERE ts_ms < ?
                   OR (ts_ms = ? AND id <= ?)
                """,
                (cutoff_ts, cutoff_ts, cutoff_id),
            )
    return int(cursor.rowcount or 0)


def _protected_chain_cutoff(active: RetentionPolicy, *, table: str, now_ts_ms: int) -> int | None:
    _ = table
    if active.protected_chain_cutoff_ts_ms is not None:
        return int(active.protected_chain_cutoff_ts_ms)
    if active.protected_chain_keep_ms is not None:
        return int(now_ts_ms) - int(active.protected_chain_keep_ms)
    return None


def _life_memory_cutoff(active: RetentionPolicy, *, now_ts_ms: int) -> int | None:
    if active.life_memory_cutoff_ts_ms is not None:
        return int(active.life_memory_cutoff_ts_ms)
    if active.life_memory_keep_ms is None:
        return None
    return int(now_ts_ms) - int(active.life_memory_keep_ms)


def _life_memory_delete_where(active: RetentionPolicy, *, now_ts_ms: int) -> tuple[str | None, list[Any]]:
    cutoff = _life_memory_cutoff(active, now_ts_ms=now_ts_ms)
    kinds = tuple(str(item).strip() for item in active.life_memory_episode_kinds if str(item).strip())
    if cutoff is None or not kinds:
        return None, []
    placeholders = ", ".join("?" for _ in kinds)
    where = f"""
        last_touched_ts_ms < ?
        AND (retention_until_ts_ms IS NULL OR retention_until_ts_ms < ?)
        AND (
            COALESCE(retention_state, 'COLD') = 'GONE'
            OR COALESCE(salience, 0.0) < ?
        )
        AND episode_kind IN ({placeholders})
        AND episode_kind <> 'semantic_episode'
        AND NOT EXISTS (
            SELECT 1 FROM life_episode_link l
            WHERE l.source_episode_id = life_episode.episode_id
               OR l.target_episode_id = life_episode.episode_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM life_memory_correction c
            WHERE c.episode_id = life_episode.episode_id
               OR c.correction_episode_id = life_episode.episode_id
        )
    """
    return where, [int(cutoff), int(now_ts_ms), float(active.life_memory_min_salience), *kinds]


def _count_life_memory_delete_candidates(conn: Any, *, policy: RetentionPolicy, now_ts_ms: int) -> int:
    if not _has_column(conn, "life_episode", "last_touched_ts_ms"):
        return 0
    where, params = _life_memory_delete_where(policy, now_ts_ms=now_ts_ms)
    if not where:
        return 0
    row = conn.execute(
        f"SELECT COUNT(1) AS c FROM life_episode WHERE {where}",
        params,
    ).fetchone()
    if row is None:
        return 0
    return int(row["c"])


def _delete_life_memory_candidates(conn: Any, *, write_lock: Any, policy: RetentionPolicy, now_ts_ms: int) -> int:
    where, params = _life_memory_delete_where(policy, now_ts_ms=now_ts_ms)
    if not where:
        return 0
    with write_lock:
        with conn:
            cursor = conn.execute(
                f"""
                DELETE FROM life_episode
                WHERE episode_id IN (
                    SELECT episode_id FROM life_episode WHERE {where}
                )
                """,
                params,
            )
    return int(cursor.rowcount or 0)


def _vacuum(conn: Any, *, write_lock: Any) -> None:
    with write_lock:
        conn.execute("VACUUM")


def _has_ts_ms_column(conn: Any, table: str) -> bool:
    return _has_column(conn, table, "ts_ms")


def _has_column(conn: Any, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    if not rows:
        return False
    names = {str(row["name"]) for row in rows}
    return str(column) in names


def _parse_env_bool(env: dict[str, str], key: str, *, default: bool) -> bool:
    raw = str(env.get(key) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_env_float(env: dict[str, str], key: str, *, default: float | None = None) -> float | None:
    raw = str(env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _max_rows_by_table_from_env(env: dict[str, str]) -> dict[str, int]:
    max_rows = dict(DEFAULT_MAX_ROWS_BY_TABLE)
    global_cap = _parse_env_float(env, "HOST_AUDIT_RETENTION_MAX_ROWS")
    if global_cap is not None:
        cap = int(global_cap)
        if cap > 0:
            max_rows = {table: cap for table in max_rows}
    for table in list(DEFAULT_MAX_ROWS_BY_TABLE):
        env_key = f"HOST_AUDIT_RETENTION_MAX_ROWS_{table.upper()}"
        value = _parse_env_float(env, env_key)
        if value is None:
            continue
        cap = int(value)
        if cap <= 0:
            max_rows.pop(table, None)
        else:
            max_rows[table] = cap
    return max_rows


def _parse_env_cutoff_ts(env: dict[str, str], key: str, *, now_ts_ms: int) -> int | None:
    _ = now_ts_ms
    raw = str(env.get(key) or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    try:
        if "T" in raw:
            dt = _dt.datetime.fromisoformat(raw)
        else:
            date_value = _dt.date.fromisoformat(raw)
            dt = _dt.datetime.combine(date_value, _dt.time.min)
        if dt.tzinfo is None:
            return int(dt.timestamp() * 1000)
        return int(dt.astimezone().timestamp() * 1000)
    except ValueError:
        return None


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
