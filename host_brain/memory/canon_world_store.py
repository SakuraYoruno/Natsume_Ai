"""World Canon SSOT store with versioned updates and auditable references."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms


WORLD_EXECUTION_FACT_TOKENS = (
    "已执行",
    "执行完成",
    "已保存",
    "保存成功",
    "已确认",
    "confirmed",
    "saved successfully",
    "executed",
    "execution complete",
)


@dataclass(frozen=True)
class WorldCanonDocument:
    doc_id: str
    version: int
    hash: str
    status: str
    title: str
    tags: list[str]
    body: str
    updated_by: str
    ts_ms: int
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version": self.version,
            "hash": self.hash,
            "status": self.status,
            "title": self.title,
            "tags": list(self.tags),
            "body": self.body,
            "updated_by": self.updated_by,
            "ts_ms": self.ts_ms,
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class WorldCanonScanIssue:
    code: str
    source_path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_path": self.source_path,
            "detail": self.detail,
        }


class WorldCanonStore:
    """Single-writer world canon store."""

    def __init__(
        self,
        *,
        world_root: str | Path = "host_brain/canon/world",
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.world_root = Path(world_root)
        self.world_root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()

    def get(self, doc_id: str) -> WorldCanonDocument | None:
        path = self._doc_path(doc_id)
        if not path.exists():
            return None
        return self._load_from_path(path)

    def list_documents(self) -> list[WorldCanonDocument]:
        docs, _issues = self.scan_documents()
        return docs

    def scan_documents(self) -> tuple[list[WorldCanonDocument], list[WorldCanonScanIssue]]:
        docs: list[WorldCanonDocument] = []
        issues: list[WorldCanonScanIssue] = []
        for path in sorted(self.world_root.glob("*.json")):
            try:
                docs.append(self._load_from_path(path))
            except json.JSONDecodeError as exc:
                issues.append(
                    WorldCanonScanIssue(
                        code="WORLD_CANON_JSON_INVALID",
                        source_path=str(path),
                        detail=str(exc),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                issues.append(
                    WorldCanonScanIssue(
                        code="WORLD_CANON_SCHEMA_INVALID",
                        source_path=str(path),
                        detail=str(exc),
                    )
                )
        return docs, issues

    def upsert_document(
        self,
        *,
        doc_id: str,
        title: str,
        tags: list[str] | None,
        body: str,
        proposal_id: str,
        proposer_node_id: str = "memory.canon_world_store",
        status: str = "active",
        ts_ms: int | None = None,
        allow_write: bool = True,
    ) -> WorldCanonDocument:
        if not allow_write:
            raise PermissionError("world canon write is blocked by policy")
        if not proposal_id:
            raise ValueError("proposal_id is required")

        doc_id = _normalize_doc_id(doc_id)
        title = str(title).strip()
        body = str(body).strip()
        status = str(status).strip() or "active"
        if not title:
            raise ValueError("world canon title cannot be empty")
        if not body:
            raise ValueError("world canon body cannot be empty")
        if _contains_execution_claim(body):
            raise ValueError("world canon body must not assert execution facts")

        current = self.get(doc_id)
        base_version = current.version if current is not None else 0
        before_hash = current.hash if current is not None else None
        next_version = base_version + 1
        update_ts = int(ts_ms if ts_ms is not None else now_ms())

        payload = {
            "doc_id": doc_id,
            "version": next_version,
            "status": status,
            "title": title,
            "tags": _normalize_tags(tags),
            "body": body,
            "updated_by": proposal_id,
            "ts_ms": update_ts,
        }
        payload["hash"] = compute_world_canon_hash(payload)

        path = self._doc_path(doc_id)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.ledger.log_canon_proposal(
            {
                "proposal_id": proposal_id,
                "canon_kind": "world",
                "target_id": doc_id,
                "base_version": base_version,
                "proposed_version": next_version,
                "proposed_hash": payload["hash"],
                "proposer_node_id": proposer_node_id,
                "summary": "world canon upsert",
                "status": "APPLIED",
                "ts_ms": update_ts,
            }
        )
        self.ledger.log_canon_change(
            {
                "change_id": f"canon_change::world::{doc_id}::v{next_version}",
                "proposal_id": proposal_id,
                "canon_kind": "world",
                "target_id": doc_id,
                "before_version": base_version if base_version > 0 else None,
                "after_version": next_version,
                "before_hash": before_hash,
                "after_hash": payload["hash"],
                "source_path": str(path),
                "ts_ms": update_ts,
            }
        )
        self.ledger.upsert_world_canon_doc(
            {
                "doc_id": doc_id,
                "version": next_version,
                "hash": payload["hash"],
                "status": status,
                "source_path": str(path),
                "updated_ts": update_ts,
            }
        )
        return self.get(doc_id)  # type: ignore[return-value]

    def build_authoritative_entry(self, doc_id: str) -> dict[str, Any]:
        doc = self.get(doc_id)
        if doc is None:
            raise KeyError(f"unknown world canon document: {doc_id}")
        return {
            "slot": "authoritative",
            "kind": "world_canon",
            "doc_id": doc.doc_id,
            "version": doc.version,
            "hash": doc.hash,
            "status": doc.status,
            "title": doc.title,
            "tags": list(doc.tags),
            "content": doc.body,
            "unsafe_as_fact": True,
            "reference_mode": "citation_only",
            "citation": f"world_canon:{doc.doc_id}@v{doc.version}:{doc.hash[:12]}",
        }

    def _doc_path(self, doc_id: str) -> Path:
        return self.world_root / f"{_normalize_doc_id(doc_id)}.json"

    def _load_from_path(self, path: Path) -> WorldCanonDocument:
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc_id = _normalize_doc_id(str(payload["doc_id"]))
        version = int(payload["version"])
        status = str(payload.get("status", "active"))
        title = str(payload["title"])
        tags = [str(tag) for tag in payload.get("tags", [])]
        body = str(payload["body"])
        updated_by = str(payload.get("updated_by", "unknown"))
        ts_value = int(payload.get("ts_ms", 0))

        expected_hash = compute_world_canon_hash(
            {
                "doc_id": doc_id,
                "version": version,
                "status": status,
                "title": title,
                "tags": tags,
                "body": body,
                "updated_by": updated_by,
                "ts_ms": ts_value,
            }
        )
        hash_value = str(payload.get("hash", expected_hash))
        if hash_value != expected_hash:
            hash_value = expected_hash

        return WorldCanonDocument(
            doc_id=doc_id,
            version=version,
            hash=hash_value,
            status=status,
            title=title,
            tags=tags,
            body=body,
            updated_by=updated_by,
            ts_ms=ts_value,
            source_path=str(path),
        )


def compute_world_canon_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_doc_id(doc_id: str) -> str:
    value = str(doc_id).strip()
    if not value:
        raise ValueError("doc_id is required")
    if "/" in value or "\\" in value:
        raise ValueError(f"invalid doc_id path segment: {doc_id}")
    allowed = value.replace("-", "").replace("_", "")
    if not allowed.isalnum():
        raise ValueError(f"doc_id contains unsupported characters: {doc_id}")
    return value


def _normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _contains_execution_claim(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in WORLD_EXECUTION_FACT_TOKENS)
