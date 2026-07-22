"""Append-only authority for signed terminal reconciliation commands."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


TERMINAL_OPERATOR_COMMAND_INVALID = "TERMINAL_OPERATOR_COMMAND_INVALID"
TERMINAL_OPERATOR_COMMAND_CONFLICT = "TERMINAL_OPERATOR_COMMAND_CONFLICT"
TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY = (
    "TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY"
)
_SCHEMA_VERSION = "Natsume.task_runtime.terminal_operator_command.v1"
_REF_PREFIX = "terminal-command://sha256/"
_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_FIELDS = (
    "operator_command_id",
    "case_id",
    "expected_case_snapshot_sha256",
    "expected_subject_snapshot_sha256",
    "action",
    "target_graph_status",
    "target_budget_status",
    "reconciler_actor_id",
    "issued_ts_ms",
    "attestation_ref",
    "attestation_key_id",
    "attestation_signature",
)
_COLUMNS = frozenset(
    {
        "bundle_ref",
        "schema_version",
        "request_id",
        "principal_id",
        "case_id",
        "command_json",
        "command_sha256",
        "ingress_claim_ref",
        "ingress_audit_ref",
        "ingress_body_sha256",
        "staged_at_ms",
    }
)


class TerminalOperatorCommandStoreError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TERMINAL_OPERATOR_COMMAND_INVALID)
        self.detail = str(detail or "terminal operator command store failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class TerminalOperatorCommandStore:
    """Persist signed input separately from generic durable task input."""

    def __init__(self, db_path: str | Path) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_INVALID,
                "a file-backed canonical database is required",
            )
        self.db_path = str(Path(raw).resolve())
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        try:
            with self._connect() as conn:
                migration = conn.execute(
                    "SELECT name FROM schema_migrations WHERE migration_id = 119"
                ).fetchone()
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(task_terminal_operator_signed_command)"
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' AND tbl_name="
                        "'task_terminal_operator_signed_command'"
                    ).fetchall()
                }
        except sqlite3.Error as exc:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY,
                "signed command schema cannot be inspected",
            ) from exc
        if (
            migration is None
            or str(migration[0]) != "terminal_operator_signed_command"
            or columns != _COLUMNS
            or triggers
            != {
                "task_terminal_operator_signed_command_no_update",
                "task_terminal_operator_signed_command_no_delete",
            }
        ):
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY,
                "migration 0119 or immutable signed command schema is unavailable",
            )

    def stage(
        self,
        *,
        request_id: str,
        principal_id: str,
        command: Mapping[str, Any],
        ingress_claim_ref: str,
        ingress_audit_ref: str,
        ingress_body_sha256: str,
        staged_at_ms: int,
    ) -> dict[str, Any]:
        request = _required(request_id, "request_id")
        principal = _required(principal_id, "principal_id")
        claim_ref = _required(ingress_claim_ref, "ingress_claim_ref")
        audit_ref = _required(ingress_audit_ref, "ingress_audit_ref")
        body_digest = _sha256_ref(ingress_body_sha256, "ingress_body_sha256")
        normalized = _normalize_command(command)
        command_json = _canonical_json(normalized)
        command_sha256 = _digest_ref(command_json.encode("utf-8"))
        binding = {
            "schema_version": _SCHEMA_VERSION,
            "request_id": request,
            "principal_id": principal,
            "command_sha256": command_sha256,
            "ingress_claim_ref": claim_ref,
            "ingress_audit_ref": audit_ref,
            "ingress_body_sha256": body_digest,
        }
        bundle_ref = _REF_PREFIX + hashlib.sha256(
            _canonical_json(binding).encode("utf-8")
        ).hexdigest()
        try:
            staged = int(staged_at_ms)
        except (TypeError, ValueError) as exc:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_INVALID,
                "staged_at_ms is invalid",
            ) from exc
        if staged < 0:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_INVALID,
                "staged_at_ms must be non-negative",
            )
        row = {
            **binding,
            "bundle_ref": bundle_ref,
            "case_id": normalized["case_id"],
            "command_json": command_json,
            "staged_at_ms": staged,
        }
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = self._read_by_request(conn, request)
                if existing is None:
                    conn.execute(
                        "INSERT INTO task_terminal_operator_signed_command("
                        "bundle_ref, schema_version, request_id, principal_id, "
                        "case_id, command_json, command_sha256, ingress_claim_ref, "
                        "ingress_audit_ref, ingress_body_sha256, staged_at_ms"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        tuple(row[key] for key in _INSERT_COLUMNS),
                    )
                    conn.commit()
                    return {**self._validate_row(row), "replayed": False}
                conn.commit()
        except sqlite3.Error as exc:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_CONFLICT,
                "signed command could not be staged",
            ) from exc
        reopened = self._validate_row(existing)
        if any(reopened.get(key) != row.get(key) for key in _REPLAY_BOUND_COLUMNS):
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_CONFLICT,
                "request_id is already bound to a different signed command",
            )
        return {**reopened, "replayed": True}

    def read(self, bundle_ref: str) -> dict[str, Any]:
        ref = _bundle_ref(bundle_ref)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT " + ",".join(_INSERT_COLUMNS) + " "
                    "FROM task_terminal_operator_signed_command WHERE bundle_ref=?",
                    (ref,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY,
                "signed command could not be reopened",
            ) from exc
        if row is None:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_INVALID,
                "signed command reference does not exist",
            )
        return self._validate_row(dict(zip(_INSERT_COLUMNS, row, strict=True)))

    def _read_by_request(
        self, conn: sqlite3.Connection, request_id: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT " + ",".join(_INSERT_COLUMNS) + " "
            "FROM task_terminal_operator_signed_command WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return None if row is None else dict(zip(_INSERT_COLUMNS, row, strict=True))

    def _validate_row(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        row = {key: raw.get(key) for key in _INSERT_COLUMNS}
        command = _normalize_command(_load_object(row["command_json"]))
        command_json = _canonical_json(command)
        command_sha256 = _digest_ref(command_json.encode("utf-8"))
        binding = {
            "schema_version": _SCHEMA_VERSION,
            "request_id": _required(row["request_id"], "request_id"),
            "principal_id": _required(row["principal_id"], "principal_id"),
            "command_sha256": _sha256_ref(
                row["command_sha256"], "command_sha256"
            ),
            "ingress_claim_ref": _required(
                row["ingress_claim_ref"], "ingress_claim_ref"
            ),
            "ingress_audit_ref": _required(
                row["ingress_audit_ref"], "ingress_audit_ref"
            ),
            "ingress_body_sha256": _sha256_ref(
                row["ingress_body_sha256"], "ingress_body_sha256"
            ),
        }
        expected_ref = _REF_PREFIX + hashlib.sha256(
            _canonical_json(binding).encode("utf-8")
        ).hexdigest()
        if (
            row.get("schema_version") != _SCHEMA_VERSION
            or command_sha256 != binding["command_sha256"]
            or _required(row["case_id"], "case_id") != command["case_id"]
            or _bundle_ref(row["bundle_ref"]) != expected_ref
        ):
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_CONFLICT,
                "signed command durable binding drifted",
            )
        try:
            staged_at_ms = int(row["staged_at_ms"])
        except (TypeError, ValueError) as exc:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_CONFLICT,
                "signed command staged timestamp drifted",
            ) from exc
        if staged_at_ms < 0:
            raise TerminalOperatorCommandStoreError(
                TERMINAL_OPERATOR_COMMAND_CONFLICT,
                "signed command staged timestamp drifted",
            )
        return {
            **binding,
            "bundle_ref": expected_ref,
            "case_id": command["case_id"],
            "command": command,
            "command_json": command_json,
            "staged_at_ms": staged_at_ms,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


_INSERT_COLUMNS = (
    "bundle_ref",
    "schema_version",
    "request_id",
    "principal_id",
    "case_id",
    "command_json",
    "command_sha256",
    "ingress_claim_ref",
    "ingress_audit_ref",
    "ingress_body_sha256",
    "staged_at_ms",
)
_REPLAY_BOUND_COLUMNS = tuple(
    key for key in _INSERT_COLUMNS if key != "staged_at_ms"
)


def _normalize_command(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            "signed command must be an object",
        )
    unknown = sorted(set(raw).difference(_COMMAND_FIELDS))
    if unknown:
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            "signed command contains unknown fields: " + ",".join(unknown),
        )
    result: dict[str, Any] = {}
    for field in _COMMAND_FIELDS:
        if field in {"target_graph_status", "target_budget_status"}:
            value = str(raw.get(field) or "").strip().upper()
            result[field] = value or None
        elif field == "issued_ts_ms":
            try:
                value = int(raw.get(field))
            except (TypeError, ValueError) as exc:
                raise TerminalOperatorCommandStoreError(
                    TERMINAL_OPERATOR_COMMAND_INVALID,
                    "issued_ts_ms is invalid",
                ) from exc
            if isinstance(raw.get(field), bool) or value < 0:
                raise TerminalOperatorCommandStoreError(
                    TERMINAL_OPERATOR_COMMAND_INVALID,
                    "issued_ts_ms is invalid",
                )
            result[field] = value
        else:
            value = _required(raw.get(field), field)
            result[field] = value.upper() if field == "action" else value
    if result["operator_command_id"] != "task_terminal.reconcile":
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            "signed command ID is invalid",
        )
    return result


def _required(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            f"{field_name} is required",
        )
    return text


def _sha256_ref(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if _HEX_SHA256.fullmatch(text):
        text = "sha256:" + text
    if not _SHA256_REF.fullmatch(text):
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            f"{field_name} must be a canonical sha256 reference",
        )
    return text


def _bundle_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(_REF_PREFIX) or not _HEX_SHA256.fullmatch(
        text[len(_REF_PREFIX) :]
    ):
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            "signed command reference is invalid",
        )
    return text


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_INVALID,
            "signed command is not canonical JSON",
        ) from exc


def _load_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_CONFLICT,
            "signed command JSON is invalid",
        ) from exc
    if not isinstance(loaded, dict):
        raise TerminalOperatorCommandStoreError(
            TERMINAL_OPERATOR_COMMAND_CONFLICT,
            "signed command JSON is not an object",
        )
    return loaded


def _digest_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


__all__ = [
    "TERMINAL_OPERATOR_COMMAND_CONFLICT",
    "TERMINAL_OPERATOR_COMMAND_INVALID",
    "TERMINAL_OPERATOR_COMMAND_SCHEMA_NOT_READY",
    "TerminalOperatorCommandStore",
    "TerminalOperatorCommandStoreError",
]
