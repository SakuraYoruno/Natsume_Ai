"""Read/proposal WorldCanon store; all file mutation is permit-gated."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.canon_schemas import WorldCanonDocument, WorldCanonScanIssue


try:
    from filelock import FileLock as _FileLock  # type: ignore[import-untyped]

    _FILELOCK_AVAILABLE = True
except ImportError:  # pragma: no cover - production dependency guard
    _FileLock = None  # type: ignore[assignment,misc]
    _FILELOCK_AVAILABLE = False


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
_CANON_DIRECT_WRITE_FORBIDDEN = "CANON_DIRECT_WRITE_FORBIDDEN"
_CANON_APPLY_PAYLOAD_KEYS = frozenset(
    {
        "doc_id",
        "version",
        "status",
        "title",
        "tags",
        "body",
        "updated_by",
        "ts_ms",
        "hash",
    }
)
_CANON_APPLY_STRING_KEYS = (
    "doc_id",
    "status",
    "title",
    "body",
    "updated_by",
    "hash",
)

# The sole capability object.  There is no env/test switch that can mint one.
_CANON_APPLY_PERMIT = object()


class WorldCanonStore:
    """Read Canon and submit/reject proposals without mutating Canon files."""

    def __init__(
        self,
        *,
        world_root: str | Path = "host_brain/canon/world",
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.world_root = Path(world_root)
        self.world_root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()
        self._lock_dir = self.world_root / ".locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    def get(self, doc_id: str) -> WorldCanonDocument | None:
        path = self._doc_path(doc_id)
        return self._load_from_path(path) if path.exists() else None

    def list_documents(self) -> list[WorldCanonDocument]:
        documents, _issues = self.scan_documents()
        return documents

    def scan_documents(self) -> tuple[list[WorldCanonDocument], list[WorldCanonScanIssue]]:
        documents: list[WorldCanonDocument] = []
        issues: list[WorldCanonScanIssue] = []
        for path in sorted(self.world_root.glob("*.json")):
            try:
                documents.append(self._load_from_path(path))
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
        return documents, issues

    # Raw mutation APIs remain as explicit fail-closed compatibility surfaces.
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
        raise PermissionError(_CANON_DIRECT_WRITE_FORBIDDEN)

    def upsert(
        self,
        *,
        doc_id: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
        proposal_id: str,
        proposer_node_id: str = "memory.canon_world_store",
        status: str = "active",
        ts_ms: int | None = None,
        allow_write: bool = True,
    ) -> WorldCanonDocument:
        raise PermissionError(_CANON_DIRECT_WRITE_FORBIDDEN)

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        approver_node_id: str = "memory.canon_world_store",
        ts_ms: int | None = None,
    ) -> WorldCanonDocument:
        raise PermissionError(_CANON_DIRECT_WRITE_FORBIDDEN)

    def approve_proposal_with_content(
        self,
        proposal_id: str,
        *,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
        approver_node_id: str = "memory.canon_world_store",
        ts_ms: int | None = None,
    ) -> WorldCanonDocument:
        raise PermissionError(_CANON_DIRECT_WRITE_FORBIDDEN)

    def rollback_proposal(
        self,
        proposal_id: str,
        *,
        rollback_reason: str = "",
        operator_node_id: str = "api.canon_proposals.rollback",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        raise PermissionError(_CANON_DIRECT_WRITE_FORBIDDEN)

    def build_authoritative_entry(self, doc_id: str) -> dict[str, Any]:
        document = self.get(doc_id)
        if document is None:
            raise KeyError(f"unknown world canon document: {doc_id}")
        return {
            "slot": "authoritative",
            "kind": "world_canon",
            "doc_id": document.doc_id,
            "version": document.version,
            "hash": document.hash,
            "status": document.status,
            "title": document.title,
            "tags": list(document.tags),
            "content": document.body,
            "unsafe_as_fact": True,
            "reference_mode": "citation_only",
            "citation": f"world_canon:{document.doc_id}@v{document.version}:{document.hash[:12]}",
        }

    def submit_proposal(
        self,
        *,
        proposal_id: str,
        target_doc_id: str,
        proposed_title: str,
        proposed_tags: list[str] | None,
        proposed_body: str,
        proposer_node_id: str = "memory.canon_world_store",
        source_intent_id: str | None = None,
        summary: str = "",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        if not str(proposal_id or "").strip():
            raise ValueError("proposal_id is required")
        title = str(proposed_title or "").strip()
        body = str(proposed_body or "").strip()
        if not title or not body:
            raise ValueError("proposed_title and proposed_body are required")
        if _contains_execution_claim(body):
            raise ValueError("proposed_body must not assert execution facts")
        doc_id = _normalize_doc_id(target_doc_id)
        current = self.get(doc_id)
        base_version = current.version if current is not None else 0
        proposed_version = base_version + 1
        submit_ts = int(now_ms() if ts_ms is None else ts_ms)
        payload: dict[str, Any] = {
            "doc_id": doc_id,
            "version": proposed_version,
            "status": "active",
            "title": title,
            "tags": _normalize_tags(proposed_tags),
            "body": body,
            "updated_by": str(proposal_id),
            "ts_ms": submit_ts,
        }
        proposed_hash = compute_world_canon_hash(payload)
        row = {
            "proposal_id": str(proposal_id),
            "canon_kind": "world",
            "target_id": doc_id,
            "base_version": base_version,
            "proposed_version": proposed_version,
            "proposed_hash": proposed_hash,
            "proposed_title": title,
            "proposed_tags": payload["tags"],
            "proposed_body": body,
            "source_intent_id": source_intent_id,
            "proposer_node_id": proposer_node_id,
            "summary": summary or f"world canon proposal: {doc_id}",
            "status": "pending",
            "ts_ms": submit_ts,
        }
        self.ledger.log_canon_proposal(row)
        return row

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        rejector_node_id: str = "api.canon_proposals.reject",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        reject_ts = int(now_ms() if ts_ms is None else ts_ms)
        proposal = self.ledger.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if str(proposal.get("status")) != "pending":
            raise PermissionError(
                f"proposal {proposal_id!r} cannot be rejected from {proposal.get('status')!r}"
            )
        if not self.ledger.update_proposal_status(
            proposal_id, new_status="rejected", ts_ms=reject_ts
        ):
            raise RuntimeError("proposal status update failed")
        return {
            **proposal,
            "status": "rejected",
            "ts_ms": reject_ts,
            "reject_reason": str(reason)[:200],
            "rejector_node_id": rejector_node_id,
        }

    def extract_hotwords(self, max_words: int = 200) -> list[str]:
        words: set[str] = set()
        for document in self.list_documents():
            if document.status != "active":
                continue
            if document.title.strip():
                words.add(document.title.strip())
            words.update(tag.strip() for tag in document.tags if tag.strip())
        return sorted(words)[:max_words]

    def _doc_path(self, doc_id: str) -> Path:
        return self.world_root / f"{_normalize_doc_id(doc_id)}.json"

    def _load_from_path(self, path: Path) -> WorldCanonDocument:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        identity = {
            "doc_id": _normalize_doc_id(str(payload["doc_id"])),
            "version": int(payload["version"]),
            "status": str(payload.get("status", "active")),
            "title": str(payload["title"]),
            "tags": [str(tag) for tag in payload.get("tags", [])],
            "body": str(payload["body"]),
            "updated_by": str(payload.get("updated_by", "unknown")),
            "ts_ms": int(payload.get("ts_ms", 0)),
        }
        expected_hash = compute_world_canon_hash(identity)
        if str(payload.get("hash", expected_hash)) != expected_hash:
            raise ValueError(f"WORLD_CANON_HASH_MISMATCH:{identity['doc_id']}")
        return WorldCanonDocument(
            **identity,
            hash=expected_hash,
            source_path=str(path),
        )

    def _load_apply_snapshot(self, doc_id: str) -> dict[str, Any] | None:
        """Read an on-disk apply/recovery snapshot without compatibility coercion."""

        requested_doc_id = _normalize_doc_id(doc_id)
        path = self._doc_path(requested_doc_id)
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(
                raw,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except json.JSONDecodeError as exc:
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_JSON_INVALID") from exc
        if not isinstance(payload, dict) or set(payload) != _CANON_APPLY_PAYLOAD_KEYS:
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_SCHEMA_INVALID")
        if any(type(payload[key]) is not str for key in _CANON_APPLY_STRING_KEYS):
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_TYPE_INVALID")
        if type(payload["version"]) is not int or type(payload["ts_ms"]) is not int:
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_TYPE_INVALID")
        tags = payload["tags"]
        if type(tags) is not list or any(type(tag) is not str for tag in tags):
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_TYPE_INVALID")
        if (
            _normalize_doc_id(payload["doc_id"]) != payload["doc_id"]
            or payload["doc_id"] != requested_doc_id
            or payload["version"] <= 0
            or payload["ts_ms"] < 0
        ):
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_VALUE_INVALID")
        identity = {key: payload[key] for key in _CANON_APPLY_PAYLOAD_KEYS if key != "hash"}
        expected_hash = compute_world_canon_hash(identity)
        if payload["hash"] != expected_hash:
            raise ValueError(f"WORLD_CANON_HASH_MISMATCH:{payload['doc_id']}")
        return dict(payload)

    def _replace_authorized_payload(
        self,
        payload: dict[str, Any],
        *,
        expected_version: int,
        expected_hash: str | None,
        apply_id: str,
        permit: object,
    ) -> Path:
        if permit is not _CANON_APPLY_PERMIT:
            raise PermissionError("CANON_APPLY_PERMIT_INVALID")
        normalized = _validated_authorized_payload(payload)
        doc_id = normalized["doc_id"]
        path = self._doc_path(doc_id)
        lock_path = self._lock_dir / f"{doc_id}.lock"
        if not _FILELOCK_AVAILABLE or _FileLock is None:
            raise RuntimeError("CANON_APPLY_FILE_LOCK_UNAVAILABLE")
        with _FileLock(str(lock_path), timeout=30):
            _assert_expected_snapshot(
                self._load_apply_snapshot(doc_id),
                expected_version=int(expected_version),
                expected_hash=expected_hash,
            )
            temp_path = path.with_name(
                f".{path.name}.{_safe_file_token(apply_id)}.{uuid.uuid4().hex}.tmp"
            )
            try:
                encoded = (
                    json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode("utf-8")
                with temp_path.open("xb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                _fsync_directory(path.parent)
            finally:
                temp_path.unlink(missing_ok=True)
        return path

    def _delete_authorized_document(
        self,
        doc_id: str,
        *,
        expected_version: int,
        expected_hash: str | None,
        apply_id: str,
        permit: object,
    ) -> Path:
        if permit is not _CANON_APPLY_PERMIT:
            raise PermissionError("CANON_APPLY_PERMIT_INVALID")
        del apply_id
        normalized_id = _normalize_doc_id(doc_id)
        path = self._doc_path(normalized_id)
        lock_path = self._lock_dir / f"{normalized_id}.lock"
        if not _FILELOCK_AVAILABLE or _FileLock is None:
            raise RuntimeError("CANON_APPLY_FILE_LOCK_UNAVAILABLE")
        with _FileLock(str(lock_path), timeout=30):
            _assert_expected_snapshot(
                self._load_apply_snapshot(normalized_id),
                expected_version=int(expected_version),
                expected_hash=expected_hash,
            )
            path.unlink()
            _fsync_directory(path.parent)
        return path


def compute_world_canon_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("WORLD_CANON_APPLY_PAYLOAD_DUPLICATE_KEY")
        payload[key] = value
    return payload


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"WORLD_CANON_APPLY_PAYLOAD_CONSTANT_INVALID:{value}")


def _normalize_doc_id(doc_id: str) -> str:
    value = str(doc_id).strip()
    if (
        not value
        or len(value) > 160
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("doc_id is invalid")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"doc_id contains unsupported characters: {doc_id}")
    return value


def _normalize_tags(tags: Any) -> list[str]:
    normalized: list[str] = []
    for tag in tags or []:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _contains_execution_claim(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in WORLD_EXECUTION_FACT_TOKENS)


def _safe_file_token(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "").strip())
    if not normalized:
        raise ValueError("apply_id is required")
    return normalized[:120]


def _validated_authorized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "doc_id": _normalize_doc_id(payload.get("doc_id", "")),
        "version": int(payload.get("version") or 0),
        "status": str(payload.get("status") or "active").strip(),
        "title": str(payload.get("title") or "").strip(),
        "tags": _normalize_tags(payload.get("tags")),
        "body": str(payload.get("body") or "").strip(),
        "updated_by": str(payload.get("updated_by") or "").strip(),
        "ts_ms": int(payload.get("ts_ms") or 0),
    }
    if (
        normalized["version"] <= 0
        or normalized["ts_ms"] < 0
        or not normalized["title"]
        or not normalized["body"]
        or not normalized["updated_by"]
    ):
        raise ValueError("CANON_APPLY_PAYLOAD_INVALID")
    if _contains_execution_claim(normalized["body"]):
        raise ValueError("world canon body must not assert execution facts")
    expected_hash = compute_world_canon_hash(normalized)
    supplied_hash = str(payload.get("hash") or "").strip().lower()
    if supplied_hash and supplied_hash != expected_hash:
        raise ValueError("CANON_APPLY_PAYLOAD_HASH_MISMATCH")
    normalized["hash"] = expected_hash
    return normalized


def _assert_expected_snapshot(
    current: dict[str, Any] | None,
    *,
    expected_version: int,
    expected_hash: str | None,
) -> None:
    current_version = int(current["version"]) if current is not None else 0
    current_hash = str(current["hash"]) if current is not None else None
    if current_version != expected_version or current_hash != expected_hash:
        raise RuntimeError(
            "CANON_APPLY_BASE_DRIFT:"
            f"expected={expected_version}:{expected_hash or ''}:"
            f"actual={current_version}:{current_hash or ''}"
        )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(
            str(path),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
