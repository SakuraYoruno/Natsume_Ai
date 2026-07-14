"""Stable application contracts for Canon projection synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.memory.canon_schemas import WorldCanonScanIssue


@dataclass(frozen=True, slots=True)
class CanonSyncRecord:
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


@dataclass(frozen=True, slots=True)
class CanonSyncPreview:
    records: tuple[CanonSyncRecord, ...]
    removed_doc_ids: tuple[str, ...]
    issues: tuple[WorldCanonScanIssue, ...]


@dataclass(frozen=True, slots=True)
class CanonSyncResult:
    records: tuple[CanonSyncRecord, ...]
    removed_doc_ids: tuple[str, ...]
    issues: tuple[WorldCanonScanIssue, ...]
    legacy_proposals_imported: int = 0
    proposal_statuses_reconciled: int = 0

    @property
    def changed_count(self) -> int:
        return sum(1 for record in self.records if record.changed) + len(
            self.removed_doc_ids
        )


__all__ = ["CanonSyncPreview", "CanonSyncRecord", "CanonSyncResult"]
