"""VTS 表情映射表持久化存储（TASK-0214 M24-B）。

emotion_tag→expression_id 映射表持久化到 SQLite，支持版本化管理。
映射表变更写入 vts_mapping_change_log（变更前后 hash/version）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    AVATAR_INVALID_EMOTION_TAG,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    VTS_MAPPING_LOAD_FAILED,
)
from host_brain.core.ids import now_ms


# 默认映射表（与 channels/vts.py 保持一致）
DEFAULT_EMOTION_MAPPING: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vts_expression_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    emotion_tag TEXT NOT NULL,
    expression_id TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL,
    UNIQUE(version, emotion_tag)
);

CREATE TABLE IF NOT EXISTS vts_mapping_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    prev_version TEXT,
    prev_hash TEXT,
    new_hash TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT 'system',
    ts_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class VTSMappingVersion:
    """映射表版本信息。"""
    version: str
    mapping: dict[str, str]
    content_hash: str
    created_ts_ms: int


class VTSMappingStore:
    """VTS 表情映射表持久化存储（TASK-0214 M24-B）。

    映射表持久化到 SQLite，支持版本化管理。
    映射表变更写入 vts_mapping_change_log（变更前后 hash/version）。
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        """初始化映射表存储。

        Args:
            db_path: SQLite 数据库路径。None 时使用内存数据库（仅用于测试）。
        """
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._ensure_schema()
        self._ensure_default_mapping()

    def _ensure_schema(self) -> None:
        """确保数据库表结构存在。"""
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

    def _ensure_default_mapping(self) -> None:
        """确保默认映射表存在（首次初始化时写入）。"""
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM vts_expression_mapping WHERE version = 'v1.0.0'"
            ).fetchone()[0]
            if count == 0:
                self._write_mapping_locked(
                    mapping=DEFAULT_EMOTION_MAPPING,
                    version="v1.0.0",
                    prev_version=None,
                    changed_by="system_init",
                )

    def _compute_hash(self, mapping: dict[str, str]) -> str:
        """计算映射表内容 hash。"""
        canonical = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"

    def _write_mapping_locked(
        self,
        *,
        mapping: dict[str, str],
        version: str,
        prev_version: str | None,
        changed_by: str = "system",
    ) -> str:
        """写入映射表（已持有锁）。返回新版本 hash。"""
        ts = now_ms()
        new_hash = self._compute_hash(mapping)

        # 获取前一版本 hash
        prev_hash: str | None = None
        if prev_version:
            rows = self._conn.execute(
                "SELECT emotion_tag, expression_id FROM vts_expression_mapping WHERE version = ?",
                (prev_version,),
            ).fetchall()
            if rows:
                prev_mapping = {r["emotion_tag"]: r["expression_id"] for r in rows}
                prev_hash = self._compute_hash(prev_mapping)

        # 写入映射条目
        for emotion_tag, expression_id in mapping.items():
            self._conn.execute(
                """
                INSERT OR REPLACE INTO vts_expression_mapping
                    (version, emotion_tag, expression_id, created_ts_ms)
                VALUES (?, ?, ?, ?)
                """,
                (version, emotion_tag.upper(), expression_id, ts),
            )

        # 写入变更日志
        self._conn.execute(
            """
            INSERT INTO vts_mapping_change_log
                (version, prev_version, prev_hash, new_hash, changed_by, ts_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (version, prev_version, prev_hash, new_hash, changed_by, ts),
        )
        self._conn.commit()
        return new_hash

    def save_mapping(
        self,
        mapping: dict[str, str],
        *,
        version: str,
        changed_by: str = "system",
    ) -> str:
        """保存映射表并写 vts_mapping_change_log。

        Args:
            mapping: emotion_tag→expression_id 映射字典。
            version: 新版本号（如 "v1.1.0"）。
            changed_by: 变更来源（用于审计）。

        Returns:
            新版本的内容 hash。
        """
        with self._lock:
            # 获取当前最新版本
            row = self._conn.execute(
                "SELECT version FROM vts_mapping_change_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_version = row["version"] if row else None
            return self._write_mapping_locked(
                mapping=mapping,
                version=version,
                prev_version=prev_version,
                changed_by=changed_by,
            )

    def load_mapping(self, version: str | None = None) -> VTSMappingVersion:
        """按版本加载映射表。

        Args:
            version: 版本号。None 时加载最新版本。

        Returns:
            VTSMappingVersion：映射表版本信息。

        Raises:
            RuntimeError: 映射表不存在或加载失败。
        """
        with self._lock:
            if version is None:
                # 加载最新版本
                row = self._conn.execute(
                    "SELECT version FROM vts_mapping_change_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if not row:
                    raise RuntimeError(f"{VTS_MAPPING_LOAD_FAILED}: 映射表为空")
                version = row["version"]

            rows = self._conn.execute(
                "SELECT emotion_tag, expression_id, created_ts_ms FROM vts_expression_mapping WHERE version = ?",
                (version,),
            ).fetchall()
            if not rows:
                raise RuntimeError(f"{VTS_MAPPING_LOAD_FAILED}: 版本 {version!r} 不存在")

            mapping = {r["emotion_tag"]: r["expression_id"] for r in rows}
            content_hash = self._compute_hash(mapping)
            created_ts_ms = rows[0]["created_ts_ms"]

            return VTSMappingVersion(
                version=version,
                mapping=mapping,
                content_hash=content_hash,
                created_ts_ms=created_ts_ms,
            )

    def get_expression_id(self, emotion_tag: str, *, version: str | None = None) -> str:
        """查询 emotion_tag 对应的 expression_id。

        Args:
            emotion_tag: 情绪标签（大小写不敏感）。
            version: 版本号。None 时使用最新版本。

        Returns:
            expression_id 字符串。

        Raises:
            ValueError: emotion_tag 非法（不在映射表中），reason_code=AVATAR_INVALID_EMOTION_TAG。
        """
        normalized = str(emotion_tag or "").strip().upper()
        if not normalized:
            raise ValueError(f"{AVATAR_INVALID_EMOTION_TAG}: emotion_tag 不能为空")

        try:
            mapping_version = self.load_mapping(version=version)
        except RuntimeError as exc:
            raise ValueError(f"{VTS_MAPPING_LOAD_FAILED}: {exc}") from exc

        expression_id = mapping_version.mapping.get(normalized)
        if not expression_id:
            raise ValueError(
                f"{AVATAR_INVALID_EMOTION_TAG}: emotion_tag '{normalized}' 不在映射表中 "
                f"(version={mapping_version.version}, "
                f"valid_tags={sorted(mapping_version.mapping.keys())})"
            )
        return expression_id

    def list_versions(self) -> list[dict[str, Any]]:
        """列出所有版本的变更日志。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, prev_version, prev_hash, new_hash, changed_by, ts_ms "
                "FROM vts_mapping_change_log ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        """关闭数据库连接。"""
        try:
            self._conn.close()
        except Exception:
            pass
