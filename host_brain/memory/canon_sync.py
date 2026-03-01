"""World canon file scan and index sync helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.canon_world_store import WorldCanonStore


WORLD_CANON_VERSION_DRIFT = "WORLD_CANON_VERSION_DRIFT"
WORLD_CANON_NOT_INDEXED = "WORLD_CANON_NOT_INDEXED"
WORLD_CANON_FILE_MISSING = "WORLD_CANON_FILE_MISSING"
WORLD_CANON_INVALID_DOC = "WORLD_CANON_INVALID_DOC"

S_CANON_SYNC = "S_CANON_SYNC"
S_CANON_DRIFT = "S_CANON_DRIFT"


@dataclass(frozen=True)
class WorldCanonSyncRecord:
    doc_id: str
    version: int
    hash: str
    source_path: str
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version": self.version,
            "hash": self.hash,
            "source_path": self.source_path,
            "changed": self.changed,
        }


def sync_world_canon_index(
    *,
    world_root: str | Path = "host_brain/canon/world",
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> list[WorldCanonSyncRecord]:
    """Scan world canon assets, refresh sqlite index, and log world changes."""

    ledger_obj = ledger or SQLiteLedger()
    store = WorldCanonStore(world_root=world_root, ledger=ledger_obj)
    synced: list[WorldCanonSyncRecord] = []
    sync_ts = int(ts_ms if ts_ms is not None else now_ms())
    docs, issues = store.scan_documents()

    for issue in issues:
        ledger_obj.log_error(
            request_id=None,
            stage=S_CANON_SYNC,
            reason_code=WORLD_CANON_INVALID_DOC,
            detail=f"{issue.code}::{issue.source_path}::{issue.detail}",
        )

    for doc in docs:
        indexed = ledger_obj.get_world_canon_doc(doc.doc_id)
        changed = (
            indexed is None
            or int(indexed.get("version", -1)) != doc.version
            or str(indexed.get("hash", "")) != doc.hash
            or str(indexed.get("status", "")) != doc.status
            or str(indexed.get("source_path", "")) != doc.source_path
        )

        ledger_obj.upsert_world_canon_doc(
            {
                "doc_id": doc.doc_id,
                "version": doc.version,
                "hash": doc.hash,
                "status": doc.status,
                "source_path": doc.source_path,
                "updated_ts": sync_ts,
            }
        )

        if changed:
            proposal_id = f"canon_sync::{doc.doc_id}::v{doc.version}"
            ledger_obj.log_canon_proposal(
                {
                    "proposal_id": proposal_id,
                    "canon_kind": "world",
                    "target_id": doc.doc_id,
                    "base_version": int(indexed["version"]) if indexed is not None else None,
                    "proposed_version": doc.version,
                    "proposed_hash": doc.hash,
                    "proposer_node_id": "memory.canon_sync",
                    "summary": "world canon index sync",
                    "status": "SYNC_INDEXED",
                    "ts_ms": sync_ts,
                }
            )
            ledger_obj.log_canon_change(
                {
                    "change_id": f"canon_change::world::{doc.doc_id}::v{doc.version}",
                    "proposal_id": proposal_id,
                    "canon_kind": "world",
                    "target_id": doc.doc_id,
                    "before_version": int(indexed["version"]) if indexed is not None else None,
                    "after_version": doc.version,
                    "before_hash": str(indexed["hash"]) if indexed is not None else None,
                    "after_hash": doc.hash,
                    "source_path": doc.source_path,
                    "ts_ms": sync_ts,
                }
            )

        synced.append(
            WorldCanonSyncRecord(
                doc_id=doc.doc_id,
                version=doc.version,
                hash=doc.hash,
                source_path=doc.source_path,
                changed=changed,
            )
        )

    return synced


def detect_world_version_drift(
    *,
    world_root: str | Path = "host_brain/canon/world",
    ledger: SQLiteLedger | None = None,
) -> list[dict[str, Any]]:
    """Find mismatches between world canon files and sqlite index snapshot."""

    ledger_obj = ledger or SQLiteLedger()
    store = WorldCanonStore(world_root=world_root, ledger=ledger_obj)
    drifts: list[dict[str, Any]] = []
    docs, issues = store.scan_documents()

    for issue in issues:
        drifts.append(
            {
                "code": WORLD_CANON_INVALID_DOC,
                "source_path": issue.source_path,
                "detail": issue.detail,
            }
        )
        ledger_obj.log_error(
            request_id=None,
            stage=S_CANON_DRIFT,
            reason_code=WORLD_CANON_INVALID_DOC,
            detail=f"{issue.code}::{issue.source_path}::{issue.detail}",
        )

    file_docs = {doc.doc_id: doc for doc in docs}
    for doc_id, doc in sorted(file_docs.items()):
        indexed = ledger_obj.get_world_canon_doc(doc_id)
        if indexed is None:
            drifts.append(
                {
                    "code": WORLD_CANON_NOT_INDEXED,
                    "doc_id": doc_id,
                    "file_version": doc.version,
                    "file_hash": doc.hash,
                    "source_path": doc.source_path,
                }
            )
            continue

        indexed_version = int(indexed.get("version", -1))
        indexed_hash = str(indexed.get("hash", ""))
        if indexed_version != doc.version or indexed_hash != doc.hash:
            drifts.append(
                {
                    "code": WORLD_CANON_VERSION_DRIFT,
                    "doc_id": doc_id,
                    "indexed_version": indexed_version,
                    "file_version": doc.version,
                    "indexed_hash": indexed_hash,
                    "file_hash": doc.hash,
                    "source_path": doc.source_path,
                }
            )

    indexed_doc_ids = {row["doc_id"] for row in ledger_obj.world_canon_doc}
    for doc_id in sorted(indexed_doc_ids - set(file_docs.keys())):
        row = ledger_obj.get_world_canon_doc(doc_id)
        drifts.append(
            {
                "code": WORLD_CANON_FILE_MISSING,
                "doc_id": doc_id,
                "indexed_version": int(row["version"]) if row is not None else None,
                "indexed_hash": str(row["hash"]) if row is not None else None,
                "source_path": str(row["source_path"]) if row is not None else None,
            }
        )

    return drifts
