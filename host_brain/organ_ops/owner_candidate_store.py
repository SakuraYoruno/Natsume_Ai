"""Owner-owned durable candidate state for BioOps handoff artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.owner_consumer import (
    OWNER_QUEUE_STATUS_QUEUED,
    OWNER_QUEUE_WRITE_POLICY,
)
from host_brain.bio_ops.owner_inbox import BIO_OPS_OWNER_IMPORT_CHANNELS, resolve_owner_import_channel
from host_brain.bio_ops.safety import is_bio_ops_sensitive_path
from host_brain.codex_runtime.artifact_store import REPO_ROOT, sanitize_segment
from host_brain.core.ids import now_ms


DEFAULT_OWNER_CANDIDATE_STATE_ROOT = REPO_ROOT / "artifacts" / "organ_ops_owner_candidate_state"
OWNER_CANDIDATE_STATUS_IMPORTED = "IMPORTED_FOR_OWNER_REVIEW"
OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED = "ALREADY_IMPORTED"
OWNER_CANDIDATE_STATUS_BLOCKED = "BLOCKED"
OWNER_DURABLE_CANDIDATE_WRITE_POLICY = "owner_durable_candidate_only"


@dataclass(slots=True)
class OwnerCandidateState:
    candidate_id: str
    owner_organ: str
    state_owner: str
    channel_id: str
    capability: str
    output_kind: str
    summary: str
    source_queue_item_path: str
    source_packet_path: str
    source_import_candidate_path: str
    status: str = OWNER_CANDIDATE_STATUS_IMPORTED
    review_status: str = "PENDING_OWNER_STATE_REVIEW"
    direct_long_term_write: bool = False
    durable_write_performed: bool = False
    durable_memory_write_performed: bool = False
    owner_candidate_state_write_performed: bool = True
    write_policy: str = OWNER_DURABLE_CANDIDATE_WRITE_POLICY
    risk_flags: list[str] = field(default_factory=lambda: ["owner_candidate_state_only"])
    next_actions: list[str] = field(
        default_factory=lambda: [
            "Owner organ may review this durable candidate state.",
            "Do not treat this record as an applied memory, Bond, Canon, persona, secret, or production configuration write.",
        ]
    )
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _owner_candidate_state_path(state_root: Path, *, owner_organ: str, channel_id: str, candidate_id: str) -> Path:
    return (
        state_root
        / sanitize_segment(owner_organ, "owner")
        / sanitize_segment(channel_id, "channel")
        / sanitize_segment(candidate_id, "candidate")
        / "owner_candidate_state.json"
    )


def validate_owner_queue_item_for_durable_import(item: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    payload = _as_dict(item)
    candidate_id = str(payload.get("candidate_id") or "").strip()
    owner = str(payload.get("owner_organ") or "").strip()
    channel_id = str(payload.get("channel_id") or "").strip()
    output_kind = str(payload.get("output_kind") or "").strip()
    status = str(payload.get("status") or "").strip().upper()

    if not candidate_id:
        return False, "OWNER_CANDIDATE_ID_REQUIRED", "Owner queue item has no candidate_id."
    if not owner:
        return False, "BIO_OPS_OWNER_REQUIRED", "Owner queue item has no owner organ."
    if owner not in BIO_OPS_OWNER_IMPORT_CHANNELS:
        return False, "BIO_OPS_OWNER_UNKNOWN", owner
    expected_channel = resolve_owner_import_channel(owner, output_kind)
    if expected_channel is None:
        return False, "BIO_OPS_OWNER_CHANNEL_UNSUPPORTED", f"owner={owner} output_kind={output_kind}"
    if channel_id != str(expected_channel.get("channel_id") or ""):
        return False, "BIO_OPS_OWNER_CHANNEL_MISMATCH", f"expected={expected_channel.get('channel_id')} actual={channel_id}"
    if status != OWNER_QUEUE_STATUS_QUEUED:
        return False, "OWNER_QUEUE_STATUS_INVALID", status
    if bool(payload.get("direct_long_term_write")):
        return False, "BIO_OPS_DIRECT_WRITE_FORBIDDEN", "Owner queue items must not request direct long-term writes."
    if bool(payload.get("durable_write_performed")):
        return False, "BIO_OPS_DURABLE_WRITE_ALREADY_PERFORMED", "Owner queue item already claims a durable write."
    if str(payload.get("write_policy") or "").strip() != OWNER_QUEUE_WRITE_POLICY:
        return False, "OWNER_QUEUE_POLICY_INVALID", str(payload.get("write_policy") or "")

    source_paths = [
        str(payload.get("source_packet_path") or ""),
        str(payload.get("source_import_candidate_path") or ""),
    ]
    sensitive_hits = [path for path in source_paths if is_bio_ops_sensitive_path(path)]
    if sensitive_hits:
        return False, "BIO_OPS_SENSITIVE_TARGET_BLOCKED", ", ".join(sorted(set(sensitive_hits)))
    return True, None, None


class OwnerCandidateStateStore:
    def __init__(
        self,
        *,
        queue_root: str | Path,
        state_root: str | Path | None = None,
        artifact_store: BioOpsArtifactStore | None = None,
    ) -> None:
        self.queue_root = Path(queue_root).expanduser()
        self.state_root = Path(state_root).expanduser() if state_root else DEFAULT_OWNER_CANDIDATE_STATE_ROOT
        self.artifact_store = artifact_store or BioOpsArtifactStore(root=self.state_root)

    def import_queue_item(self, queue_item_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        path = Path(queue_item_path).expanduser()
        item = _read_json(path)
        allowed, reason_code, detail = validate_owner_queue_item_for_durable_import(item)
        if not allowed:
            return {
                "ok": False,
                "status": OWNER_CANDIDATE_STATUS_BLOCKED,
                "queue_item_path": str(path),
                "reason_code": reason_code,
                "detail": detail,
                "direct_long_term_write": False,
                "durable_write_performed": False,
                "owner_candidate_state_write_performed": False,
            }

        owner = str(item.get("owner_organ") or "").strip()
        channel_id = str(item.get("channel_id") or "").strip()
        candidate_id = str(item.get("candidate_id") or "").strip()
        state_path = _owner_candidate_state_path(
            self.state_root,
            owner_organ=owner,
            channel_id=channel_id,
            candidate_id=candidate_id,
        )
        if state_path.exists():
            existing = _read_json(state_path)
            return {
                "ok": True,
                "status": OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED,
                "candidate_id": candidate_id,
                "owner_organ": owner,
                "channel_id": channel_id,
                "state_path": str(state_path),
                "state": existing,
                "dry_run": bool(dry_run),
                "direct_long_term_write": False,
                "durable_write_performed": False,
                "owner_candidate_state_write_performed": False,
            }

        record = OwnerCandidateState(
            candidate_id=candidate_id,
            owner_organ=owner,
            state_owner=owner,
            channel_id=channel_id,
            capability=str(item.get("capability") or "").strip(),
            output_kind=str(item.get("output_kind") or "").strip(),
            summary=str(item.get("summary") or "").strip(),
            source_queue_item_path=str(path),
            source_packet_path=str(item.get("source_packet_path") or "").strip(),
            source_import_candidate_path=str(item.get("source_import_candidate_path") or "").strip(),
        ).to_dict()
        if dry_run:
            return {
                "ok": True,
                "status": OWNER_CANDIDATE_STATUS_IMPORTED,
                "candidate_id": candidate_id,
                "owner_organ": owner,
                "channel_id": channel_id,
                "state_path": str(state_path),
                "state": record,
                "dry_run": True,
                "direct_long_term_write": False,
                "durable_write_performed": False,
                "owner_candidate_state_write_performed": False,
            }

        artifact = self.artifact_store.write_json(state_path, record, artifact_type="owner_candidate_state")
        receipt = {
            "ok": True,
            "status": OWNER_CANDIDATE_STATUS_IMPORTED,
            "candidate_id": candidate_id,
            "owner_organ": owner,
            "channel_id": channel_id,
            "state_path": str(state_path),
            "artifact": artifact.to_dict(),
            "direct_long_term_write": False,
            "durable_write_performed": False,
            "durable_memory_write_performed": False,
            "owner_candidate_state_write_performed": True,
            "write_policy": OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
            "created_at_ms": now_ms(),
        }
        self.artifact_store.write_json(
            self.state_root / sanitize_segment(owner, "owner") / "last_import_receipt.json",
            receipt,
            artifact_type="owner_candidate_import_receipt",
        )
        return {**receipt, "state": record}

    def import_all(self, *, owner_organ: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        imported: list[dict[str, Any]] = []
        already_imported: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        source_count = 0
        if self.queue_root.exists():
            for path in sorted(self.queue_root.rglob("queue_item.json")):
                item = _read_json(path)
                owner = str(item.get("owner_organ") or "").strip()
                if owner_filter and owner != owner_filter:
                    continue
                source_count += 1
                receipt = self.import_queue_item(path, dry_run=dry_run)
                if not receipt.get("ok"):
                    blocked.append(receipt)
                elif receipt.get("status") == OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED:
                    already_imported.append(receipt)
                else:
                    imported.append(receipt)

        manifest = {
            "ok": True,
            "queue_root": str(self.queue_root),
            "state_root": str(self.state_root),
            "owner_organ": owner_filter or None,
            "dry_run": bool(dry_run),
            "source_queue_count": source_count,
            "imported_count": len(imported),
            "already_imported_count": len(already_imported),
            "blocked_count": len(blocked),
            "imported": imported,
            "already_imported": already_imported,
            "blocked": blocked,
            "direct_long_term_write": False,
            "durable_write_performed": False,
            "durable_memory_write_performed": False,
            "owner_candidate_state_write_performed": bool(imported) and not dry_run,
            "write_policy": OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
            "created_at_ms": now_ms(),
        }
        if not dry_run:
            artifact = self.artifact_store.write_json(
                self.state_root / "owner_candidate_import_manifest.json",
                manifest,
                artifact_type="owner_candidate_import_manifest",
            )
            manifest["artifact"] = artifact.to_dict()
        return manifest

    def scan_state(self, *, owner_organ: str | None = None) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        items: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        if self.state_root.exists():
            for path in sorted(self.state_root.rglob("owner_candidate_state.json")):
                state = _read_json(path)
                owner = str(state.get("owner_organ") or "").strip()
                if owner_filter and owner != owner_filter:
                    continue
                payload = {**state, "path": str(path)}
                items.append(payload)
                status = str(state.get("status") or "UNKNOWN")
                status_counts[status] = int(status_counts.get(status, 0)) + 1
        return {
            "ok": True,
            "state_root": str(self.state_root),
            "owner_organ": owner_filter or None,
            "count": len(items),
            "status_counts": status_counts,
            "items": items,
            "direct_long_term_write": False,
            "durable_write_performed": False,
            "durable_memory_write_performed": False,
            "write_policy": OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
        }


__all__ = [
    "DEFAULT_OWNER_CANDIDATE_STATE_ROOT",
    "OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED",
    "OWNER_CANDIDATE_STATUS_BLOCKED",
    "OWNER_CANDIDATE_STATUS_IMPORTED",
    "OWNER_DURABLE_CANDIDATE_WRITE_POLICY",
    "OwnerCandidateState",
    "OwnerCandidateStateStore",
    "validate_owner_queue_item_for_durable_import",
]
