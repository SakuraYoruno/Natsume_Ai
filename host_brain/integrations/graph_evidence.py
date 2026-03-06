"""Evidence Plane SQLite 写入（M36-E）。

实现 Evidence Plane 的 SQLite 轻量写入（ModelPlane.md §10 Rule 3）：
- EvidencePlane：封装 SQLite 连接，提供 write_trace / write_subgraph_summary / write_clock_events
- 写入失败时静默降级（不抛出异常，记录 logging.warning）
- 数据库路径可通过 EVIDENCE_DB_PATH 环境变量覆盖

表结构：
- graph_trace：request_id, graph_id, node_id, status, reason_code, detail, ts_ms
- subgraph_summary：request_id, subgraph_id, node_count, success_count, fail_count, committed_at_ms, graph_trace_flat_json
- clock_events：request_id, graph_id, event_type, node_id, elapsed_ms, threshold_ms, ts_ms
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("data/evidence/evidence.db")
_SINGLETON: EvidencePlane | None = None


def get_evidence_plane() -> "EvidencePlane":
    """获取 EvidencePlane 单例（懒初始化）。"""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = EvidencePlane()
    return _SINGLETON


class EvidencePlane:
    """Evidence Plane SQLite 写入器。

    写入失败时静默降级（不抛出异常，记录 logging.warning）。
    数据库路径可通过 EVIDENCE_DB_PATH 环境变量覆盖。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        env_path = os.environ.get("EVIDENCE_DB_PATH", "").strip()
        if db_path is not None:
            self._db_path = Path(db_path)
        elif env_path:
            self._db_path = Path(env_path)
        else:
            self._db_path = _DEFAULT_DB_PATH

        self._initialized = False
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库，创建表结构（若不存在）。"""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS graph_trace (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        reason_code TEXT,
                        detail TEXT,
                        ts_ms INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS subgraph_summary (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        subgraph_id TEXT NOT NULL,
                        node_count INTEGER NOT NULL,
                        success_count INTEGER NOT NULL,
                        fail_count INTEGER NOT NULL,
                        committed_at_ms INTEGER NOT NULL,
                        graph_trace_flat_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS clock_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        elapsed_ms INTEGER NOT NULL,
                        threshold_ms INTEGER NOT NULL,
                        ts_ms INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS branch_manifest (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        total_count INTEGER NOT NULL,
                        success_count INTEGER NOT NULL,
                        fail_count INTEGER NOT NULL,
                        committed_at_ms INTEGER NOT NULL,
                        branches_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS stream_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        node_id TEXT,
                        chunk TEXT,
                        ts_ms INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        checkpoint_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        interrupt_reason TEXT,
                        resume_deadline_at INTEGER,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS barrier_join_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        passed INTEGER NOT NULL,
                        failed_branches_json TEXT,
                        join_policy_ref TEXT,
                        reason TEXT,
                        ts_ms INTEGER NOT NULL
                    );
                """)
            self._initialized = True
        except Exception as exc:
            logger.warning("EvidencePlane 初始化失败（静默降级）: %s", exc)
            self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        """创建 SQLite 连接（check_same_thread=False 支持多线程）。"""
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    def write_trace(
        self,
        *,
        request_id: str,
        graph_id: str,
        trace: list[dict[str, Any]],
    ) -> None:
        """将 trace 列表写入 graph_trace 表。

        Args:
            request_id: 请求 ID
            graph_id: 图 ID
            trace: GraphState.trace 列表
        """
        if not self._initialized or not trace:
            return
        try:
            ts_ms = int(time.time() * 1000)
            rows = [
                (
                    str(request_id),
                    str(graph_id),
                    str(entry.get("node_id") or ""),
                    str(entry.get("status") or "OK"),
                    entry.get("reason_code"),
                    str(entry.get("detail") or ""),
                    int(entry.get("ts_ms") or ts_ms),
                )
                for entry in trace
                if isinstance(entry, dict)
            ]
            with self._connect() as conn:
                conn.executemany(
                    "INSERT INTO graph_trace (request_id, graph_id, node_id, status, reason_code, detail, ts_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_trace 失败（静默降级）: %s", exc)

    def write_subgraph_summary(
        self,
        *,
        request_id: str,
        summary: dict[str, Any],
    ) -> None:
        """将 subgraph_commit_summary 写入 subgraph_summary 表。

        Args:
            request_id: 请求 ID
            summary: build_subgraph_commit_summary() 返回的字典
        """
        if not self._initialized or not summary:
            return
        try:
            flat_json = json.dumps(
                summary.get("graph_trace_flat") or [],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO subgraph_summary "
                    "(request_id, subgraph_id, node_count, success_count, fail_count, committed_at_ms, graph_trace_flat_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(request_id),
                        str(summary.get("subgraph_id") or ""),
                        int(summary.get("node_count") or 0),
                        int(summary.get("success_count") or 0),
                        int(summary.get("fail_count") or 0),
                        int(summary.get("committed_at_ms") or int(time.time() * 1000)),
                        flat_json,
                    ),
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_subgraph_summary 失败（静默降级）: %s", exc)

    def write_clock_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        clock_events: list[dict[str, Any]],
    ) -> None:
        """将 clock_events 写入 clock_events 表。

        Args:
            request_id: 请求 ID
            graph_id: 图 ID
            clock_events: GraphState.clock_events 列表
        """
        if not self._initialized or not clock_events:
            return
        try:
            ts_ms = int(time.time() * 1000)
            rows = [
                (
                    str(request_id),
                    str(graph_id),
                    str(event.get("event_type") or ""),
                    str(event.get("node_id") or ""),
                    int(event.get("elapsed_ms") or 0),
                    int(event.get("threshold_ms") or 0),
                    int(event.get("ts_ms") or ts_ms),
                )
                for event in clock_events
                if isinstance(event, dict)
            ]
            with self._connect() as conn:
                conn.executemany(
                    "INSERT INTO clock_events "
                    "(request_id, graph_id, event_type, node_id, elapsed_ms, threshold_ms, ts_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_clock_events 失败（静默降级）: %s", exc)

    def write_branch_manifest(
        self,
        *,
        request_id: str,
        graph_id: str,
        manifest: dict[str, Any],
    ) -> None:
        """将 branch_manifest 写入 branch_manifest 表（M37-L）。

        Args:
            request_id: 请求 ID
            graph_id: 图 ID
            manifest: build_branch_manifest_dict() 返回的字典
        """
        if not self._initialized or not manifest:
            return
        try:
            branches_json = json.dumps(
                manifest.get("branches") or [],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO branch_manifest "
                    "(request_id, graph_id, total_count, success_count, fail_count, committed_at_ms, branches_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(request_id),
                        str(graph_id),
                        int(manifest.get("total_count") or 0),
                        int(manifest.get("success_count") or 0),
                        int(manifest.get("fail_count") or 0),
                        int(manifest.get("committed_at_ms") or int(time.time() * 1000)),
                        branches_json,
                    ),
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_branch_manifest 失败（静默降级）: %s", exc)

    def query_branch_manifest(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 branch_manifest 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT request_id, graph_id, total_count, success_count, fail_count, committed_at_ms, branches_json "
                    "FROM branch_manifest WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                cols = ["request_id", "graph_id", "total_count", "success_count", "fail_count", "committed_at_ms", "branches_json"]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("EvidencePlane.query_branch_manifest 失败: %s", exc)
            return []

    def write_stream_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        """将 streaming 事件列表写入 stream_events 表（M38-H）。

        Args:
            request_id: 请求 ID
            graph_id: 图 ID
            events: 流事件列表（PresenceEvent.to_dict() 格式）
        """
        if not self._initialized or not events:
            return
        try:
            ts_ms = int(time.time() * 1000)
            rows = [
                (
                    str(request_id),
                    str(graph_id),
                    str(event.get("phase") or ""),
                    str(event.get("event_type") or ""),
                    str(event.get("node_id") or ""),
                    str(event.get("chunk") or ""),
                    int(event.get("ts_ms") or ts_ms),
                )
                for event in events
                if isinstance(event, dict)
            ]
            with self._connect() as conn:
                conn.executemany(
                    "INSERT INTO stream_events "
                    "(request_id, graph_id, phase, event_type, node_id, chunk, ts_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_stream_events 失败（静默降级）: %s", exc)

    def query_stream_events(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 stream_events 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT request_id, graph_id, phase, event_type, node_id, chunk, ts_ms "
                    "FROM stream_events WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                cols = ["request_id", "graph_id", "phase", "event_type", "node_id", "chunk", "ts_ms"]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("EvidencePlane.query_stream_events 失败: %s", exc)
            return []

    def write_checkpoint(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        graph_id: str,
        interrupt_reason: str = "",
        resume_deadline_at: int = 0,
    ) -> None:
        """将 checkpoint 信息写入 checkpoints 表（M39-G）。

        Args:
            checkpoint_id: checkpoint ID
            request_id: 请求 ID
            graph_id: 图 ID
            interrupt_reason: 中断原因
            resume_deadline_at: 恢复截止时间戳（毫秒）
        """
        if not self._initialized:
            return
        try:
            ts_ms = int(time.time() * 1000)
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints "
                    "(checkpoint_id, request_id, graph_id, interrupt_reason, resume_deadline_at, created_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(checkpoint_id),
                        str(request_id),
                        str(graph_id),
                        str(interrupt_reason),
                        int(resume_deadline_at),
                        ts_ms,
                    ),
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_checkpoint 失败（静默降级）: %s", exc)

    def query_checkpoint(self, *, checkpoint_id: str) -> dict[str, Any] | None:
        """查询指定 checkpoint_id 的记录（测试用）。"""
        if not self._initialized:
            return None
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT checkpoint_id, request_id, graph_id, interrupt_reason, resume_deadline_at, created_at_ms "
                    "FROM checkpoints WHERE checkpoint_id = ?",
                    (str(checkpoint_id),),
                )
                row = cursor.fetchone()
                if row:
                    cols = ["checkpoint_id", "request_id", "graph_id", "interrupt_reason", "resume_deadline_at", "created_at_ms"]
                    return dict(zip(cols, row))
        except Exception as exc:
            logger.warning("EvidencePlane.query_checkpoint 失败: %s", exc)
        return None

    def write_barrier_join(
        self,
        *,
        request_id: str,
        graph_id: str,
        barrier_join_result: dict[str, Any],
    ) -> None:
        """将 barrier_join 结果写入 barrier_join_log 表（M40-H）。

        Args:
            request_id: 请求 ID
            graph_id: 图 ID
            barrier_join_result: BarrierJoinResult.to_dict() 返回的字典
        """
        if not self._initialized:
            return
        try:
            import json as _json
            ts_ms = int(time.time() * 1000)
            passed = int(bool(barrier_join_result.get("passed")))
            failed_branches = barrier_join_result.get("failed_branches") or []
            failed_branches_json = _json.dumps(list(failed_branches), ensure_ascii=False)
            join_policy_ref = str(barrier_join_result.get("join_policy_ref") or "")
            reason = str(barrier_join_result.get("reason") or "")
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO barrier_join_log "
                    "(request_id, graph_id, passed, failed_branches_json, join_policy_ref, reason, ts_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(request_id),
                        str(graph_id),
                        passed,
                        failed_branches_json,
                        join_policy_ref,
                        reason,
                        ts_ms,
                    ),
                )
        except Exception as exc:
            logger.warning("EvidencePlane.write_barrier_join 失败（静默降级）: %s", exc)

    def query_barrier_join(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 barrier_join 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            import json as _json
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT id, request_id, graph_id, passed, failed_branches_json, join_policy_ref, reason, ts_ms "
                    "FROM barrier_join_log WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                rows = cursor.fetchall()
                cols = ["id", "request_id", "graph_id", "passed", "failed_branches_json", "join_policy_ref", "reason", "ts_ms"]
                result = []
                for row in rows:
                    d = dict(zip(cols, row))
                    d["passed"] = bool(d["passed"])
                    try:
                        d["failed_branches"] = _json.loads(d.get("failed_branches_json") or "[]")
                    except Exception:
                        d["failed_branches"] = []
                    result.append(d)
                return result
        except Exception as exc:
            logger.warning("EvidencePlane.query_barrier_join 失败: %s", exc)
        return []

    def query_trace(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 trace 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT request_id, graph_id, node_id, status, reason_code, detail, ts_ms "
                    "FROM graph_trace WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                cols = ["request_id", "graph_id", "node_id", "status", "reason_code", "detail", "ts_ms"]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("EvidencePlane.query_trace 失败: %s", exc)
            return []

    def query_subgraph_summary(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 subgraph_summary 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT request_id, subgraph_id, node_count, success_count, fail_count, committed_at_ms, graph_trace_flat_json "
                    "FROM subgraph_summary WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                cols = ["request_id", "subgraph_id", "node_count", "success_count", "fail_count", "committed_at_ms", "graph_trace_flat_json"]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("EvidencePlane.query_subgraph_summary 失败: %s", exc)
            return []

    def query_clock_events(self, *, request_id: str) -> list[dict[str, Any]]:
        """查询指定 request_id 的 clock_events 记录（测试用）。"""
        if not self._initialized:
            return []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT request_id, graph_id, event_type, node_id, elapsed_ms, threshold_ms, ts_ms "
                    "FROM clock_events WHERE request_id = ? ORDER BY id",
                    (str(request_id),),
                )
                cols = ["request_id", "graph_id", "event_type", "node_id", "elapsed_ms", "threshold_ms", "ts_ms"]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("EvidencePlane.query_clock_events 失败: %s", exc)
            return []
