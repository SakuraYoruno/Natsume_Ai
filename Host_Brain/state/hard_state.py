from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS hard_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  user_text_cn TEXT NOT NULL,
  context_pack_json TEXT NOT NULL,
  dify_raw_json TEXT NOT NULL,
  assistant_reply_json TEXT NOT NULL,
  qa_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_history_ts ON chat_history(ts_utc);
CREATE INDEX IF NOT EXISTS idx_chat_history_request_id ON chat_history(request_id);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HardStateLedger:
    """
    SQLite Hard Ledger（事实与审计链落盘）：
    - hard_state：键值事实（可更新）
    - chat_history：append-only 审计记录（对话、context_pack、Dify 原始输出、Host 审计）
    """
    def __init__(self, sqlite_path: str) -> None:
        self.sqlite_path = sqlite_path
        Path(os.path.dirname(sqlite_path) or ".").mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.sqlite_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()

    def new_request_id(self) -> str:
        return uuid.uuid4().hex

    def set_state(self, key: str, value: str) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO hard_state(key, value, updated_at_utc) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_utc=excluded.updated_at_utc",
                (key, value, _utc_now_iso()),
            )
            con.commit()
        finally:
            con.close()

    def get_state(self, key: str) -> Optional[str]:
        con = self._connect()
        try:
            row = con.execute("SELECT value FROM hard_state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            con.close()

    def read_hard_state_snapshot(self, limit_keys: int = 200) -> Dict[str, Any]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT key, value, updated_at_utc FROM hard_state ORDER BY updated_at_utc DESC LIMIT ?",
                (limit_keys,),
            ).fetchall()
            out: Dict[str, Any] = {}
            for r in rows:
                out[r["key"]] = {"value": r["value"], "updated_at_utc": r["updated_at_utc"]}
            return out
        finally:
            con.close()

    def append_chat_history(
        self,
        request_id: str,
        user_text_cn: str,
        context_pack_json: str,
        dify_raw_json: str,
        assistant_reply_json: str,
        qa_json: str,
    ) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO chat_history(request_id, ts_utc, user_text_cn, context_pack_json, dify_raw_json, assistant_reply_json, qa_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (request_id, _utc_now_iso(), user_text_cn, context_pack_json, dify_raw_json, assistant_reply_json, qa_json),
            )
            con.commit()
        finally:
            con.close()

    def read_recent_dialogue(self, limit_turns: int = 6) -> List[Dict[str, str]]:
        """
        读取最近 N 轮（按时间正序），仅用于给 Dify 叙事稳定性提供“近因”。
        返回结构：
          [{"user_cn": "...", "assistant_ja": "..."} ...]
        """
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT user_text_cn, assistant_reply_json FROM chat_history ORDER BY id DESC LIMIT ?",
                (max(1, limit_turns),),
            ).fetchall()
        finally:
            con.close()

        rows = list(reversed(rows))
        out: List[Dict[str, str]] = []
        for r in rows:
            user_cn = r["user_text_cn"] or ""
            assistant_ja = ""
            try:
                import json
                obj = json.loads(r["assistant_reply_json"] or "{}")
                assistant_ja = str(obj.get("narrative_ja", "") or "")
            except Exception:
                assistant_ja = ""
            out.append({"user_cn": user_cn, "assistant_ja": assistant_ja})
        return out
