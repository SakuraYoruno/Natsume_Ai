"""Compatibility facade for the explicit Canon application service.

Production startup must inject ``CanonApplicationService``.  The ledger-based
constructor remains only for narrow compatibility callers and performs no
direct writes; all mutations still enter ``CanonRepository``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.canon.application import CanonApplicationService
from host_brain.canon.contracts import CanonSyncRecord as WorldCanonSyncRecord
from host_brain.persistence.canon_repository import CanonRepository


WORLD_CANON_VERSION_DRIFT = "WORLD_CANON_VERSION_DRIFT"
WORLD_CANON_NOT_INDEXED = "WORLD_CANON_NOT_INDEXED"
WORLD_CANON_FILE_MISSING = "WORLD_CANON_FILE_MISSING"
WORLD_CANON_INVALID_DOC = "WORLD_CANON_INVALID_DOC"

S_CANON_SYNC = "S_CANON_SYNC"
S_CANON_DRIFT = "S_CANON_DRIFT"


def sync_world_canon_index(
    *,
    world_root: str | Path = "host_brain/canon/world",
    service: CanonApplicationService | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> list[WorldCanonSyncRecord]:
    """Project Canon assets through the single application/writer boundary."""

    application = service or _compat_application(ledger=ledger, world_root=world_root)
    return list(application.sync_startup(ts_ms=ts_ms).records)


def detect_world_version_drift(
    *,
    world_root: str | Path = "host_brain/canon/world",
    service: CanonApplicationService | None = None,
    ledger: SQLiteLedger | None = None,
) -> list[dict[str, Any]]:
    """Pure read comparison between assets and the durable Canon projection."""

    application = service or _compat_application(ledger=ledger, world_root=world_root)
    preview = application.preview_sync()
    drifts: list[dict[str, Any]] = []
    for issue in preview.issues:
        drifts.append(
            {
                "code": WORLD_CANON_INVALID_DOC,
                "source_path": issue.source_path,
                "detail": issue.detail,
            }
        )
    for record in preview.records:
        if not record.changed:
            continue
        indexed = application.get_document(record.doc_id)
        if indexed is None:
            drifts.append(
                {
                    "code": WORLD_CANON_NOT_INDEXED,
                    "doc_id": record.doc_id,
                    "file_version": record.version,
                    "file_hash": record.hash,
                    "source_path": record.source_path,
                }
            )
        else:
            drifts.append(
                {
                    "code": WORLD_CANON_VERSION_DRIFT,
                    "doc_id": record.doc_id,
                    "indexed_version": int(indexed["version"]),
                    "file_version": record.version,
                    "indexed_hash": str(indexed["hash"]),
                    "file_hash": record.hash,
                    "source_path": record.source_path,
                }
            )
    for doc_id in preview.removed_doc_ids:
        indexed = application.get_document(doc_id)
        drifts.append(
            {
                "code": WORLD_CANON_FILE_MISSING,
                "doc_id": doc_id,
                "indexed_version": int(indexed["version"]) if indexed else None,
                "indexed_hash": str(indexed["hash"]) if indexed else None,
                "source_path": str(indexed["source_path"]) if indexed else None,
            }
        )
    return drifts


def _compat_application(
    *,
    ledger: SQLiteLedger | None,
    world_root: str | Path,
) -> CanonApplicationService:
    if ledger is None:
        raise RuntimeError("Canon application service must be injected")
    db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
    if not db_path or db_path == ":memory:":
        raise RuntimeError("Canon application service requires a migrated durable database")
    return CanonApplicationService(
        repository=CanonRepository(db_path),
        world_root=world_root,
    )


__all__ = [
    "S_CANON_DRIFT",
    "S_CANON_SYNC",
    "WORLD_CANON_FILE_MISSING",
    "WORLD_CANON_INVALID_DOC",
    "WORLD_CANON_NOT_INDEXED",
    "WORLD_CANON_VERSION_DRIFT",
    "WorldCanonSyncRecord",
    "detect_world_version_drift",
    "sync_world_canon_index",
]
