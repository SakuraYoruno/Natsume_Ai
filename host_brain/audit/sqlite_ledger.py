"""SQLite-backed ledger with triptych schema, WAL mode, and replay-friendly indexes.

WAL (Write-Ahead Logging) mode is enabled by default for improved concurrent
read/write throughput. When WAL activation fails (e.g. read-only filesystem),
the ledger degrades to DELETE journal mode and logs AUDIT_WAL_FAILED.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any
import sqlite3

from host_brain.audit.ledger_constants import (
    DEFAULT_WAL_CONFIG,
    INTENT_COLUMNS,
    GOVERNOR_COLUMNS,
    SKILL_EXEC_COLUMNS,
    MODEL_CONTROL_COLUMNS,
    CANON_PROPOSAL_COLUMNS,
    CANON_CHANGE_COLUMNS,
    WORLD_CANON_DOC_COLUMNS,
    MEMORY_OP_COLUMNS,
    VAULT_MANIFEST_COLUMNS,
    VAULT_GC_COLUMNS,
    AIRLOCK_COLUMNS,
    CHANNEL_IO_COLUMNS,
    AVATAR_ACTION_COLUMNS,
    CLAWD_ACTION_COLUMNS,
    AGENT_LOOP_COLUMNS,
    SERVICE_HEALTH_COLUMNS,
    MIGRATION_COLUMNS,
    CONFIG_SNAPSHOT_COLUMNS,
    VOICE_EVENT_COLUMNS,
    RETRIEVAL_EVENT_COLUMNS,
    VISION_EVENT_COLUMNS,
    BOND_UPDATE_COLUMNS,
    SANDBOX_EXEC_COLUMNS,
    SCHEDULER_EVENT_COLUMNS,
    CAPABILITY_FORGE_PROPOSAL_COLUMNS,
    P8_SUBGRAPH_FAILURE_LOG_COLUMNS,
    P8_GRAPH_RUN_LOG_COLUMNS,
    WalConfig,
)
from host_brain.audit.ledger_fetch_mixin import LedgerFetchMixin
from host_brain.audit.ledger_log_mixin import LedgerLogMixin
from host_brain.audit.ledger_query_mixin import LedgerQueryMixin
from host_brain.audit.ledger_scheduler_mixin import LedgerSchedulerMixin
from host_brain.audit.ledger_wal_mixin import LedgerWalMixin
from host_brain.audit.ledger_schema import apply_schema

from host_brain.core.ids import now_ms

logger = logging.getLogger(__name__)


class SQLiteLedger(
    LedgerFetchMixin,
    LedgerLogMixin,
    LedgerQueryMixin,
    LedgerSchedulerMixin,
    LedgerWalMixin,
):
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
        self._write_lock = threading.RLock()  # RLock allows re-entrant acquisition within transaction()
        self._wal_status: str = "UNKNOWN"
        self._wal_degradation_reason: str | None = None

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        # 表结构缓存：避免每次 _table_has_column 调用 PRAGMA table_info
        self._column_cache: dict[str, set[str]] = {}
        self._column_cache_lock = threading.Lock()
        self._apply_wal_config()
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._ensure_schema()

    # -------------------------------------------------------------------
    # Properties（快捷访问各日志表）
    # -------------------------------------------------------------------

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
    def agent_loop_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("agent_loop_log")

    @property
    def supervisor_decision_log(self) -> list[dict[str, Any]]:
        """P5-4：Supervisor 决策观测日志（rule/llm/llm_fallback_rule、failure_code、selected_agent）。"""
        return self._fetch_all("supervisor_decision_log")

    @property
    def p8_subgraph_failure_log(self) -> list[dict[str, Any]]:
        """P8-1：subgraph/local loop 失败分布聚合日志。"""
        return self._fetch_all("p8_subgraph_failure_log")

    @property
    def p8_graph_run_log(self) -> list[dict[str, Any]]:
        """P8-1：三图 run_graph 执行记录（延迟聚合 + Intent budget breach 聚合）。"""
        return self._fetch_all("p8_graph_run_log")

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

    @property
    def capability_forge_proposal_log(self) -> list[dict[str, Any]]:
        return self._fetch_all("capability_forge_proposal_log")

    # -------------------------------------------------------------------
    # 写入方法（业务 log_* 方法）
    # -------------------------------------------------------------------

    def log_intent(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in INTENT_COLUMNS}
        # M31-C：向后兼容 lint_result_json 字段
        if "lint_result_json" in row and row["lint_result_json"] is not None:
            payload["lint_result"] = row["lint_result_json"]
        if payload.get("tool_visibility_hash") is None:
            payload["tool_visibility_hash"] = ""
        if payload.get("lint_result") is None:
            payload["lint_result"] = ""
        self._insert("intent_log", payload)

    def log_governor(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in GOVERNOR_COLUMNS}
        # FIX-4.2：tribe_params_snapshot 需要 JSON 序列化（dict → str）
        if payload.get("tribe_params_snapshot") is not None and isinstance(payload["tribe_params_snapshot"], dict):
            payload["tribe_params_snapshot"] = json.dumps(
                payload["tribe_params_snapshot"], ensure_ascii=False, default=str
            )
        self._insert("governor_log", payload)

    def log_skill_exec(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SKILL_EXEC_COLUMNS}
        if not payload.get("event_id"):
            payload["event_id"] = f"evt_{uuid.uuid4().hex}"
        self._insert("skill_exec_log", payload)

    def log_model_control(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in MODEL_CONTROL_COLUMNS}
        self._insert("model_control_log", payload)

    def log_supervisor_decision(self, row: dict[str, Any]) -> None:
        """P5-4：记录一次 Supervisor 决策（rule/llm/llm_fallback_rule、failure_code、selected_agent）。"""
        payload = {
            "request_id": str(row.get("request_id") or ""),
            "ts_ms": int(row.get("ts_ms") or 0),
            "supervisor_mode": str(row.get("supervisor_mode") or "rule").strip(),
            "supervisor_decision_source": str(row.get("supervisor_decision_source") or "rule").strip(),
            "supervisor_failure_code": str(row.get("supervisor_failure_code") or "").strip() or None,
            "supervisor_selected_agent": str(row.get("supervisor_selected_agent") or "").strip(),
        }
        self._insert("supervisor_decision_log", payload)

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
        # INSERT OR IGNORE：proposal_id UNIQUE 约束下幂等写入，已存在则跳过
        cols = [k for k, v in payload.items() if v is not None or k in ("proposal_id", "canon_kind", "target_id", "status", "ts_ms")]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [payload[c] for c in cols]
        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO canon_proposal_log ({col_names}) VALUES ({placeholders})",
                    values,
                )

    def log_canon_change(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CANON_CHANGE_COLUMNS}
        self._insert("canon_change_log", payload)

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

    def log_agent_loop(self, row: dict[str, Any]) -> None:
        """写入 Agent Loop 中间状态（每轮/每阶段一行）。

        必填字段：request_id, session_id, goal, phase, ts_ms
        可选字段：round, status, goal_achieved, action_plan_json, report_json,
                  action_count, success_count, failed_count, latency_ms, error, extra_json
        """
        payload = {key: row.get(key) for key in AGENT_LOOP_COLUMNS}
        # goal_achieved 存为 0/1 整数
        if payload.get("goal_achieved") is None:
            payload["goal_achieved"] = 0
        else:
            payload["goal_achieved"] = 1 if payload["goal_achieved"] else 0
        # round 默认 0（loop 级事件）
        if payload.get("round") is None:
            payload["round"] = 0
        self._insert("agent_loop_log", payload)

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

    def log_scheduler_event(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in SCHEDULER_EVENT_COLUMNS}
        self._insert("scheduler_event_log", payload)

    def log_capability_forge_proposal(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in CAPABILITY_FORGE_PROPOSAL_COLUMNS}
        payload["proposal_id"] = str(payload.get("proposal_id") or "").strip()
        payload["event_type"] = str(payload.get("event_type") or "").strip().upper() or "UNKNOWN"
        payload["status"] = str(payload.get("status") or "").strip().upper() or "UNKNOWN"
        payload["stage"] = str(payload.get("stage") or "").strip().upper() or "UNKNOWN"
        payload["session_id"] = str(payload.get("session_id") or "").strip() or None
        payload["candidate_key"] = str(payload.get("candidate_key") or "").strip() or None
        payload["reason_code"] = str(payload.get("reason_code") or "").strip().upper() or None
        payload["actor"] = str(payload.get("actor") or "").strip() or None
        payload["ts_ms"] = int(payload.get("ts_ms") or now_ms())
        self._insert("capability_forge_proposal_log", payload)

    def log_p8_subgraph_failure(self, row: dict[str, Any]) -> None:
        """P8-1：写入 subgraph/local loop 失败记录。

        必填字段：request_id, ts_ms, log_category ('subgraph' | 'local_loop'), error_code
        可选字段：execution_profile, failed_node, detail
        """
        payload = {key: row.get(key) for key in P8_SUBGRAPH_FAILURE_LOG_COLUMNS}
        self._insert("p8_subgraph_failure_log", payload)

    def log_p8_graph_run(self, row: dict[str, Any]) -> None:
        """P8-1：写入一次 run_graph 执行记录（延迟 + budget breach）。

        必填字段：request_id, ts_ms
        可选字段：execution_profile, latency_ms, status, budget_breach_code
        """
        payload = {key: row.get(key) for key in P8_GRAPH_RUN_LOG_COLUMNS}
        self._insert("p8_graph_run_log", payload)

    def list_capability_forge_proposals(
        self,
        *,
        proposal_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 20000))
        offset = max(0, int(offset))
        conditions: list[str] = []
        params: list[Any] = []
        if proposal_id is not None and str(proposal_id).strip():
            conditions.append("proposal_id = ?")
            params.append(str(proposal_id).strip())
        if status is not None and str(status).strip():
            conditions.append("status = ?")
            params.append(str(status).strip().upper())
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM capability_forge_proposal_log {where_clause} ORDER BY ts_ms ASC, id ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._decode_row("capability_forge_proposal_log", row) for row in rows]

    def get_latest_capability_forge_proposals(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.list_capability_forge_proposals(limit=max(1, int(limit)) * 20, offset=0)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            proposal_id = str(row.get("proposal_id") or "").strip()
            if not proposal_id:
                continue
            latest[proposal_id] = dict(row)
        ordered = sorted(
            latest.values(),
            key=lambda item: int(item.get("ts_ms") or 0),
            reverse=True,
        )
        return ordered[: max(1, int(limit))]

    # -------------------------------------------------------------------
    # Canon 提案查询与更新
    # -------------------------------------------------------------------

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """按 proposal_id 获取最新的 canon_proposal_log 行。"""
        row = self._conn.execute(
            "SELECT * FROM canon_proposal_log WHERE proposal_id = ? ORDER BY id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("canon_proposal_log", row)

    def get_canon_change_by_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """按 proposal_id 获取对应的 canon_change_log 行（含 before_body / after_body）。"""
        row = self._conn.execute(
            "SELECT * FROM canon_change_log WHERE proposal_id = ? ORDER BY id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_row("canon_change_log", row)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        canon_kind: str | None = None,
        target_doc_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        after_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出 canon_proposal_log 记录，支持过滤和分页。"""
        limit = min(int(limit), 100)
        offset = max(int(offset), 0)

        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if canon_kind is not None:
            conditions.append("canon_kind = ?")
            params.append(str(canon_kind))
        if target_doc_id is not None:
            conditions.append("target_id = ?")
            params.append(str(target_doc_id))

        # F-38: cursor-based pagination（after_id 优先于 offset）
        use_cursor = after_id is not None
        if use_cursor:
            conditions.append("id < ?")
            params.append(int(after_id))

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM canon_proposal_log {where_clause}",
            params,
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        if use_cursor:
            rows = self._conn.execute(
                f"SELECT * FROM canon_proposal_log {where_clause} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT * FROM canon_proposal_log {where_clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            raw_id = row["id"] if "id" in row.keys() else None
            decoded = self._decode_row("canon_proposal_log", row)
            if raw_id is not None:
                decoded["row_id"] = int(raw_id)
            items.append(decoded)
        return items, total

    def update_proposal_status(
        self,
        proposal_id: str,
        *,
        new_status: str,
        ts_ms: int | None = None,
    ) -> bool:
        """按 proposal_id 更新提案状态和时间戳。"""
        update_ts = int(ts_ms if ts_ms is not None else now_ms())
        with self._write_lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE canon_proposal_log SET status = ?, ts_ms = ? WHERE proposal_id = ?",
                    (new_status, update_ts, proposal_id),
                )
        return cursor.rowcount > 0

    # -------------------------------------------------------------------
    # World Canon Doc
    # -------------------------------------------------------------------

    def upsert_world_canon_doc(self, row: dict[str, Any]) -> None:
        payload = {key: row.get(key) for key in WORLD_CANON_DOC_COLUMNS}
        if not payload.get("doc_id"):
            raise ValueError("world_canon_doc requires doc_id")

        with self._write_lock:
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

    # -------------------------------------------------------------------
    # M10-C: 语义记忆 CRUD
    # -------------------------------------------------------------------

    def log_semantic_memory(self, row: dict[str, Any]) -> None:
        """写入语义记忆条目（INSERT OR IGNORE，幂等）。"""
        source_ids = row.get("source_memory_ids", [])
        tags = row.get("tags", [])
        payload = {
            "semantic_id": str(row.get("semantic_id", "")),
            "summary": str(row.get("summary", "")),
            "source_memory_ids": json.dumps(list(source_ids), ensure_ascii=False),
            "source_hash": str(row.get("source_hash", "")),
            "distill_strategy": str(row.get("distill_strategy", "merge_top_k")),
            "confidence": float(row.get("confidence", 0.0)),
            "tags": json.dumps(list(tags), ensure_ascii=False),
            "ts_ms": int(row.get("ts_ms", 0)),
            "operator_node_id": str(row.get("operator_node_id", "distillation_engine")),
        }
        cols = list(payload.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [payload[c] for c in cols]
        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO semantic_memory ({col_names}) VALUES ({placeholders})",
                    values,
                )

    def get_semantic_memory_by_source_hash(self, source_hash: str) -> dict[str, Any] | None:
        """按 source_hash 查询语义记忆（幂等写入查重用）。"""
        row = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE source_hash = ? AND deleted_at IS NULL LIMIT 1",
            (str(source_hash),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_semantic_memory(row)

    def get_semantic_memory_by_id(self, semantic_id: str) -> dict[str, Any] | None:
        """按 semantic_id 精确查询语义记忆（O(1) 索引查询，避免全量扫描）。"""
        row = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE semantic_id = ? AND deleted_at IS NULL LIMIT 1",
            (str(semantic_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_semantic_memory(row)

    def list_semantic_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出语义记忆（分页，按 ts_ms 降序）。"""
        total_row = self._conn.execute(
            "SELECT COUNT(*) FROM semantic_memory WHERE deleted_at IS NULL"
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        rows = self._conn.execute(
            "SELECT * FROM semantic_memory WHERE deleted_at IS NULL "
            "ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        ).fetchall()
        items = [self._row_to_semantic_memory(r) for r in rows]
        return items, total

    def _row_to_semantic_memory(self, row: Any) -> dict[str, Any]:
        """将 semantic_memory sqlite 行转换为 Python dict。"""
        d = dict(row)
        for json_field in ("source_memory_ids", "tags"):
            raw = d.get(json_field, "[]")
            try:
                d[json_field] = json.loads(raw) if isinstance(raw, str) else list(raw or [])
            except (ValueError, TypeError):
                d[json_field] = []
        return d

    # -------------------------------------------------------------------
    # 事务上下文管理器
    # -------------------------------------------------------------------

    def transaction(self):
        """上下文管理器：将多个 DB 操作包裹在单个 SQLite 事务中（F-35）。

        用法::

            with ledger.transaction():
                ledger.update_proposal_status(...)
                ledger.log_canon_change(...)

        事务内任何异常都会触发 ROLLBACK，正常退出则 COMMIT。
        """
        @contextlib.contextmanager
        def _tx():
            with self._write_lock:
                self._conn.execute("BEGIN")
                try:
                    yield
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

        return _tx()

    # -------------------------------------------------------------------
    # Schema 初始化
    # -------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        apply_schema(self._conn)
