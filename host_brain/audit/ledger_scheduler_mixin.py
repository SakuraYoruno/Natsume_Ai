"""LedgerSchedulerMixin：调度器任务持久化方法。"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from host_brain.core.ids import now_ms


class LedgerSchedulerMixin:
    """调度器相关的持久化方法 Mixin。"""

    _conn: sqlite3.Connection
    _write_lock: threading.Lock

    def persist_scheduler_task(self, task_config: dict[str, Any]) -> None:
        """持久化调度器任务配置到 scheduler_tasks 表（F-40 修复）。"""
        ts = int(task_config.get("registered_at", now_ms()))
        with self._write_lock:
            self._conn.execute(
                """
                INSERT INTO scheduler_tasks
                  (task_id, task_name, trigger_mode, trigger_config, intent_template,
                   priority, enabled, max_consecutive_failures, registered_at, updated_at,
                   registered_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  task_name=excluded.task_name,
                  trigger_mode=excluded.trigger_mode,
                  trigger_config=excluded.trigger_config,
                  intent_template=excluded.intent_template,
                  priority=excluded.priority,
                  enabled=excluded.enabled,
                  max_consecutive_failures=excluded.max_consecutive_failures,
                  updated_at=excluded.updated_at,
                  registered_by=excluded.registered_by
                """,
                (
                    str(task_config["task_id"]),
                    str(task_config.get("task_name", task_config["task_id"])),
                    str(task_config["trigger_mode"]),
                    json.dumps(task_config.get("trigger_config", {}), ensure_ascii=False),
                    json.dumps(task_config.get("intent_template", {}), ensure_ascii=False),
                    str(task_config.get("priority", "MEDIUM")),
                    int(task_config.get("enabled", 1)),
                    int(task_config.get("max_consecutive_failures", 3)),
                    ts,
                    ts,
                    str(task_config.get("registered_by", "scheduler")),
                ),
            )
            self._conn.commit()

    def load_persisted_scheduler_tasks(self) -> list[dict[str, Any]]:
        """从 scheduler_tasks 表加载所有持久化任务配置（F-40 修复）。"""
        rows = self._conn.execute(
            "SELECT * FROM scheduler_tasks ORDER BY id ASC"
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["trigger_config"] = json.loads(d.get("trigger_config", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["trigger_config"] = {}
            try:
                d["intent_template"] = json.loads(d.get("intent_template", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["intent_template"] = {}
            result.append(d)
        return result

    def load_latest_scheduler_event_trigger_ts(self, task_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, int]:
        """返回每个调度任务最近一次触发时间，用于进程重启后恢复去重状态。"""
        normalized_task_ids = [str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()]
        params: list[Any] = []
        filters = ["status NOT IN ('SKIPPED', 'REJECTED', 'DEFERRED', 'RETRYING', 'ERROR', 'FAILED', 'SUSPENDED')"]
        if normalized_task_ids:
            placeholders = ",".join("?" for _ in normalized_task_ids)
            filters.append(f"task_id IN ({placeholders})")
            params.extend(normalized_task_ids)
        where = "WHERE " + " AND ".join(filters)
        try:
            rows = self._conn.execute(
                f"""
                SELECT task_id, MAX(trigger_ts) AS trigger_ts
                FROM scheduler_event_log
                {where}
                GROUP BY task_id
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        result: dict[str, int] = {}
        for row in rows:
            task_id = str(row["task_id"] or "").strip()
            if not task_id:
                continue
            try:
                trigger_ts = int(row["trigger_ts"] or 0)
            except (TypeError, ValueError):
                trigger_ts = 0
            if trigger_ts > 0:
                result[task_id] = trigger_ts
        return result

    def delete_persisted_scheduler_task(self, task_id: str) -> bool:
        """从 scheduler_tasks 表删除指定任务（任务注销时调用）。"""
        with self._write_lock:
            cursor = self._conn.execute(
                "DELETE FROM scheduler_tasks WHERE task_id = ?",
                (str(task_id),),
            )
            self._conn.commit()
            return cursor.rowcount > 0
