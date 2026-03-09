"""LedgerWalMixin：SQLiteLedger WAL 配置、健康检查与检查点方法。"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from host_brain.audit.ledger_constants import WalConfig
from host_brain.core.errors import AUDIT_WAL_CHECKPOINT_FAILED, AUDIT_WAL_FAILED

logger = logging.getLogger(__name__)


class LedgerWalMixin:
    """WAL lifecycle 方法 Mixin，需要混入 SQLiteLedger 才能访问 self._conn 等属性。"""

    _conn: sqlite3.Connection
    _wal_config: WalConfig
    _wal_status: str
    _wal_degradation_reason: str | None
    db_path: str

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
