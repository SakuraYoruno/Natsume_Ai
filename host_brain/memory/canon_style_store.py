"""Style canon SSOT store backed by a single v5 canonical profile file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.canon_vector_auto_sync import trigger_canon_vector_auto_sync

STYLE_V5_FILE_NAME = "style_profile_v5.json"
STYLE_CANON_KEY_MAP: dict[str, str] = {
    "natsume_ai_cn": "instruction_cn",
    "natsume_ai_ja": "instruction_ja",
}
STYLE_CANON_TAGS: dict[str, list[str]] = {
    "natsume_ai_cn": ["natsume_ai", "style_canon", "tone_only", "distilled_v5", "cn"],
    "natsume_ai_ja": ["natsume_ai", "style_canon", "tone_only", "distilled_v5", "ja"],
}
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
    """Single-writer style canon store backed by canonical v5 JSON."""

    def __init__(self, *, style_root: str | Path = "host_brain/canon/style", ledger: SQLiteLedger | None = None) -> None:
        self.style_root = Path(style_root)
        self.style_root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()

    def get(self, canon_id: str) -> StyleCanonEntry | None:
        normalized_canon_id = _normalize_canon_id(canon_id)
        if normalized_canon_id in STYLE_CANON_KEY_MAP:
            payload = self._load_v5_payload()
            if payload is not None:
                entry = _entry_from_payload(self._v5_path(), payload, normalized_canon_id)
                if entry is not None:
                    return entry
        return self._load_legacy_entry(normalized_canon_id)

    def list_entries(self) -> list[StyleCanonEntry]:
        entries: list[StyleCanonEntry] = []
        seen_ids: set[str] = set()

        payload = self._load_v5_payload()
        if payload is not None:
            for canon_id in STYLE_CANON_KEY_MAP:
                entry = _entry_from_payload(self._v5_path(), payload, canon_id)
                if entry is not None:
                    entries.append(entry)
                    seen_ids.add(entry.canon_id)

        for path in sorted(self.style_root.glob("*.json")):
            if path.name == STYLE_V5_FILE_NAME:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                continue
            canon_id = str(payload.get("canon_id") or "").strip()
            if not canon_id or canon_id in seen_ids:
                continue
            entry = self._load_legacy_entry(canon_id)
            if entry is not None:
                entries.append(entry)
                seen_ids.add(entry.canon_id)
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

        if canon_id not in STYLE_CANON_KEY_MAP:
            return self._upsert_legacy_entry(
                canon_id=canon_id,
                content=content,
                tags=tags,
                proposal_id=proposal_id,
                proposer_node_id=proposer_node_id,
                ts_ms=ts_ms,
            )

        current_payload = self._load_v5_payload() or {}
        current_entry = _entry_from_payload(self._v5_path(), current_payload, canon_id)
        base_version = int(current_entry.version) if current_entry is not None else int(current_payload.get("v5_version") or 5)
        update_ts = int(ts_ms if ts_ms is not None else now_ms())
        normalized_tags = _normalize_tags(tags) or list(STYLE_CANON_TAGS.get(canon_id, []))
        key = STYLE_CANON_KEY_MAP[canon_id]

        current_payload[key] = content
        current_payload["v5_version"] = max(5, int(current_payload.get("v5_version") or 5))
        current_payload["distill_date"] = date.fromtimestamp(update_ts / 1000).isoformat()
        current_payload["updated_by"] = proposal_id
        current_payload[f"tags_{canon_id.rsplit('_', 1)[-1]}"] = normalized_tags

        self._v5_path().write_text(
            json.dumps(current_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        next_entry = self.get(canon_id)
        if next_entry is None:
            raise RuntimeError(f"style canon upsert failed for {canon_id}")

        self.ledger.log_canon_proposal(
            {
                "proposal_id": proposal_id,
                "canon_kind": "style",
                "target_id": canon_id,
                "base_version": base_version,
                "proposed_version": next_entry.version,
                "proposed_hash": next_entry.hash,
                "proposer_node_id": proposer_node_id,
                "summary": "style canon v5 upsert",
                "status": "APPLIED",
                "ts_ms": update_ts,
            }
        )
        self.ledger.log_canon_change(
            {
                "change_id": f"canon_change::style::{canon_id}::v{next_entry.version}",
                "proposal_id": proposal_id,
                "canon_kind": "style",
                "target_id": canon_id,
                "before_version": current_entry.version if current_entry is not None else None,
                "after_version": next_entry.version,
                "before_hash": current_entry.hash if current_entry is not None else None,
                "after_hash": next_entry.hash,
                "source_path": str(self._v5_path()),
                "ts_ms": update_ts,
            }
        )
        trigger_canon_vector_auto_sync(
            include_style=True,
            include_world=False,
            style_root=self.style_root,
            reason=f"style_v5_upsert:{canon_id}@v{next_entry.version}",
        )
        return next_entry

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
            "citation": f"style_canon:{entry.canon_id}@v{entry.version}:{entry.hash[:12]}",
        }

    def _v5_path(self) -> Path:
        return self.style_root / STYLE_V5_FILE_NAME

    def _legacy_entry_path(self, canon_id: str) -> Path:
        return self.style_root / f"{_normalize_canon_id(canon_id)}.json"

    def _load_v5_payload(self) -> dict[str, Any] | None:
        path = self._v5_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _load_legacy_entry(self, canon_id: str) -> StyleCanonEntry | None:
        path = self._legacy_entry_path(canon_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        content = str(payload.get("content") or "").strip()
        version = int(payload.get("version") or 1)
        tags = [str(tag).strip() for tag in payload.get("tags", [])] if isinstance(payload.get("tags"), list) else []
        hash_value = str(payload.get("hash") or "").strip()
        if not hash_value:
            hash_value = _stable_hash(
                {
                    "canon_id": canon_id,
                    "version": version,
                    "tone_only": bool(payload.get("tone_only", True)),
                    "unsafe_as_fact": bool(payload.get("unsafe_as_fact", True)),
                    "content": content,
                    "tags": tags,
                }
            )
        return StyleCanonEntry(
            canon_id=str(payload.get("canon_id") or canon_id),
            version=version,
            hash=hash_value,
            tone_only=bool(payload.get("tone_only", True)),
            unsafe_as_fact=bool(payload.get("unsafe_as_fact", True)),
            content=content,
            tags=tags,
            updated_by=str(payload.get("updated_by") or "legacy_style"),
            ts_ms=int(payload.get("ts_ms") or int(path.stat().st_mtime * 1000)),
        )

    def _upsert_legacy_entry(
        self,
        *,
        canon_id: str,
        content: str,
        tags: list[str] | None,
        proposal_id: str,
        proposer_node_id: str,
        ts_ms: int | None,
    ) -> StyleCanonEntry:
        current = self._load_legacy_entry(canon_id)
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
        path = self._legacy_entry_path(canon_id)
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
        loaded = self._load_legacy_entry(canon_id)
        if loaded is None:
            raise RuntimeError(f"style canon upsert failed for {canon_id}")
        return loaded


def _entry_from_payload(path: Path, payload: dict[str, Any], canon_id: str) -> StyleCanonEntry | None:
    key = STYLE_CANON_KEY_MAP.get(canon_id)
    if not key:
        return None
    content = str(payload.get(key) or "").strip()
    if not content:
        return None
    version = max(5, int(payload.get("v5_version") or 5))
    updated_by = str(payload.get("updated_by") or f"style_profile_v5:{payload.get('distill_date') or 'unknown'}").strip()
    ts_ms = int(path.stat().st_mtime * 1000) if path.exists() else int(now_ms())
    tags = payload.get(f"tags_{canon_id.rsplit('_', 1)[-1]}")
    tag_list = [str(tag).strip() for tag in tags] if isinstance(tags, list) else []
    if not tag_list:
        tag_list = list(STYLE_CANON_TAGS.get(canon_id, []))
    hash_payload = {
        "canon_id": canon_id,
        "content": content,
        "version": version,
        "distill_date": str(payload.get("distill_date") or ""),
    }
    hash_value = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return StyleCanonEntry(
        canon_id=canon_id,
        version=version,
        hash=hash_value,
        tone_only=True,
        unsafe_as_fact=True,
        content=content,
        tags=tag_list,
        updated_by=updated_by,
        ts_ms=ts_ms,
    )


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
