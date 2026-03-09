"""Style Canon SSOT store with versioned updates and audit trace hooks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.canon_vector_auto_sync import trigger_canon_vector_auto_sync


STYLE_EXECUTION_FACT_TOKENS = (
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
class StyleCanonEntry:
    canon_id: str
    version: int
    hash: str
    tone_only: bool
    unsafe_as_fact: bool
    content: str
    tags: list[str]
    updated_by: str
    ts_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "canon_id": self.canon_id,
            "version": self.version,
            "hash": self.hash,
            "tone_only": self.tone_only,
            "unsafe_as_fact": self.unsafe_as_fact,
            "content": self.content,
            "tags": list(self.tags),
            "updated_by": self.updated_by,
            "ts_ms": self.ts_ms,
        }


class StyleCanonStore:
    """Single-writer style canon store."""

    def __init__(
        self,
        *,
        style_root: str | Path = "host_brain/canon/style",
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.style_root = Path(style_root)
        self.style_root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()

    def get(self, canon_id: str) -> StyleCanonEntry | None:
        path = self._entry_path(canon_id)
        if not path.exists():
            return None

        payload = json.loads(path.read_text(encoding="utf-8"))
        return StyleCanonEntry(
            canon_id=str(payload["canon_id"]),
            version=int(payload["version"]),
            hash=str(payload["hash"]),
            tone_only=bool(payload["tone_only"]),
            unsafe_as_fact=bool(payload["unsafe_as_fact"]),
            content=str(payload["content"]),
            tags=[str(tag) for tag in payload.get("tags", [])],
            updated_by=str(payload["updated_by"]),
            ts_ms=int(payload["ts_ms"]),
        )

    def list_entries(self) -> list[StyleCanonEntry]:
        entries: list[StyleCanonEntry] = []
        for path in sorted(self.style_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if "canon_id" not in payload:
                continue
            item = self.get(str(payload["canon_id"]))
            if item is not None:
                entries.append(item)
        return entries

    def upsert(
        self,
        *,
        canon_id: str,
        content: str,
        tags: list[str] | None,
        proposal_id: str,
        proposer_node_id: str = "memory.canon_style_store",
        ts_ms: int | None = None,
        allow_write: bool = True,
    ) -> StyleCanonEntry:
        if not allow_write:
            raise PermissionError("style canon write is blocked by policy")
        if not proposal_id:
            raise ValueError("proposal_id is required")
        canon_id = _normalize_canon_id(canon_id)
        content = str(content).strip()
        if not content:
            raise ValueError("style canon content cannot be empty")
        if _contains_execution_claim(content):
            raise ValueError("style canon must not assert execution facts")

        current = self.get(canon_id)
        base_version = current.version if current is not None else 0
        before_hash = current.hash if current is not None else None
        next_version = base_version + 1
        update_ts = int(ts_ms if ts_ms is not None else now_ms())

        normalized_tags = _normalize_tags(tags)
        entry_payload = {
            "canon_id": canon_id,
            "version": next_version,
            "tone_only": True,
            "unsafe_as_fact": True,
            "content": content,
            "tags": normalized_tags,
            "updated_by": proposal_id,
            "ts_ms": update_ts,
        }
        entry_payload["hash"] = _stable_hash(entry_payload)
        path = self._entry_path(canon_id)
        path.write_text(
            json.dumps(entry_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.ledger.log_canon_proposal(
            {
                "proposal_id": proposal_id,
                "canon_kind": "style",
                "target_id": canon_id,
                "base_version": base_version,
                "proposed_version": next_version,
                "proposed_hash": entry_payload["hash"],
                "proposer_node_id": proposer_node_id,
                "summary": "style canon upsert",
                "status": "APPLIED",
                "ts_ms": update_ts,
            }
        )
        self.ledger.log_canon_change(
            {
                "change_id": f"canon_change::style::{canon_id}::v{next_version}",
                "proposal_id": proposal_id,
                "canon_kind": "style",
                "target_id": canon_id,
                "before_version": base_version if base_version > 0 else None,
                "after_version": next_version,
                "before_hash": before_hash,
                "after_hash": entry_payload["hash"],
                "source_path": str(path),
                "ts_ms": update_ts,
            }
        )
        trigger_canon_vector_auto_sync(
            include_style=True,
            include_world=False,
            style_root=self.style_root,
            reason=f"style_upsert:{canon_id}@v{next_version}",
        )
        return self.get(canon_id)  # type: ignore[return-value]

    def build_style_pack(self, canon_id: str) -> dict[str, Any]:
        entry = self.get(canon_id)
        if entry is None:
            raise KeyError(f"unknown style canon: {canon_id}")
        return {
            "slot": "style_pack",
            "canon_id": entry.canon_id,
            "version": entry.version,
            "hash": entry.hash,
            "tone_only": entry.tone_only,
            "unsafe_as_fact": entry.unsafe_as_fact,
            "style_instruction": entry.content,
            "tags": list(entry.tags),
            "citation": (
                f"style_canon:{entry.canon_id}@v{entry.version}:{entry.hash[:12]}"
            ),
        }

    def _entry_path(self, canon_id: str) -> Path:
        return self.style_root / f"{_normalize_canon_id(canon_id)}.json"


def _normalize_canon_id(canon_id: str) -> str:
    value = str(canon_id).strip()
    if not value:
        raise ValueError("canon_id is required")
    if "/" in value or "\\" in value:
        raise ValueError(f"invalid canon_id path segment: {canon_id}")
    allowed = value.replace("-", "").replace("_", "")
    if not allowed.isalnum():
        raise ValueError(f"canon_id contains unsupported characters: {canon_id}")
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
    return any(token in lowered for token in STYLE_EXECUTION_FACT_TOKENS)


def _stable_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
