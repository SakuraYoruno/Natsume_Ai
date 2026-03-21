"""LedgerQueryMixin：SQLiteLedger 业务查询方法（LLM路由、回放守卫、序号守卫、trace等）。"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from host_brain.core.errors import TRACE_MISSING_GOVERNOR, TRACE_MISSING_INTENT
from host_brain.core.ids import now_ms


class LedgerQueryMixin:
    """业务查询方法 Mixin，需要混入 SQLiteLedger 才能访问 self._conn 等属性。"""

    _conn: sqlite3.Connection
    _write_lock: threading.Lock

    def query_llm_route_config_log(
        self,
        *,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询 LLM 路由配置变更记录（M26-C）。"""
        conditions: list[str] = []
        params: list[Any] = []
        if start_ts_ms is not None:
            conditions.append("ts_ms >= ?")
            params.append(int(start_ts_ms))
        if end_ts_ms is not None:
            conditions.append("ts_ms <= ?")
            params.append(int(end_ts_ms))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM llm_route_config_log {where} ORDER BY ts_ms DESC LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_llm_route_log(
        self,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """按时间窗口查询 llm_route_log（T-AUDIT-LLM-05）。"""
        limit = max(1, min(int(limit), 10_000))
        conditions: list[str] = []
        params: list[Any] = []
        if start_ms is not None:
            conditions.append("ts_ms >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            conditions.append("ts_ms <= ?")
            params.append(int(end_ms))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM llm_route_log {where} ORDER BY ts_ms ASC LIMIT ?",
            params + [limit],
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["fallback_used"] = bool(d.get("fallback_used"))
            d.pop("id", None)
            result.append(d)
        return result

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
        return self._decode_row("vault_manifest_index", row)  # type: ignore[attr-defined]

    def has_replay(self, *, intent_id: str, session_id: str, nonce: str, window_ms: int = 60 * 60 * 1000) -> bool:
        from host_brain.core.ids import now_ms
        cutoff = now_ms() - window_ms
        row = self._conn.execute(
            """
            SELECT 1
            FROM replay_guard
            WHERE ts_ms >= ?
              AND (intent_id = ? OR (session_id = ? AND nonce = ?))
            LIMIT 1
            """,
            (cutoff, intent_id, session_id, nonce),
        ).fetchone()
        return row is not None

    def mark_intent_seen(self, *, intent_id: str, session_id: str, nonce: str) -> None:
        with self._write_lock:
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

    def get_session_seq(self, *, session_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT seq FROM session_seq_guard WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return int(row["seq"])
        except (TypeError, ValueError):
            return None

    def update_seq(self, *, session_id: str, seq: int) -> None:
        with self._write_lock:
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
        intents = self._fetch_by_request("intent_log", request_id)  # type: ignore[attr-defined]
        governors = self._fetch_by_request("governor_log", request_id)  # type: ignore[attr-defined]
        skills = self._fetch_by_request("skill_exec_log", request_id)  # type: ignore[attr-defined]
        model_controls = self._fetch_by_request("model_control_log", request_id)  # type: ignore[attr-defined]

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
        return self._decode_row("skill_exec_log", row)  # type: ignore[attr-defined]
