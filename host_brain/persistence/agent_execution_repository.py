"""Immutable durable result projection for governed internal Agent workers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from host_brain.core.ids import now_ms


AGENT_EXECUTION_SCHEMA_NOT_READY = "AGENT_EXECUTION_SCHEMA_NOT_READY"
AGENT_EXECUTION_RESULT_INVALID = "AGENT_EXECUTION_RESULT_INVALID"
AGENT_EXECUTION_RESULT_CONFLICT = "AGENT_EXECUTION_RESULT_CONFLICT"
AGENT_EXECUTION_RESULT_NOT_FOUND = "AGENT_EXECUTION_RESULT_NOT_FOUND"

AGENT_EXECUTION_MIGRATION_ASSET_KEY = "0064#agent_execution_result"
# Frozen to the catalog asset.  Update only when the unapplied migration asset
# is intentionally changed together with its contract tests.
AGENT_EXECUTION_MIGRATION_SHA256 = "3a34845837c82e7637fe9331945d56a11e58b212e51a75bd34dada446eb0d040"

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_ALLOWED_STATUSES = frozenset({"EXECUTED", "FAILED", "BLOCKED", "DEGRADED"})
_REFERENCE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,475}$"
)
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_REQUIRED_COLUMNS = frozenset(
    {
        "receipt_ref",
        "command_id",
        "work_order_id",
        "result_status",
        "result_json",
        "result_digest",
        "created_at_ms",
    }
)


class AgentExecutionRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or AGENT_EXECUTION_RESULT_INVALID)
        self.detail = str(detail or "agent execution result persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


class AgentExecutionResultRepository:
    """One append-only result per admitted command and WorkOrder."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "AgentExecutionResultRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_SCHEMA_NOT_READY,
                f"agent execution database does not exist: {self.db_path}",
            )
        try:
            conn = self._connect()
            try:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (AGENT_EXECUTION_MIGRATION_ASSET_KEY,),
                ).fetchone()
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(agent_execution_result)"
                    ).fetchall()
                }
                foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_SCHEMA_NOT_READY,
                "agent execution schema cannot be verified",
            ) from exc
        if (
            asset is None
            or str(asset["checksum"]) != AGENT_EXECUTION_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or not _REQUIRED_COLUMNS <= columns
            or foreign_keys != 1
        ):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_SCHEMA_NOT_READY,
                "agent execution migration 0064 is missing or drifted",
            )

    def append(
        self,
        *,
        command_id: str,
        work_order_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        command_ref = _required_text(command_id, field_name="command_id", limit=512)
        work_ref = _required_text(work_order_id, field_name="work_order_id", limit=240)
        safe_result = _safe_value(dict(result or {}), path="result")
        if not isinstance(safe_result, dict):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "result must be a mapping",
            )
        status = str(safe_result.get("status") or "").strip().upper()
        if status not in _ALLOWED_STATUSES:
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                f"unsupported result status: {status or 'missing'}",
            )
        result_json = _canonical_json(safe_result)
        if len(result_json.encode("utf-8")) > 16_777_216:
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "result exceeds the durable size limit",
            )
        result_digest = _sha256(result_json)
        receipt_ref = _receipt_ref(command_ref, result_digest)
        created_at_ms = int(self._clock())
        if created_at_ms < 0:
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "clock must be non-negative",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM agent_execution_result
                WHERE command_id = ? OR work_order_id = ? OR receipt_ref = ?
                ORDER BY created_at_ms, receipt_ref
                LIMIT 1
                """,
                (command_ref, work_ref, receipt_ref),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                expected = {
                    "command_id": command_ref,
                    "work_order_id": work_ref,
                    "receipt_ref": receipt_ref,
                    "result_status": status,
                    "result_digest": result_digest,
                    "result": safe_result,
                }
                if any(record[key] != value for key, value in expected.items()):
                    raise AgentExecutionRepositoryError(
                        AGENT_EXECUTION_RESULT_CONFLICT,
                        "command or WorkOrder already owns a different result",
                    )
                conn.commit()
                return {**record, "created": False, "replayed": True}
            conn.execute(
                """
                INSERT INTO agent_execution_result(
                    receipt_ref, command_id, work_order_id, result_status,
                    result_json, result_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_ref,
                    command_ref,
                    work_ref,
                    status,
                    result_json,
                    result_digest,
                    created_at_ms,
                ),
            )
            row = conn.execute(
                "SELECT * FROM agent_execution_result WHERE receipt_ref = ?",
                (receipt_ref,),
            ).fetchone()
            conn.commit()
            if row is None:
                raise AgentExecutionRepositoryError(
                    AGENT_EXECUTION_RESULT_NOT_FOUND,
                    "committed result cannot be read back",
                )
            return {**self._record(row), "created": True, "replayed": False}
        except AgentExecutionRepositoryError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_CONFLICT,
                "agent execution result identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, receipt_ref: str) -> dict[str, Any] | None:
        receipt = _required_reference(receipt_ref, field_name="receipt_ref")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM agent_execution_result WHERE receipt_ref = ?",
                (receipt,),
            ).fetchone()
            return None if row is None else self._record(row)
        finally:
            conn.close()

    def get_by_command(self, command_id: str) -> dict[str, Any] | None:
        command_ref = _required_text(command_id, field_name="command_id", limit=512)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM agent_execution_result WHERE command_id = ?",
                (command_ref,),
            ).fetchone()
            return None if row is None else self._record(row)
        finally:
            conn.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        try:
            result = json.loads(str(record["result_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "durable result JSON is invalid",
            ) from exc
        canonical = _canonical_json(result)
        if _sha256(canonical) != str(record["result_digest"]):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "durable result digest mismatch",
            )
        expected_receipt = _receipt_ref(
            str(record["command_id"]),
            str(record["result_digest"]),
        )
        if expected_receipt != str(record["receipt_ref"]):
            raise AgentExecutionRepositoryError(
                AGENT_EXECUTION_RESULT_INVALID,
                "durable result receipt binding drifted",
            )
        return {**record, "result": result}


def _safe_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if _FORBIDDEN_KEY.search(key) and not key.lower().endswith("_ref"):
                raise AgentExecutionRepositoryError(
                    AGENT_EXECUTION_RESULT_INVALID,
                    f"secret-like result field is forbidden: {path}.{key}",
                )
            safe[key] = _safe_value(raw_value, path=f"{path}.{key}")
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, path=f"{path}[]") for item in value[:2_048]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:128_000]
    return str(value)[:8_000]


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            deepcopy(dict(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AgentExecutionRepositoryError(
            AGENT_EXECUTION_RESULT_INVALID,
            "result is not finite canonical JSON",
        ) from exc


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt_ref(command_id: str, result_digest: str) -> str:
    digest = hashlib.sha256(
        f"{command_id}\n{result_digest}".encode("utf-8")
    ).hexdigest()
    return f"agentresult://sha256/{digest}"


def _required_text(value: Any, *, field_name: str, limit: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise AgentExecutionRepositoryError(
            AGENT_EXECUTION_RESULT_INVALID,
            f"{field_name} is missing or too long",
        )
    return normalized


def _required_reference(value: Any, *, field_name: str) -> str:
    normalized = _required_text(value, field_name=field_name, limit=512)
    if not _REFERENCE.fullmatch(normalized):
        raise AgentExecutionRepositoryError(
            AGENT_EXECUTION_RESULT_INVALID,
            f"{field_name} is not a durable reference",
        )
    return normalized


__all__ = [
    "AGENT_EXECUTION_MIGRATION_ASSET_KEY",
    "AGENT_EXECUTION_MIGRATION_SHA256",
    "AGENT_EXECUTION_RESULT_CONFLICT",
    "AGENT_EXECUTION_RESULT_INVALID",
    "AGENT_EXECUTION_SCHEMA_NOT_READY",
    "AgentExecutionRepositoryError",
    "AgentExecutionResultRepository",
]
