"""SQLite-backed ledger with triptych schema, WAL mode, and replay-friendly indexes.

WAL (Write-Ahead Logging) mode is enabled by default for improved concurrent
read/write throughput. When WAL activation fails (e.g. read-only filesystem),
the ledger degrades to DELETE journal mode and logs AUDIT_WAL_FAILED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from host_brain.core.errors import (
    AUDIT_TABLE_NAME_INVALID,
    AUDIT_WAL_CHECKPOINT_FAILED,
    AUDIT_WAL_FAILED,
    TRACE_MISSING_GOVERNOR,
    TRACE_MISSING_INTENT,
)

# ---------------------------------------------------------------------------
# 表名白名单（硬编码允许表名集合，用于 _fetch_all 安全校验）
# ---------------------------------------------------------------------------
_ALLOWED_TABLES: frozenset[str] = frozenset({
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "error_log",
    "qa_violation_log",
    "canon_proposal_log",
    "canon_change_log",
    "world_canon_doc",
    "memory_ops_log",
    "vault_manifest_index",
    "vault_gc_log",
    "airlock_log",
    "channel_io_log",
    "avatar_action_log",
    "clawd_action_log",
    "service_health_log",
    "migration_log",
    "config_snapshot_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "bond_update_log",
    "sandbox_exec_log",
    "replay_guard",
    "session_seq_guard",
})
from host_brain.core.ids import now_ms

logger = logging.getLogger(__name__)


INTENT_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "session_id",
    "seq",
    "ts_ms",
    "nonce",
    "parse_stage",
    "raw_text",
    "normalized_json",
    "proposer_node_id",
    "model_snapshot",
    "prompt_profile_id",
    "tool_visibility_hash",
    "proposed_intent_draft_json",
    "lint_result",
)

GOVERNOR_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "decision",
    "reason_code",
    "rule_trace",
    "tokens",
    "snapshot_refs",
    "stage",
    "ts_ms",
)

SKILL_EXEC_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "event_id",
    "skill_name",
    "approval_token_ref",
    "confirm_token_ref",
    "undo_token_ref",
    "result_status",
    "result_code",
    "result_digest",
    "evidence_ref",
    "manifest_ref",
    "ts_ms",
)

CANON_PROPOSAL_COLUMNS = (
    "proposal_id",
    "canon_kind",
    "target_id",
    "base_version",
    "proposed_version",
    "proposed_hash",
    "proposer_node_id",
    "summary",
    "status",
    "ts_ms",
)

CANON_CHANGE_COLUMNS = (
    "change_id",
    "proposal_id",
    "canon_kind",
    "target_id",
    "before_version",
    "after_version",
    "before_hash",
    "after_hash",
    "source_path",
    "ts_ms",
)

WORLD_CANON_DOC_COLUMNS = (
    "doc_id",
    "version",
    "hash",
    "status",
    "source_path",
    "updated_ts",
)

MEMORY_OP_COLUMNS = (
    "request_id",
    "intent_id",
    "op_kind",
    "memory_id",
    "target_memory_id",
    "source_memory_ids",
    "evidence_refs",
    "status",
    "reason_code",
    "detail",
    "decay_profile_id",
    "visibility",
    "ts_ms",
)

VAULT_MANIFEST_COLUMNS = (
    "request_id",
    "manifest_id",
    "intent_id",
    "integrity_digest",
    "manifest_path",
    "items_count",
    "evidence_refs",
    "policy_profile_id",
    "undo_ref",
    "reversible",
    "skill_name",
    "session_id",
    "seq",
    "status",
    "ts_ms",
)

VAULT_GC_COLUMNS = (
    "request_id",
    "policy_profile_id",
    "deleted_manifest_ids",
    "deleted_artifact_ids",
    "blocked_manifest_ids",
    "evidence_refs",
    "status",
    "ts_ms",
)

AIRLOCK_COLUMNS = (
    "request_id",
    "direction",
    "reason_code",
    "removed_fields",
    "redactions",
    "payload_digest",
    "quarantine_digest",
    "ingress_classification",
    "blocked",
    "detail",
    "ts_ms",
)

CHANNEL_IO_COLUMNS = (
    "request_id",
    "channel",
    "direction",
    "thread_key",
    "message_id",
    "user_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "payload_digest",
    "ts_ms",
)

AVATAR_ACTION_COLUMNS = (
    "request_id",
    "trigger",
    "emotion_tag",
    "expression_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

CLAWD_ACTION_COLUMNS = (
    "request_id",
    "intent_id",
    "target_desc",
    "action_name",
    "confidence",
    "before_artifact_ref",
    "after_artifact_ref",
    "manifest_ref",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

SERVICE_HEALTH_COLUMNS = (
    "request_id",
    "service_id",
    "owner",
    "required",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

MIGRATION_COLUMNS = (
    "request_id",
    "scope",
    "migration_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "latency_ms",
    "ts_ms",
)

CONFIG_SNAPSHOT_COLUMNS = (
    "request_id",
    "component",
    "env",
    "version",
    "digest",
    "override_sources",
    "redacted_keys",
    "snapshot_json",
    "diff_json",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

VOICE_EVENT_COLUMNS = (
    "request_id",
    "session_id",
    "event_type",
    "direction",
    "seq",
    "status",
    "reason_code",
    "stage",
    "payload",
    "detail",
    "ts_ms",
)

RETRIEVAL_EVENT_COLUMNS = (
    "request_id",
    "intent_id",
    "op_kind",
    "status",
    "reason_code",
    "stage",
    "payload",
    "detail",
    "ts_ms",
)

VISION_EVENT_COLUMNS = (
    "request_id",
    "intent_id",
    "event_type",
    "status",
    "reason_code",
    "stage",
    "source_ref",
    "payload",
    "detail",
    "ts_ms",
)

BOND_UPDATE_COLUMNS = (
    "request_id",
    "intent_id",
    "session_id",
    "seq",
    "event_id",
    "user_id",
    "topic_id",
    "capability_hint",
    "update_kind",
    "status",
    "reason_code",
    "stage",
    "evidence_refs",
    "payload",
    "detail",
    "ts_ms",
)


SANDBOX_EXEC_COLUMNS = (
    "request_id",
    "intent_id",
    "sandbox_id",
    "status",
    "exit_code",
    "reason_code",
    "evidence_ref",
    "code_digest",
    "stdout_preview",
    "stderr_preview",
    "duration_ms",
    "resource_usage",
    "truncated_stdout",
    "truncated_stderr",
    "ts_ms",
)

MODEL_CONTROL_COLUMNS = (
    "request_id",
    "node_id",
    "model_node_id",
    "model_profile_id",
    "provider",
    "model_id",
    "expected_param_hash",
    "actual_param_hash",
    "drift_detected",
    "approval_token_ref",
    "evidence_ref",
    "reason_code",
    "status",
    "stage",
    "ts_ms",
)

JSON_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "intent_log": {"normalized_json", "proposed_intent_draft_json", "lint_result"},
    "governor_log": {"rule_trace", "tokens", "snapshot_refs"},
    "skill_exec_log": set(),
    "error_log": set(),
    "qa_violation_log": set(),
    "memory_ops_log": {"source_memory_ids", "evidence_refs"},
    "vault_manifest_index": {"evidence_refs"},
    "vault_gc_log": {"deleted_manifest_ids", "deleted_artifact_ids", "blocked_manifest_ids", "evidence_refs"},
    "airlock_log": {"removed_fields", "redactions"},
    "channel_io_log": set(),
    "avatar_action_log": set(),
    "clawd_action_log": set(),
    "service_health_log": set(),
    "migration_log": set(),
    "config_snapshot_log": {"override_sources", "redacted_keys", "snapshot_json", "diff_json"},
    "voice_event_log": {"payload"},
    "retrieval_event_log": {"payload"},
    "vision_event_log": {"payload"},
    "bond_update_log": {"evidence_refs", "payload"},
    "model_control_log": set(),
    "sandbox_exec_log": {"resource_usage"},
}


# ---------------------------------------------------------------------------
# WAL Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalConfig:
    """WAL-related PRAGMA tunables."""
    journal_mode: str = "WAL"
    synchronous: str = "NORMAL"
    wal_autocheckpoint: int = 1000
    busy_timeout_ms: int = 5000
    cache_size_kb: int = 2000

    def as_dict(self) -> dict[str, Any]:
        return {
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "wal_autocheckpoint": self.wal_autocheckpoint,
            "busy_timeout_ms": self.busy_timeout_ms,
            "cache_size_kb": self.cache_size_kb,
        }


DEFAULT_WAL_CONFIG = WalConfig()

# 写入重试策略
WRITE_RETRY_MAX = 3
WRITE_RETRY_DELAY_S = 0.05


class SQLiteLedger:
    """Audit ledger persisted by sqlite3 with WAL mode and concurrent write protection."""

    def __init__(
        self,
        db_path: str | Path = "host_brain/data/audit.sqlite3",
        *,
        check_same_thread: bool = True,
        wal_config: WalConfig | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self._wal_config = wal_config or DEFAULT_WAL_CONFIG
        self._write_lock = threading.Lock()
        self._wal_status: str = "UNKNOWN"
        self._wal_degradation_reason: str | None = None

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        # 表结构缓存：避免每次 _table_has_column 调用 PRAGMA table_info
        self._column_cache: dict[str, set[str]] = {}
        self._apply_wal_config()
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._ensure_schema()

    @property
    def intent_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("intent_log")

    @property
    def governor_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("governor_log")

    @property
    def skill_exec_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("skill_exec_log")

    @property
    def model_control_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("model_control_log")

    @property
    def error_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("error_log")

    @property
    def qa_violation_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("qa_violation_log")

    @property
    def canon_proposal_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("canon_proposal_log")

    @property
    def canon_change_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("canon_change_log")

    @property
    def world_canon_doc(self) -> list[dict[str, Any]]:
        return self._fetch_all("world_canon_doc")

    @property
    def memory_ops_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("memory_ops_log")

    @property
    def vault_manifest_index(self) -> list[dict[str, Any]]:
        return self._fetch_all("vault_manifest_index")

    @property
    def vault_gc_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("vault_gc_log")

    @property
    def airlock_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("airlock_log")

    @property
    def channel_io_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("channel_io_log")

    @property
    def avatar_action_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("avatar_action_log")

    @property
    def clawd_action_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("clawd_action_log")

    @property
    def service_health_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("service_health_log")

    @property
    def migration_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("migration_log")

    @property
    def config_snapshot_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("config_snapshot_log")

    @property
    def voice_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("voice_event_log")

    @property
    def retrieval_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("retrieval_event_log")

    @property
    def vision_event_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("vision_event_log")

    @property
    def bond_update_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("bond_update_log")

    @property
    def sandbox_exec_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("sandbox_exec_log")

    # -------------------------------------------------------------------
    # WAL lifecycle
    # -------------------------------------------------------------------

    def _apply_wal_config(self) -> None:
        """Apply WAL PRAGMA configuration. Degrade gracefully on failure."""
        cfg = self._wal_config
        try:
            result = self._conn.execute("PRAGMA journal_mode = WAL;").fetchone()
            actual_mode = str(result[0]).upper() if result else "UNKNOWN"
            if actual_mode != "WAL":
                self._wal_status = "DEGRADED"
                self._wal_degradation_reason = f"journal_mode returned {actual_mode}, expected WAL"
                logger.warning("WAL activation failed: %s", self._wal_degradation_reason)
                return
            self._conn.execute(f"PRAGMA synchronous = {cfg.synchronous};")
            self._conn.execute(f"PRAGMA wal_autocheckpoint = {cfg.wal_autocheckpoint};")
            self._conn.execute(f"PRAGMA busy_timeout = {cfg.busy_timeout_ms};")
            self._conn.execute(f"PRAGMA cache_size = -{cfg.cache_size_kb};")
            self._wal_status = "ACTIVE"
            self._wal_degradation_reason = None
        except Exception as exc:
            self._wal_status = "DEGRADED"
            self._wal_degradation_reason = str(exc)
            logger.warning("WAL configuration failed, degrading to DELETE mode: %s", exc)
            try:
                self._conn.execute("PRAGMA journal_mode = DELETE;")
            except Exception:
                pass

    def wal_health(self) -> dict[str, Any]:
        """Return WAL health snapshot for observability."""
        wal_size: int | None = None
        try:
            row = self._conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchone()
            if row:
                wal_size = int(row[1]) if row[1] is not None else None
        except Exception:
            pass

        journal_mode = "UNKNOWN"
        try:
            row = self._conn.execute("PRAGMA journal_mode;").fetchone()
            if row:
                journal_mode = str(row[0]).upper()
        except Exception:
            pass

        synchronous = "UNKNOWN"
        try:
            row = self._conn.execute("PRAGMA synchronous;").fetchone()
            sync_map = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
            if row:
                synchronous = sync_map.get(int(row[0]), str(row[0]))
        except Exception:
            pass

        return {
            "wal_status": self._wal_status,
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "degradation_reason": self._wal_degradation_reason,
            "wal_config": self._wal_config.as_dict(),
            "wal_size_pages": wal_size,
            "db_path": self.db_path,
        }

    def checkpoint(self, mode: str = "PASSIVE") -> dict[str, Any]:
        """Trigger a WAL checkpoint. Returns checkpoint result."""
        allowed_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        mode = str(mode).upper().strip()
        if mode not in allowed_modes:
            mode = "PASSIVE"
        try:
            row = self._conn.execute(f"PRAGMA wal_checkpoint({mode});").fetchone()
            blocked = int(row[0]) if row else -1
            total = int(row[1]) if row and row[1] is not None else 0
            checkpointed = int(row[2]) if row and row[2] is not None else 0
            return {
                "status": "OK",
                "mode": mode,
                "blocked": blocked,
                "total_frames": total,
                "checkpointed_frames": checkpointed,
                "reason_code": None,
            }
        except Exception as exc:
            return {
                "status": "FAILED",
                "mode": mode,
                "blocked": -1,
                "total_frames": 0,
                "checkpointed_frames": 0,
                "reason_code": AUDIT_WAL_CHECKPOINT_FAILED,
                "detail": str(exc),
            }

    def close(self) -> None:
        try:
            self.checkpoint("PASSIVE")
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def log_intent(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in INTENT_COLUMNS}
        self._insert("intent_log", payload)

    def log_governor(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in GOVERNOR_COLUMNS}
        self._insert("governor_log", payload)

    def log_skill_exec(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SKILL_EXEC_COLUMNS}
        if not payload.get("event_id"):
            payload["event_id"] = f"evt_{uuid.uuid4().hex}"
        self._insert("skill_exec_log", payload)

    def log_model_control(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MODEL_CONTROL_COLUMNS}
        self._insert("model_control_log", payload)

    def log_error(self, *, request_id: str | None, stage: str, reason_code: str, detail: str) -> None:
        self._insert(
            "error_log",
            {
                "request_id": request_id,
                "stage": stage,
                "reason_code": reason_code,
                "detail": detail,
                "ts_ms": now_ms(),
            },
        )

    def log_qa_violation(self, *, request_id: str | None, code: str, detail: str, field: str | None = None) -> None:
        self._insert(
            "qa_violation_log",
            {
                "request_id": request_id,
                "code": code,
                "detail": detail,
                "field": field,
                "ts_ms": now_ms(),
            },
        )

    def log_canon_proposal(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CANON_PROPOSAL_COLUMNS}
        self._insert("canon_proposal_log", payload)

    def log_canon_change(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CANON_CHANGE_COLUMNS}
        self._insert("canon_change_log", payload)

    def upsert_world_canon_doc(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in WORLD_CANON_DOC_COLUMNS}
        if not payload.get("doc_id"):
            raise ValueError("world_canon_doc requires doc_id")

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO world_canon_doc(doc_id, version, hash, status, source_path, updated_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    version = excluded.version,
                    hash = excluded.hash,
                    status = excluded.status,
                    source_path = excluded.source_path,
                    updated_ts = excluded.updated_ts
                """,
                (
                    payload["doc_id"],
                    payload["version"],
                    payload["hash"],
                    payload["status"],
                    payload["source_path"],
                    payload["updated_ts"],
                ),
            )

    def get_world_canon_doc(self, doc_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM world_canon_doc WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("world_canon_doc", row)

    def log_memory_op(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MEMORY_OP_COLUMNS}
        self._insert("memory_ops_log", payload)

    def log_vault_manifest(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VAULT_MANIFEST_COLUMNS}
        self._insert("vault_manifest_index", payload)

    def log_vault_gc(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VAULT_GC_COLUMNS}
        self._insert("vault_gc_log", payload)

    def log_airlock(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in AIRLOCK_COLUMNS}
        self._insert("airlock_log", payload)

    def log_channel_io(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CHANNEL_IO_COLUMNS}
        self._insert("channel_io_log", payload)

    def log_avatar_action(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in AVATAR_ACTION_COLUMNS}
        self._insert("avatar_action_log", payload)

    def log_clawd_action(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CLAWD_ACTION_COLUMNS}
        self._insert("clawd_action_log", payload)

    def log_service_health(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SERVICE_HEALTH_COLUMNS}
        self._insert("service_health_log", payload)

    def log_migration(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MIGRATION_COLUMNS}
        self._insert("migration_log", payload)

    def log_config_snapshot(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CONFIG_SNAPSHOT_COLUMNS}
        self._insert("config_snapshot_log", payload)

    def log_voice_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VOICE_EVENT_COLUMNS}
        self._insert("voice_event_log", payload)

    def log_retrieval_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in RETRIEVAL_EVENT_COLUMNS}
        self._insert("retrieval_event_log", payload)

    def log_vision_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in VISION_EVENT_COLUMNS}
        self._insert("vision_event_log", payload)

    def log_bond_update(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in BOND_UPDATE_COLUMNS}
        self._insert("bond_update_log", payload)

    def log_sandbox_exec(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SANDBOX_EXEC_COLUMNS}
        self._insert("sandbox_exec_log", payload)

    def get_vault_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM vault_manifest_index
            WHERE manifest_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (manifest_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("vault_manifest_index", row)

    def has_replay(self, *, intent_id: str, session_id: str, nonce: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM replay_guard
            WHERE intent_id = ? OR (session_id = ? AND nonce = ?)
            LIMIT 1
            """,
            (intent_id, session_id, nonce),
        ).fetchone()
        return row is not None

    def mark_intent_seen(self, *, intent_id: str, session_id: str, nonce: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO replay_guard(intent_id, session_id, nonce, ts_ms)
                VALUES (?, ?, ?, ?)
                """,
                (intent_id, session_id, nonce, now_ms()),
            )

    def is_seq_invalid(self, *, session_id: str, seq: int) -> bool:
        row = self._conn.execute(
            "SELECT seq FROM session_seq_guard WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        return int(seq) <= int(row["seq"])

    def update_seq(self, *, session_id: str, seq: int) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO session_seq_guard(session_id, seq, updated_ts_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    seq = excluded.seq,
                    updated_ts_ms = excluded.updated_ts_ms
                """,
                (session_id, seq, now_ms()),
            )

    def has_skill_exec_for_intent(self, *, intent_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM skill_exec_log
            WHERE intent_id = ?
              AND result_status IN ('SUCCESS', 'IDEMPOTENT_REPLAY')
            LIMIT 1
            """,
            (intent_id,),
        ).fetchone()
        return row is not None

    def has_channel_message(self, *, channel: str, direction: str, message_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM channel_io_log
            WHERE channel = ?
              AND direction = ?
              AND message_id = ?
            LIMIT 1
            """,
            (channel, direction, message_id),
        ).fetchone()
        return row is not None

    def trace_request(self, request_id: str) -> dict[str, Any]:
        intents = self._fetch_by_request("intent_log", request_id)
        governors = self._fetch_by_request("governor_log", request_id)
        skills = self._fetch_by_request("skill_exec_log", request_id)
        model_controls = self._fetch_by_request("model_control_log", request_id)

        if not intents:
            return {
                "request_id": request_id,
                "status": "INCOMPLETE",
                "stage": "S_INTENT",
                "reason_code": TRACE_MISSING_INTENT,
                "intent_log": [],
                "governor_log": governors,
                "skill_exec_log": skills,
                "model_control_log": model_controls,
            }

        if not governors:
            return {
                "request_id": request_id,
                "status": "INCOMPLETE",
                "stage": "S_GOVERNOR",
                "reason_code": TRACE_MISSING_GOVERNOR,
                "intent_log": intents,
                "governor_log": [],
                "skill_exec_log": skills,
                "model_control_log": model_controls,
            }

        latest_governor = sorted(governors, key=lambda r: int(r.get("ts_ms") or 0))[-1]
        status = "COMPLETE"
        if latest_governor.get("decision") == "REJECT":
            status = "COMPLETE_REJECTED"

        return {
            "request_id": request_id,
            "status": status,
            "stage": latest_governor.get("stage"),
            "reason_code": latest_governor.get("reason_code"),
            "intent_log": intents,
            "governor_log": governors,
            "skill_exec_log": skills,
            "model_control_log": model_controls,
        }

    def get_event(self, evidence_ref: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM skill_exec_log WHERE evidence_ref = ? ORDER BY id DESC LIMIT 1",
            (evidence_ref,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("skill_exec_log", row)

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intent_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    intent_id TEXT,
                    session_id TEXT,
                    seq INTEGER,
                    ts_ms INTEGER,
                    nonce TEXT,
                    parse_stage TEXT,
                    raw_text TEXT,
                    normalized_json TEXT,
                    proposer_node_id TEXT,
                    model_snapshot TEXT,
                    prompt_profile_id TEXT,
                    tool_visibility_hash TEXT,
                    proposed_intent_draft_json TEXT,
                    lint_result TEXT
                );

                CREATE TABLE IF NOT EXISTS governor_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    intent_id TEXT,
                    decision TEXT,
                    reason_code TEXT,
                    rule_trace TEXT,
                    tokens TEXT,
                    snapshot_refs TEXT,
                    stage TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS skill_exec_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    intent_id TEXT,
                    event_id TEXT,
                    skill_name TEXT,
                    approval_token_ref TEXT,
                    confirm_token_ref TEXT,
                    undo_token_ref TEXT,
                    result_status TEXT,
                    result_code TEXT,
                    result_digest TEXT,
                    evidence_ref TEXT,
                    manifest_ref TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS model_control_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    node_id TEXT,
                    model_node_id TEXT,
                    model_profile_id TEXT,
                    provider TEXT,
                    model_id TEXT,
                    expected_param_hash TEXT,
                    actual_param_hash TEXT,
                    drift_detected INTEGER,
                    approval_token_ref TEXT,
                    evidence_ref TEXT,
                    reason_code TEXT,
                    status TEXT,
                    stage TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    stage TEXT,
                    reason_code TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS qa_violation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    code TEXT,
                    detail TEXT,
                    field TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS canon_proposal_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT,
                    canon_kind TEXT,
                    target_id TEXT,
                    base_version INTEGER,
                    proposed_version INTEGER,
                    proposed_hash TEXT,
                    proposer_node_id TEXT,
                    summary TEXT,
                    status TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS canon_change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    change_id TEXT,
                    proposal_id TEXT,
                    canon_kind TEXT,
                    target_id TEXT,
                    before_version INTEGER,
                    after_version INTEGER,
                    before_hash TEXT,
                    after_hash TEXT,
                    source_path TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS world_canon_doc (
                    doc_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    updated_ts INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_ops_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    op_kind TEXT,
                    memory_id TEXT,
                    target_memory_id TEXT,
                    source_memory_ids TEXT,
                    evidence_refs TEXT,
                    status TEXT,
                    reason_code TEXT,
                    detail TEXT,
                    decay_profile_id TEXT,
                    visibility TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS vault_manifest_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    manifest_id TEXT,
                    intent_id TEXT,
                    integrity_digest TEXT,
                    manifest_path TEXT,
                    items_count INTEGER,
                    evidence_refs TEXT,
                    policy_profile_id TEXT,
                    undo_ref TEXT,
                    reversible INTEGER,
                    skill_name TEXT,
                    session_id TEXT,
                    seq INTEGER,
                    status TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS vault_gc_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    policy_profile_id TEXT,
                    deleted_manifest_ids TEXT,
                    deleted_artifact_ids TEXT,
                    blocked_manifest_ids TEXT,
                    evidence_refs TEXT,
                    status TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS airlock_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    direction TEXT,
                    reason_code TEXT,
                    removed_fields TEXT,
                    redactions TEXT,
                    payload_digest TEXT,
                    quarantine_digest TEXT,
                    ingress_classification TEXT,
                    blocked INTEGER,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS channel_io_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    channel TEXT,
                    direction TEXT,
                    thread_key TEXT,
                    message_id TEXT,
                    user_id TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    payload_digest TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS avatar_action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    trigger TEXT,
                    emotion_tag TEXT,
                    expression_id TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS clawd_action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    target_desc TEXT,
                    action_name TEXT,
                    confidence REAL,
                    before_artifact_ref TEXT,
                    after_artifact_ref TEXT,
                    manifest_ref TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS service_health_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    service_id TEXT,
                    owner TEXT,
                    required INTEGER,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS migration_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    scope TEXT,
                    migration_id TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    latency_ms INTEGER,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS config_snapshot_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    component TEXT,
                    env TEXT,
                    version TEXT,
                    digest TEXT,
                    override_sources TEXT,
                    redacted_keys TEXT,
                    snapshot_json TEXT,
                    diff_json TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS voice_event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    session_id TEXT,
                    event_type TEXT,
                    direction TEXT,
                    seq INTEGER,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    payload TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS retrieval_event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    op_kind TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    payload TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS vision_event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    event_type TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    source_ref TEXT,
                    payload TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS bond_update_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    session_id TEXT,
                    seq INTEGER,
                    event_id TEXT,
                    user_id TEXT,
                    topic_id TEXT,
                    capability_hint TEXT,
                    update_kind TEXT,
                    status TEXT,
                    reason_code TEXT,
                    stage TEXT,
                    evidence_refs TEXT,
                    payload TEXT,
                    detail TEXT,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS sandbox_exec_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    intent_id TEXT,
                    sandbox_id TEXT,
                    status TEXT,
                    exit_code INTEGER,
                    reason_code TEXT,
                    evidence_ref TEXT,
                    code_digest TEXT,
                    stdout_preview TEXT,
                    stderr_preview TEXT,
                    duration_ms INTEGER,
                    resource_usage TEXT,
                    truncated_stdout INTEGER,
                    truncated_stderr INTEGER,
                    ts_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS replay_guard (
                    intent_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    UNIQUE(session_id, nonce)
                );

                CREATE TABLE IF NOT EXISTS session_seq_guard (
                    session_id TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL,
                    updated_ts_ms INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intent_request ON intent_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_intent_intent_id ON intent_log(intent_id);
                CREATE INDEX IF NOT EXISTS idx_governor_request ON governor_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_governor_intent_id ON governor_log(intent_id);
                CREATE INDEX IF NOT EXISTS idx_skill_request ON skill_exec_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_skill_intent_id ON skill_exec_log(intent_id);
                CREATE INDEX IF NOT EXISTS idx_skill_evidence_ref ON skill_exec_log(evidence_ref);
                CREATE INDEX IF NOT EXISTS idx_model_control_request ON model_control_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_model_control_profile ON model_control_log(model_profile_id);
                CREATE INDEX IF NOT EXISTS idx_canon_proposal_target ON canon_proposal_log(canon_kind, target_id);
                CREATE INDEX IF NOT EXISTS idx_canon_change_target ON canon_change_log(canon_kind, target_id);
                CREATE INDEX IF NOT EXISTS idx_memory_ops_memory_id ON memory_ops_log(memory_id);
                CREATE INDEX IF NOT EXISTS idx_memory_ops_request ON memory_ops_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_vault_manifest_id ON vault_manifest_index(manifest_id);
                CREATE INDEX IF NOT EXISTS idx_vault_manifest_intent ON vault_manifest_index(intent_id);
                CREATE INDEX IF NOT EXISTS idx_vault_gc_ts ON vault_gc_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_airlock_request ON airlock_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_airlock_direction ON airlock_log(direction);
                CREATE INDEX IF NOT EXISTS idx_airlock_ts ON airlock_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_channel_io_request ON channel_io_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_channel_io_thread ON channel_io_log(thread_key);
                CREATE INDEX IF NOT EXISTS idx_channel_io_message ON channel_io_log(channel, direction, message_id);
                CREATE INDEX IF NOT EXISTS idx_channel_io_ts ON channel_io_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_avatar_action_request ON avatar_action_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_avatar_action_emotion ON avatar_action_log(emotion_tag);
                CREATE INDEX IF NOT EXISTS idx_avatar_action_ts ON avatar_action_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_clawd_action_request ON clawd_action_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_clawd_action_intent ON clawd_action_log(intent_id);
                CREATE INDEX IF NOT EXISTS idx_clawd_action_ts ON clawd_action_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_service_health_request ON service_health_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_service_health_service_id ON service_health_log(service_id);
                CREATE INDEX IF NOT EXISTS idx_service_health_ts ON service_health_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_migration_request ON migration_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_migration_scope ON migration_log(scope);
                CREATE INDEX IF NOT EXISTS idx_migration_id ON migration_log(migration_id);
                CREATE INDEX IF NOT EXISTS idx_migration_ts ON migration_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_config_snapshot_request ON config_snapshot_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_config_snapshot_component ON config_snapshot_log(component, env);
                CREATE INDEX IF NOT EXISTS idx_config_snapshot_digest ON config_snapshot_log(digest);
                CREATE INDEX IF NOT EXISTS idx_config_snapshot_ts ON config_snapshot_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_voice_event_request ON voice_event_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_voice_event_session ON voice_event_log(session_id);
                CREATE INDEX IF NOT EXISTS idx_voice_event_type ON voice_event_log(event_type);
                CREATE INDEX IF NOT EXISTS idx_voice_event_ts ON voice_event_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_retrieval_event_request ON retrieval_event_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_retrieval_event_op_kind ON retrieval_event_log(op_kind);
                CREATE INDEX IF NOT EXISTS idx_retrieval_event_ts ON retrieval_event_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_vision_event_request ON vision_event_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_vision_event_type ON vision_event_log(event_type);
                CREATE INDEX IF NOT EXISTS idx_vision_event_source_ref ON vision_event_log(source_ref);
                CREATE INDEX IF NOT EXISTS idx_vision_event_ts ON vision_event_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_bond_update_request ON bond_update_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_bond_update_intent ON bond_update_log(intent_id);
                CREATE INDEX IF NOT EXISTS idx_bond_update_event ON bond_update_log(event_id);
                CREATE INDEX IF NOT EXISTS idx_bond_update_user ON bond_update_log(user_id);
                CREATE INDEX IF NOT EXISTS idx_bond_update_ts ON bond_update_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_sandbox_exec_request ON sandbox_exec_log(request_id);
                CREATE INDEX IF NOT EXISTS idx_sandbox_exec_sandbox_id ON sandbox_exec_log(sandbox_id);
                CREATE INDEX IF NOT EXISTS idx_sandbox_exec_evidence ON sandbox_exec_log(evidence_ref);
                CREATE INDEX IF NOT EXISTS idx_sandbox_exec_ts ON sandbox_exec_log(ts_ms);

                CREATE INDEX IF NOT EXISTS idx_intent_ts ON intent_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_governor_ts ON governor_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_skill_exec_ts ON skill_exec_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_model_control_ts ON model_control_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_error_ts ON error_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_qa_violation_ts ON qa_violation_log(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_memory_ops_ts ON memory_ops_log(ts_ms);
                """
            )

    def _insert(self, table: str, payload: dict[str, Any]) -> None:
        keys = list(payload.keys())
        if not keys:
            return

        serialized = [self._encode_value(table, key, payload[key]) for key in keys]
        placeholders = ", ".join("?" for _ in keys)
        columns = ", ".join(keys)
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        last_exc: Exception | None = None
        for attempt in range(WRITE_RETRY_MAX):
            try:
                with self._write_lock:
                    with self._conn:
                        self._conn.execute(sql, serialized)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower() and attempt < WRITE_RETRY_MAX - 1:
                    last_exc = exc
                    time.sleep(WRITE_RETRY_DELAY_S * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc

    def fetch_by_time_range(
        self,
        table: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按时间窗口查询审计表，返回 ts_ms 在 [start_ms, end_ms] 范围的行。

        参数化查询，表名白名单校验（防 SQL 注入）。
        若表无 ts_ms 列则退化到全量查询 + limit/offset。
        """
        if table not in _ALLOWED_TABLES:
            logger.warning(
                "Rejected fetch_by_time_range for invalid table name: %s (reason_code=%s)",
                table,
                AUDIT_TABLE_NAME_INVALID,
            )
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )

        limit = max(1, min(limit, 10_000))  # 硬上限 10000
        offset = max(0, offset)

        has_ts = self._table_has_column(table, "ts_ms")
        has_id = self._table_has_column(table, "id")

        if has_ts and (start_ms is not None or end_ms is not None):
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append("ts_ms >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append("ts_ms <= ?")
                params.append(int(end_ms))
            where_clause = " AND ".join(conditions)
            order = "ts_ms ASC" if has_ts else ("id ASC" if has_id else "rowid ASC")
            sql = f"SELECT * FROM {table} WHERE {where_clause} ORDER BY {order} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._conn.execute(sql, params).fetchall()
        else:
            order = "ts_ms ASC" if has_ts else ("id ASC" if has_id else "rowid ASC")
            sql = f"SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?"
            rows = self._conn.execute(sql, [limit, offset]).fetchall()

        return [self._decode_row(table, row) for row in rows]

    def count_by_time_range(
        self,
        table: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> int:
        """统计时间窗口内的行数（用于分页 total）。"""
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        has_ts = self._table_has_column(table, "ts_ms")
        if has_ts and (start_ms is not None or end_ms is not None):
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append("ts_ms >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append("ts_ms <= ?")
                params.append(int(end_ms))
            where_clause = " AND ".join(conditions)
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where_clause}", params
            ).fetchone()
        else:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0

    def _fetch_all(self, table: str) -> list[dict[str, Any]]:
        # 表名白名单校验（F-16: 拒绝非法表名防止 SQL 注入）
        if table not in _ALLOWED_TABLES:
            logger.warning(
                "Rejected _fetch_all for invalid table name: %s (reason_code=%s)",
                table,
                AUDIT_TABLE_NAME_INVALID,
            )
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        if self._table_has_column(table, "id"):
            rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY id ASC").fetchall()
        elif self._table_has_column(table, "doc_id"):
            rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY doc_id ASC").fetchall()
        else:
            rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
        return [self._decode_row(table, row) for row in rows]

    def _fetch_by_request(self, table: str, request_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        ).fetchall()
        return [self._decode_row(table, row) for row in rows]

    def _encode_value(self, table: str, key: str, value: Any) -> Any:
        if key in JSON_FIELDS_BY_TABLE.get(table, set()):
            if value is None:
                return None
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

    def _decode_row(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in JSON_FIELDS_BY_TABLE.get(table, set()):
            raw = data.get(key)
            if isinstance(raw, str) and raw:
                try:
                    data[key] = json.loads(raw)
                except json.JSONDecodeError:
                    data[key] = raw
        data.pop("id", None)
        return data

    def _table_has_column(self, table: str, column: str) -> bool:
        # F-06: 使用表结构缓存，避免每次调用 PRAGMA table_info
        if table not in self._column_cache:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            self._column_cache[table] = {str(row["name"]) for row in rows}
        return column in self._column_cache[table]
