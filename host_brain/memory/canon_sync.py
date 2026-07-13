"""Pure operations over the explicitly composed Canon application service."""

from __future__ import annotations

from typing import Any

from host_brain.canon.application import CanonApplicationService
from host_brain.canon.contracts import CanonSyncRecord as WorldCanonSyncRecord


WORLD_CANON_VERSION_DRIFT = "WORLD_CANON_VERSION_DRIFT"
WORLD_CANON_NOT_INDEXED = "WORLD_CANON_NOT_INDEXED"
WORLD_CANON_FILE_MISSING = "WORLD_CANON_FILE_MISSING"
WORLD_CANON_INVALID_DOC = "WORLD_CANON_INVALID_DOC"

S_CANON_SYNC = "S_CANON_SYNC"
S_CANON_DRIFT = "S_CANON_DRIFT"


def sync_world_canon_index(
    *,
    service: CanonApplicationService,
    ts_ms: int | None = None,
) -> list[WorldCanonSyncRecord]:
    """Project Canon assets through the single application/writer boundary."""

    return list(service.sync_startup(ts_ms=ts_ms).records)


def detect_world_version_drift(
    *,
    service: CanonApplicationService,
) -> list[dict[str, Any]]:
    """Pure read comparison between assets and the durable Canon projection."""

    preview = service.preview_sync()
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
        indexed = service.get_document(record.doc_id)
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
        indexed = service.get_document(doc_id)
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
