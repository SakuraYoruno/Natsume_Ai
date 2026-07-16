"""Durable workspace ownership, lease and cleanup authority.

This repository deliberately persists only a logical ``managed_root_ref`` and
the workspace id used as its relative segment.  It never persists or consumes
an absolute filesystem path; physical roots belong to process composition.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
from typing import Any, TypeVar

from host_brain.core.ids import now_ms


WORKSPACE_AUTHORITY_INVALID = "WORKSPACE_AUTHORITY_INVALID"
WORKSPACE_AUTHORITY_NOT_FOUND = "WORKSPACE_AUTHORITY_NOT_FOUND"
WORKSPACE_AUTHORITY_CONFLICT = "WORKSPACE_AUTHORITY_CONFLICT"
WORKSPACE_OWNERSHIP_CONFLICT = "WORKSPACE_OWNERSHIP_CONFLICT"
WORKSPACE_LEASE_CONFLICT = "WORKSPACE_LEASE_CONFLICT"
WORKSPACE_STALE_FENCE = "WORKSPACE_STALE_FENCE"
WORKSPACE_SCHEMA_NOT_READY = "WORKSPACE_SCHEMA_NOT_READY"
WORKSPACE_DURABLE_DATA_INVALID = "WORKSPACE_DURABLE_DATA_INVALID"

WORKSPACE_AUTHORITY_SCHEMA_VERSION = "Natsume.agent.workspace_authority.v1"
WORKSPACE_AUTHORITY_MIGRATION_ASSET_KEY = "0101#agent_workspace_authority"
# Frozen after the 0101 asset is finalized.  A test binds this constant to the
# catalog bytes so an edited migration cannot silently become runtime truth.
WORKSPACE_AUTHORITY_MIGRATION_SHA256 = (
    "eda5e495b5e9b582e0c7e43c35537de36586697daaf9bce9e1511caa01e4137a"
)

_TABLE = "agent_workspace_authority"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_ISOLATION_MODES = frozenset({"DIRECTORY_FENCE", "CONTAINER", "MICROVM"})
_LIVE_STATUSES = frozenset({"ACTIVE", "CLEANUP_PENDING"})
_TERMINAL_STATUSES = frozenset({"CLEANED", "QUARANTINED"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$)|"
    r"(?:COM|LPT)[1-9])(?:\..*)?$",
    flags=re.IGNORECASE,
)
_MAX_LEASE_MS = 86_400_000
_REQUIRED_COLUMN_METADATA = {
    "workspace_id": ("TEXT", 1, None, 1),
    "schema_version": ("TEXT", 1, None, 0),
    "root_work_order_id": ("TEXT", 1, None, 0),
    "graph_id": ("TEXT", 1, None, 0),
    "graph_revision": ("INTEGER", 1, None, 0),
    "owner_principal_id": ("TEXT", 1, None, 0),
    "managed_root_ref": ("TEXT", 1, None, 0),
    "workspace_relpath": ("TEXT", 1, None, 0),
    "source_ref": ("TEXT", 1, None, 0),
    "isolation_mode": ("TEXT", 1, None, 0),
    "access_mode": ("TEXT", 1, None, 0),
    "allowed_paths_json": ("TEXT", 1, None, 0),
    "allowed_paths_digest": ("TEXT", 1, None, 0),
    "status": ("TEXT", 1, None, 0),
    "status_version": ("INTEGER", 1, "0", 0),
    "lease_owner": ("TEXT", 0, None, 0),
    "lease_token_digest": ("TEXT", 0, None, 0),
    "lease_fencing_token": ("INTEGER", 1, "0", 0),
    "lease_expires_ts_ms": ("INTEGER", 0, None, 0),
    "cleanup_receipt_ref": ("TEXT", 0, None, 0),
    "cleanup_digest": ("TEXT", 0, None, 0),
    "created_ts_ms": ("INTEGER", 1, None, 0),
    "updated_ts_ms": ("INTEGER", 1, None, 0),
}
_REQUIRED_INDEXES = {
    "sqlite_autoindex_agent_workspace_authority_1": (
        ("workspace_id",),
        True,
        "pk",
    ),
    "sqlite_autoindex_agent_workspace_authority_2": (
        ("root_work_order_id", "graph_revision"),
        True,
        "u",
    ),
    "sqlite_autoindex_agent_workspace_authority_3": (
        ("graph_id", "graph_revision"),
        True,
        "u",
    ),
    "sqlite_autoindex_agent_workspace_authority_4": (
        ("managed_root_ref", "workspace_relpath"),
        True,
        "u",
    ),
    "idx_agent_workspace_authority_owner_status": (
        ("owner_principal_id", "status", "updated_ts_ms", "workspace_id"),
        False,
        "c",
    ),
    "idx_agent_workspace_authority_lease_expiry": (
        ("status", "lease_expires_ts_ms", "workspace_id"),
        False,
        "c",
    ),
}
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_agent_workspace_authority_immutable_update",
        "trg_agent_workspace_authority_delete_guard",
        "trg_agent_workspace_authority_version_guard",
        "trg_agent_workspace_authority_fence_guard",
        "trg_agent_workspace_authority_status_guard",
        "trg_agent_workspace_authority_cleanup_immutable",
    }
)
_GRAPH_BINDING_INDEX = (
    "uq_task_runtime_graph_workspace_binding",
    ("graph_id", "revision", "root_work_order_id"),
    True,
    "c",
)
_REQUIRED_FOREIGN_KEYS = (
    (
        0,
        0,
        "task_runtime_graph",
        "graph_id",
        "graph_id",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        0,
        1,
        "task_runtime_graph",
        "graph_revision",
        "revision",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        0,
        2,
        "task_runtime_graph",
        "root_work_order_id",
        "root_work_order_id",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
)
_TABLE_SQL_SHA256 = "21c97b83df4cb3479561eb3fa90fb0ee1b76c65cdb9bc207fdbfbde98a69fa23"
_TRIGGER_SQL_SHA256 = {
    "trg_agent_workspace_authority_cleanup_immutable": (
        "2629aadb0c2b8d67451c330c3306ecb13cff27bf6c584729acd91d11c2edb6c8"
    ),
    "trg_agent_workspace_authority_delete_guard": (
        "9e3bbeda01c36ddcfa24c5fdda88f79a88e9579b7c551313c3670b8ec5e95864"
    ),
    "trg_agent_workspace_authority_fence_guard": (
        "0a4f1e7389f683df5649506fbe60ffb38aa02ec4ea8f2906f8911b66e2e03b63"
    ),
    "trg_agent_workspace_authority_immutable_update": (
        "3ec290153b972df080770fdf2a2e67ba474462802ec46e03b2b70d32c7711311"
    ),
    "trg_agent_workspace_authority_status_guard": (
        "8f05d67a5daa8524498ee21aa3daa668e972f9983bb12b41a59c8eee9882ec1d"
    ),
    "trg_agent_workspace_authority_version_guard": (
        "26c30d959ed16e375f403872cd717c14e9fa4e5782927c2f21051fbdeee02f60"
    ),
}

T = TypeVar("T")


class WorkspaceRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or WORKSPACE_AUTHORITY_INVALID)
        self.detail = str(detail or "workspace authority operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class WorkspaceAuthority:
    workspace_id: str
    root_work_order_id: str
    graph_id: str
    graph_revision: int
    owner_principal_id: str
    managed_root_ref: str
    workspace_relpath: str
    source_ref: str
    isolation_mode: str
    allowed_paths: tuple[str, ...]
    allowed_paths_digest: str
    status: str
    status_version: int
    lease_owner: str | None
    lease_fencing_token: int
    lease_expires_ts_ms: int | None
    cleanup_receipt_ref: str | None
    cleanup_digest: str | None
    created_ts_ms: int
    updated_ts_ms: int


@dataclass(frozen=True, slots=True)
class WorkspaceMutation:
    authority: WorkspaceAuthority
    created: bool = False
    replayed: bool = False


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_sql_digest(value: Any) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be non-empty text",
        )
    normalized = value.strip()
    if len(normalized) > maximum:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} exceeds {maximum} characters",
        )
    return normalized


def _logical_id(value: Any, *, field: str) -> str:
    normalized = _required_text(value, field=field, maximum=120)
    if not _ID.fullmatch(normalized) or _WINDOWS_DEVICE_NAME.fullmatch(normalized):
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be one portable path segment",
        )
    return normalized


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a positive integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a positive integer",
        ) from exc
    if normalized < 1:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a positive integer",
        )
    return normalized


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a non-negative integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"{field} must be a non-negative integer",
        )
    return normalized


def _lease_ms(value: Any) -> int:
    normalized = _positive_int(value, field="lease_ms")
    if normalized > _MAX_LEASE_MS:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            f"lease_ms cannot exceed {_MAX_LEASE_MS}",
        )
    return normalized


def _canonical_allowed_paths(values: Iterable[str]) -> tuple[tuple[str, ...], str, str]:
    if isinstance(values, (str, bytes)):
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            "allowed_paths must be an iterable of relative paths",
        )
    normalized: set[str] = set()
    for value in values:
        path = _required_text(value, field="allowed_path", maximum=512)
        if "\\" in path or "\x00" in path:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"allowed_path is not portable: {path}",
            )
        posix = PurePosixPath(path)
        windows = PureWindowsPath(path)
        portable_parts = tuple(part for part in path.split("/") if part)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or windows.root
            or ".." in posix.parts
            or any(":" in part for part in posix.parts)
            or (
                path != "."
                and any(
                    part.rstrip(" .") != part
                    or not part.rstrip(" .")
                    or _WINDOWS_DEVICE_NAME.fullmatch(part) is not None
                    for part in portable_parts
                )
            )
        ):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"allowed_path must stay workspace-relative: {path}",
            )
        canonical = posix.as_posix().rstrip("/") or "."
        if canonical.startswith("./"):
            canonical = canonical[2:] or "."
        normalized.add(canonical)
    if not normalized:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            "allowed_paths cannot be empty",
        )
    if "." in normalized and len(normalized) != 1:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            "allowed_paths cannot combine workspace root with narrower paths",
        )
    ordered = tuple(sorted(normalized))
    encoded = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return ordered, encoded, _digest(encoded)


class WorkspaceRepository:
    """Canonical W1 authority with transaction-scoped fencing checks."""

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
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "WorkspaceRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
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

    @contextmanager
    def _transaction(self, *, write: bool) -> Any:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "trusted clock must return a non-negative integer",
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "trusted clock must return a non-negative integer",
            ) from exc
        if normalized < 0:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "trusted clock must return a non-negative integer",
            )
        return normalized

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise WorkspaceRepositoryError(
                WORKSPACE_SCHEMA_NOT_READY,
                f"workspace authority database does not exist: {self.db_path}",
            )
        try:
            conn = self._connect()
            try:
                asset = conn.execute(
                    "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
                    (WORKSPACE_AUTHORITY_MIGRATION_ASSET_KEY,),
                ).fetchone()
                table_row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (_TABLE,),
                ).fetchone()
                columns = {
                    str(row[1]): (
                        str(row[2]).upper(),
                        int(row[3]),
                        row[4],
                        int(row[5]),
                    )
                    for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
                }
                indexes = {
                    str(row[1]): (bool(row[2]), str(row[3]), int(row[4]))
                    for row in conn.execute(f"PRAGMA index_list({_TABLE})").fetchall()
                }
                graph_indexes = {
                    str(row[1]): (bool(row[2]), str(row[3]), int(row[4]))
                    for row in conn.execute("PRAGMA index_list(task_runtime_graph)").fetchall()
                }
                index_columns: dict[str, tuple[str, ...]] = {}
                for name in {*indexes, *graph_indexes}:
                    index_columns[name] = tuple(
                        str(row[2])
                        for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
                    )
                triggers = {
                    str(row[0]): str(row[1])
                    for row in conn.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                        (_TABLE,),
                    ).fetchall()
                }
                foreign_keys = conn.execute(f"PRAGMA foreign_key_list({_TABLE})").fetchall()
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_SCHEMA_NOT_READY,
                "workspace authority schema cannot be verified",
            ) from exc

        invalid_indexes = sorted(
            name
            for name, (expected_columns, expected_unique, expected_origin) in _REQUIRED_INDEXES.items()
            if name not in indexes
            or indexes[name]
            != (expected_unique, expected_origin, 0)
            or index_columns.get(name) != expected_columns
        )
        graph_index_name, graph_index_columns, graph_unique, graph_origin = (
            _GRAPH_BINDING_INDEX
        )
        invalid_graph_index = (
            graph_indexes.get(graph_index_name) != (graph_unique, graph_origin, 0)
            or index_columns.get(graph_index_name) != graph_index_columns
        )
        fk_rows = tuple(
            (
                int(row[0]),
                int(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in foreign_keys
        )
        trigger_digests = {
            name: _normalized_sql_digest(sql) for name, sql in triggers.items()
        }
        if (
            asset is None
            or str(asset["checksum"]) != WORKSPACE_AUTHORITY_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or table_row is None
            or _normalized_sql_digest(table_row["sql"]) != _TABLE_SQL_SHA256
            or columns != _REQUIRED_COLUMN_METADATA
            or set(indexes) != set(_REQUIRED_INDEXES)
            or invalid_indexes
            or invalid_graph_index
            or set(triggers) != _REQUIRED_TRIGGERS
            or trigger_digests != _TRIGGER_SQL_SHA256
            or fk_rows != _REQUIRED_FOREIGN_KEYS
        ):
            raise WorkspaceRepositoryError(
                WORKSPACE_SCHEMA_NOT_READY,
                "workspace authority migration 0101 is missing or drifted",
            )

    def allocate(
        self,
        *,
        workspace_id: str,
        root_work_order_id: str,
        graph_id: str,
        graph_revision: int,
        owner_principal_id: str,
        managed_root_ref: str,
        source_ref: str,
        isolation_mode: str,
        allowed_paths: Iterable[str],
    ) -> WorkspaceMutation:
        workspace_ref = _logical_id(workspace_id, field="workspace_id")
        root_order_ref = _required_text(
            root_work_order_id,
            field="root_work_order_id",
            maximum=240,
        )
        graph_ref = _required_text(graph_id, field="graph_id", maximum=240)
        revision = _positive_int(graph_revision, field="graph_revision")
        owner = _required_text(
            owner_principal_id,
            field="owner_principal_id",
            maximum=240,
        )
        root_ref = _logical_id(managed_root_ref, field="managed_root_ref")
        source = _required_text(source_ref, field="source_ref", maximum=512)
        isolation = str(isolation_mode or "").strip().upper()
        if isolation not in _ISOLATION_MODES:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"unsupported isolation_mode: {isolation or 'missing'}",
            )
        paths, paths_json, paths_digest = _canonical_allowed_paths(allowed_paths)
        expected = {
            "workspace_id": workspace_ref,
            "root_work_order_id": root_order_ref,
            "graph_id": graph_ref,
            "graph_revision": revision,
            "owner_principal_id": owner,
            "managed_root_ref": root_ref,
            "workspace_relpath": workspace_ref,
            "source_ref": source,
            "isolation_mode": isolation,
            "allowed_paths": paths,
            "allowed_paths_digest": paths_digest,
        }
        try:
            with self._transaction(write=True) as conn:
                existing_rows = conn.execute(
                    f"""
                    SELECT * FROM {_TABLE}
                    WHERE workspace_id = ?
                       OR (root_work_order_id = ? AND graph_revision = ?)
                       OR (graph_id = ? AND graph_revision = ?)
                       OR (managed_root_ref = ? AND workspace_relpath = ?)
                    ORDER BY workspace_id
                    """,
                    (
                        workspace_ref,
                        root_order_ref,
                        revision,
                        graph_ref,
                        revision,
                        root_ref,
                        workspace_ref,
                    ),
                ).fetchall()
                if existing_rows:
                    if len(existing_rows) != 1:
                        raise WorkspaceRepositoryError(
                            WORKSPACE_AUTHORITY_CONFLICT,
                            "allocation identities resolve to multiple workspace authorities",
                        )
                    authority = self._authority(existing_rows[0])
                    if authority.owner_principal_id != owner:
                        raise WorkspaceRepositoryError(
                            WORKSPACE_OWNERSHIP_CONFLICT,
                            "a writable workspace cannot be shared across owners",
                        )
                    if any(getattr(authority, key) != value for key, value in expected.items()):
                        raise WorkspaceRepositoryError(
                            WORKSPACE_AUTHORITY_CONFLICT,
                            "workspace allocation replay does not match immutable authority",
                        )
                    return WorkspaceMutation(authority=authority, replayed=True)

                ts_ms = self._now()
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}(
                        workspace_id, schema_version, root_work_order_id,
                        graph_id, graph_revision, owner_principal_id,
                        managed_root_ref, workspace_relpath, source_ref,
                        isolation_mode, access_mode, allowed_paths_json,
                        allowed_paths_digest, status, status_version,
                        lease_owner, lease_token_digest, lease_fencing_token,
                        lease_expires_ts_ms, cleanup_receipt_ref,
                        cleanup_digest, created_ts_ms, updated_ts_ms
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'READ_WRITE', ?, ?,
                        'ALLOCATED', 0, NULL, NULL, 0, NULL, NULL, NULL, ?, ?
                    )
                    """,
                    (
                        workspace_ref,
                        WORKSPACE_AUTHORITY_SCHEMA_VERSION,
                        root_order_ref,
                        graph_ref,
                        revision,
                        owner,
                        root_ref,
                        workspace_ref,
                        source,
                        isolation,
                        paths_json,
                        paths_digest,
                        ts_ms,
                        ts_ms,
                    ),
                )
                authority = self._require(conn, workspace_ref)
                return WorkspaceMutation(authority=authority, created=True)
        except sqlite3.IntegrityError as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_CONFLICT,
                "workspace allocation violates its graph or ownership binding",
            ) from exc

    def acquire_lease(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_ms: int,
        expected_status_version: int | None = None,
    ) -> WorkspaceMutation:
        reference = _logical_id(workspace_id, field="workspace_id")
        owner = _required_text(lease_owner, field="lease_owner", maximum=240)
        token = _required_text(lease_token, field="lease_token", maximum=512)
        if len(token) < 32:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "lease_token must contain at least 32 characters",
            )
        duration = _lease_ms(lease_ms)
        token_digest = _digest(token)
        expected_version = (
            _nonnegative_int(
                expected_status_version,
                field="expected_status_version",
            )
            if expected_status_version is not None
            else None
        )
        with self._transaction(write=True) as conn:
            ts_ms = self._now()
            row = self._require_row(conn, reference)
            authority = self._authority(row)
            if (
                authority.status in _LIVE_STATUSES
                and authority.lease_owner == owner
                and str(row["lease_token_digest"] or "") == token_digest
                and authority.lease_expires_ts_ms is not None
                and authority.lease_expires_ts_ms > ts_ms
            ):
                return WorkspaceMutation(authority=authority, replayed=True)
            if authority.status in _TERMINAL_STATUSES:
                raise WorkspaceRepositoryError(
                    WORKSPACE_LEASE_CONFLICT,
                    f"workspace is terminal: {authority.status}",
                )
            if (
                authority.lease_owner is not None
                and authority.lease_expires_ts_ms is not None
                and authority.lease_expires_ts_ms > ts_ms
            ):
                raise WorkspaceRepositoryError(
                    WORKSPACE_LEASE_CONFLICT,
                    "workspace already has a live lease",
                )
            if (
                expected_version is not None
                and authority.status_version != expected_version
            ):
                raise WorkspaceRepositoryError(
                    WORKSPACE_STALE_FENCE,
                    "workspace status version is stale",
                )
            new_status = "ACTIVE" if authority.status == "ALLOCATED" else authority.status
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET status = ?, status_version = status_version + 1,
                    lease_owner = ?, lease_token_digest = ?,
                    lease_fencing_token = lease_fencing_token + 1,
                    lease_expires_ts_ms = ?, updated_ts_ms = ?
                WHERE workspace_id = ? AND status_version = ?
                """,
                (
                    new_status,
                    owner,
                    token_digest,
                    ts_ms + duration,
                    ts_ms,
                    reference,
                    authority.status_version,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkspaceRepositoryError(
                    WORKSPACE_STALE_FENCE,
                    "workspace lease acquisition lost its durable CAS",
                )
            return WorkspaceMutation(authority=self._require(conn, reference))

    def authorize_lease(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
    ) -> WorkspaceAuthority:
        reference, owner, token_digest, fence = self._lease_arguments(
            workspace_id,
            lease_owner,
            lease_token,
            lease_fencing_token,
        )
        with self._transaction(write=False) as conn:
            ts_ms = self._now()
            row = self._require_row(conn, reference)
            self._assert_live_lease(
                row,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                ts_ms=ts_ms,
            )
            return self._authority(row)

    def run_fenced(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        operation: Callable[[WorkspaceAuthority], T],
    ) -> T:
        """Run one filesystem adapter operation while holding the DB writer fence."""

        if not callable(operation):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "operation must be callable",
            )
        reference, owner, token_digest, fence = self._lease_arguments(
            workspace_id,
            lease_owner,
            lease_token,
            lease_fencing_token,
        )
        with self._transaction(write=True) as conn:
            ts_ms = self._now()
            row = self._require_row(conn, reference)
            self._assert_live_lease(
                row,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                ts_ms=ts_ms,
            )
            return operation(self._authority(row))

    def begin_cleanup(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        expected_status_version: int,
    ) -> WorkspaceAuthority:
        return self._fenced_transition(
            workspace_id,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_fencing_token=lease_fencing_token,
            expected_status_version=expected_status_version,
            expected_status="ACTIVE",
            sql="status = 'CLEANUP_PENDING'",
            values=(),
        )

    def record_cleanup(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        expected_status_version: int,
        cleanup_receipt_ref: str,
        cleanup_digest: str,
    ) -> WorkspaceAuthority:
        receipt = _required_text(
            cleanup_receipt_ref,
            field="cleanup_receipt_ref",
            maximum=512,
        )
        digest = str(cleanup_digest or "").strip()
        if not _DIGEST.fullmatch(digest):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "cleanup_digest must be a sha256 digest",
            )
        return self._fenced_transition(
            workspace_id,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_fencing_token=lease_fencing_token,
            expected_status_version=expected_status_version,
            expected_status="CLEANUP_PENDING",
            sql=(
                "status = 'CLEANED', lease_owner = NULL, "
                "lease_token_digest = NULL, lease_expires_ts_ms = NULL, "
                "cleanup_receipt_ref = ?, cleanup_digest = ?"
            ),
            values=(receipt, digest),
        )

    def get(self, workspace_id: str) -> WorkspaceAuthority:
        reference = _logical_id(workspace_id, field="workspace_id")
        with self._transaction(write=False) as conn:
            return self._require(conn, reference)

    def _fenced_transition(
        self,
        workspace_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        expected_status_version: int,
        expected_status: str,
        sql: str,
        values: tuple[Any, ...],
    ) -> WorkspaceAuthority:
        reference, owner, token_digest, fence = self._lease_arguments(
            workspace_id,
            lease_owner,
            lease_token,
            lease_fencing_token,
        )
        version = _nonnegative_int(
            expected_status_version,
            field="expected_status_version",
        )
        with self._transaction(write=True) as conn:
            ts_ms = self._now()
            row = self._require_row(conn, reference)
            self._assert_live_lease(
                row,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                ts_ms=ts_ms,
            )
            authority = self._authority(row)
            if authority.status_version != version:
                raise WorkspaceRepositoryError(
                    WORKSPACE_STALE_FENCE,
                    "workspace status version is stale",
                )
            if authority.status != expected_status:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_CONFLICT,
                    f"workspace status must be {expected_status}",
                )
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET {sql}, status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE workspace_id = ? AND status_version = ?
                  AND lease_owner = ? AND lease_token_digest = ?
                  AND lease_fencing_token = ?
                """,
                (*values, ts_ms, reference, version, owner, token_digest, fence),
            )
            if cursor.rowcount != 1:
                raise WorkspaceRepositoryError(
                    WORKSPACE_STALE_FENCE,
                    "workspace transition lost its durable fence",
                )
            return self._require(conn, reference)

    def _lease_arguments(
        self,
        workspace_id: str,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
    ) -> tuple[str, str, str, int]:
        reference = _logical_id(workspace_id, field="workspace_id")
        owner = _required_text(lease_owner, field="lease_owner", maximum=240)
        token = _required_text(lease_token, field="lease_token", maximum=512)
        fence = _positive_int(lease_fencing_token, field="lease_fencing_token")
        return reference, owner, _digest(token), fence

    @staticmethod
    def _assert_live_lease(
        row: sqlite3.Row,
        *,
        owner: str,
        token_digest: str,
        fence: int,
        ts_ms: int,
    ) -> None:
        if (
            str(row["status"]) not in _LIVE_STATUSES
            or str(row["lease_owner"] or "") != owner
            or str(row["lease_token_digest"] or "") != token_digest
            or int(row["lease_fencing_token"]) != fence
            or row["lease_expires_ts_ms"] is None
            or int(row["lease_expires_ts_ms"]) <= ts_ms
        ):
            raise WorkspaceRepositoryError(
                WORKSPACE_STALE_FENCE,
                "workspace lease is absent, expired, or fenced by another owner",
            )

    @staticmethod
    def _authority(row: sqlite3.Row) -> WorkspaceAuthority:
        try:
            def exact_text(field: str, maximum: int) -> str:
                raw = row[field]
                normalized = _required_text(raw, field=field, maximum=maximum)
                if raw != normalized:
                    raise ValueError(f"{field} is not canonical")
                return normalized

            def exact_int(field: str, minimum: int) -> int:
                raw = row[field]
                if type(raw) is not int or raw < minimum:
                    raise ValueError(f"{field} is invalid")
                return raw

            workspace_id = _logical_id(row["workspace_id"], field="workspace_id")
            if row["workspace_id"] != workspace_id:
                raise ValueError("workspace_id is not canonical")
            root_work_order_id = exact_text("root_work_order_id", 240)
            graph_id = exact_text("graph_id", 240)
            graph_revision = exact_int("graph_revision", 1)
            owner_principal_id = exact_text("owner_principal_id", 240)
            managed_root_ref = _logical_id(
                row["managed_root_ref"],
                field="managed_root_ref",
            )
            if row["managed_root_ref"] != managed_root_ref:
                raise ValueError("managed_root_ref is not canonical")
            workspace_relpath = _logical_id(
                row["workspace_relpath"],
                field="workspace_relpath",
            )
            if row["workspace_relpath"] != workspace_relpath:
                raise ValueError("workspace_relpath is not canonical")
            if workspace_relpath != workspace_id:
                raise ValueError("workspace_relpath does not match workspace_id")
            source_ref = exact_text("source_ref", 512)
            if str(row["schema_version"]) != WORKSPACE_AUTHORITY_SCHEMA_VERSION:
                raise ValueError("workspace schema version is invalid")
            isolation_mode = str(row["isolation_mode"])
            if isolation_mode not in _ISOLATION_MODES:
                raise ValueError("workspace isolation mode is invalid")
            if str(row["access_mode"]) != "READ_WRITE":
                raise ValueError("workspace access mode is invalid")

            raw_paths_json = row["allowed_paths_json"]
            if not isinstance(raw_paths_json, str):
                raise ValueError("allowed paths JSON must be text")
            decoded_paths = json.loads(raw_paths_json)
            if not isinstance(decoded_paths, list):
                raise ValueError("allowed paths must be a JSON array")
            paths, canonical_paths_json, paths_digest = _canonical_allowed_paths(
                decoded_paths
            )
            if raw_paths_json != canonical_paths_json:
                raise ValueError("allowed paths JSON is not canonical")
            stored_paths_digest = str(row["allowed_paths_digest"] or "")
            if (
                not _DIGEST.fullmatch(stored_paths_digest)
                or stored_paths_digest != paths_digest
            ):
                raise ValueError("allowed paths digest does not match canonical JSON")

            status = str(row["status"])
            if status not in {
                "ALLOCATED",
                "ACTIVE",
                "CLEANUP_PENDING",
                "CLEANED",
                "QUARANTINED",
            }:
                raise ValueError("workspace status is invalid")
            status_version = exact_int("status_version", 0)
            lease_fencing_token = exact_int("lease_fencing_token", 0)
            created_ts_ms = exact_int("created_ts_ms", 0)
            updated_ts_ms = exact_int("updated_ts_ms", created_ts_ms)

            lease_owner_raw = row["lease_owner"]
            lease_digest_raw = row["lease_token_digest"]
            lease_expiry_raw = row["lease_expires_ts_ms"]
            if lease_owner_raw is None:
                if lease_digest_raw is not None or lease_expiry_raw is not None:
                    raise ValueError("workspace lease tuple is incomplete")
                lease_owner = None
                lease_expires_ts_ms = None
            else:
                lease_owner = exact_text("lease_owner", 240)
                if not isinstance(lease_digest_raw, str) or not _DIGEST.fullmatch(
                    lease_digest_raw
                ):
                    raise ValueError("workspace lease token digest is invalid")
                lease_expires_ts_ms = exact_int("lease_expires_ts_ms", 0)
                if lease_fencing_token < 1:
                    raise ValueError("live workspace lease has no fence")
            if status in _LIVE_STATUSES and lease_owner is None:
                raise ValueError("live workspace status has no lease")
            if status not in _LIVE_STATUSES and lease_owner is not None:
                raise ValueError("non-live workspace status retains a lease")

            cleanup_receipt_raw = row["cleanup_receipt_ref"]
            cleanup_digest_raw = row["cleanup_digest"]
            if status == "CLEANED":
                cleanup_receipt_ref = exact_text("cleanup_receipt_ref", 512)
                if not isinstance(cleanup_digest_raw, str) or not _DIGEST.fullmatch(
                    cleanup_digest_raw
                ):
                    raise ValueError("workspace cleanup digest is invalid")
                cleanup_digest = cleanup_digest_raw
            else:
                if cleanup_receipt_raw is not None or cleanup_digest_raw is not None:
                    raise ValueError("non-cleaned workspace has cleanup evidence")
                cleanup_receipt_ref = None
                cleanup_digest = None

            return WorkspaceAuthority(
                workspace_id=workspace_id,
                root_work_order_id=root_work_order_id,
                graph_id=graph_id,
                graph_revision=graph_revision,
                owner_principal_id=owner_principal_id,
                managed_root_ref=managed_root_ref,
                workspace_relpath=workspace_relpath,
                source_ref=source_ref,
                isolation_mode=isolation_mode,
                allowed_paths=paths,
                allowed_paths_digest=stored_paths_digest,
                status=status,
                status_version=status_version,
                lease_owner=lease_owner,
                lease_fencing_token=lease_fencing_token,
                lease_expires_ts_ms=lease_expires_ts_ms,
                cleanup_receipt_ref=cleanup_receipt_ref,
                cleanup_digest=cleanup_digest,
                created_ts_ms=created_ts_ms,
                updated_ts_ms=updated_ts_ms,
            )
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            WorkspaceRepositoryError,
        ) as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_DURABLE_DATA_INVALID,
                "workspace authority row is corrupt",
            ) from exc

    @classmethod
    def _require(cls, conn: sqlite3.Connection, workspace_id: str) -> WorkspaceAuthority:
        return cls._authority(cls._require_row(conn, workspace_id))

    @staticmethod
    def _require_row(conn: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
        row = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_NOT_FOUND,
                f"workspace authority not found: {workspace_id}",
            )
        return row


__all__ = [
    "WORKSPACE_AUTHORITY_CONFLICT",
    "WORKSPACE_AUTHORITY_INVALID",
    "WORKSPACE_AUTHORITY_MIGRATION_ASSET_KEY",
    "WORKSPACE_AUTHORITY_MIGRATION_SHA256",
    "WORKSPACE_AUTHORITY_NOT_FOUND",
    "WORKSPACE_AUTHORITY_SCHEMA_VERSION",
    "WORKSPACE_DURABLE_DATA_INVALID",
    "WORKSPACE_LEASE_CONFLICT",
    "WORKSPACE_OWNERSHIP_CONFLICT",
    "WORKSPACE_SCHEMA_NOT_READY",
    "WORKSPACE_STALE_FENCE",
    "WorkspaceAuthority",
    "WorkspaceMutation",
    "WorkspaceRepository",
    "WorkspaceRepositoryError",
]
