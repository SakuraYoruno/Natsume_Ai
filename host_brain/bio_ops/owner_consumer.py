"""Owner-side consumers for BioOps intake packets.

The consumer moves validated owner intake packets into per-organ candidate
queues. It is intentionally still artifact-only: no durable memory, Bond,
Canon, persona, secret, or production configuration writes happen here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.owner_inbox import BIO_OPS_OWNER_IMPORT_CHANNELS, resolve_owner_import_channel
from host_brain.codex_runtime.artifact_store import REPO_ROOT, sanitize_segment
from host_brain.core.ids import now_ms


DEFAULT_OWNER_QUEUE_ROOT = REPO_ROOT / "artifacts" / "bio_ops_owner_queues"
OWNER_QUEUE_STATUS_QUEUED = "QUEUED_FOR_OWNER"
OWNER_QUEUE_STATUS_BLOCKED = "BLOCKED"
OWNER_QUEUE_WRITE_POLICY = "owner_queue_candidate_only"


@dataclass(slots=True)
class BioOpsOwnerQueueItem:
    candidate_id: str
    owner_organ: str
    channel_id: str
    capability: str
    output_kind: str
    summary: str
    source_packet_path: str
    source_import_candidate_path: str
    status: str = OWNER_QUEUE_STATUS_QUEUED
    direct_long_term_write: bool = False
    durable_write_performed: bool = False
    write_policy: str = OWNER_QUEUE_WRITE_POLICY
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _owner_queue_item_path(queue_root: Path, *, owner_organ: str, channel_id: str, candidate_id: str) -> Path:
    return (
        queue_root
        / sanitize_segment(owner_organ, "owner")
        / sanitize_segment(channel_id, "channel")
        / sanitize_segment(candidate_id, "candidate")
        / "queue_item.json"
    )


def validate_owner_intake_packet(packet: dict[str, Any], *, expected_owner: str | None = None) -> tuple[bool, str | None, str | None]:
    payload = _as_dict(packet)
    owner = str(payload.get("owner_organ") or "").strip()
    if not owner:
        return False, "BIO_OPS_OWNER_REQUIRED", "Owner intake packet has no owner organ."
    if expected_owner and owner != str(expected_owner or "").strip():
        return False, "BIO_OPS_OWNER_MISMATCH", f"expected={expected_owner} actual={owner}"
    if owner not in BIO_OPS_OWNER_IMPORT_CHANNELS:
        return False, "BIO_OPS_OWNER_UNKNOWN", owner
    if bool(payload.get("direct_long_term_write")):
        return False, "BIO_OPS_DIRECT_WRITE_FORBIDDEN", "Owner intake packets must not perform durable writes."
    if str(payload.get("write_policy") or "").strip() != "owner_intake_packet_only":
        return False, "BIO_OPS_INTAKE_POLICY_INVALID", str(payload.get("write_policy") or "")

    candidates = [item for item in _as_list(payload.get("import_candidates")) if isinstance(item, dict)]
    if int(payload.get("item_count") or len(candidates)) != len(candidates):
        return False, "BIO_OPS_INTAKE_COUNT_MISMATCH", f"item_count={payload.get('item_count')} actual={len(candidates)}"
    for item in candidates:
        candidate_owner = str(_as_dict(item.get("owner_channel")).get("owner_organ") or owner).strip()
        output_kind = str(item.get("output_kind") or "").strip()
        if candidate_owner != owner:
            return False, "BIO_OPS_OWNER_MISMATCH", f"packet={owner} candidate={candidate_owner}"
        if resolve_owner_import_channel(owner, output_kind) is None:
            return False, "BIO_OPS_OWNER_CHANNEL_UNSUPPORTED", f"owner={owner} output_kind={output_kind}"
    return True, None, None


class BioOpsOwnerIntakeConsumer:
    def __init__(
        self,
        *,
        inbox_root: str | Path,
        queue_root: str | Path | None = None,
        artifact_store: BioOpsArtifactStore | None = None,
    ) -> None:
        self.inbox_root = Path(inbox_root).expanduser()
        self.queue_root = Path(queue_root).expanduser() if queue_root else DEFAULT_OWNER_QUEUE_ROOT
        self.artifact_store = artifact_store or BioOpsArtifactStore(root=self.queue_root)

    def consume_packet(self, packet_path: str | Path, *, owner_organ: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        path = Path(packet_path).expanduser()
        packet = _read_json(path)
        allowed, reason, detail = validate_owner_intake_packet(packet, expected_owner=owner_organ)
        if not allowed:
            return {
                "ok": False,
                "status": OWNER_QUEUE_STATUS_BLOCKED,
                "packet_path": str(path),
                "owner_organ": owner_organ,
                "reason_code": reason,
                "detail": detail,
                "direct_long_term_write": False,
                "durable_write_performed": False,
            }

        owner = str(packet.get("owner_organ") or "").strip()
        queued: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for item in _as_list(packet.get("import_candidates")):
            candidate = _as_dict(item)
            channel = resolve_owner_import_channel(owner, str(candidate.get("output_kind") or ""))
            if channel is None:
                skipped.append({"candidate_id": candidate.get("candidate_id"), "reason_code": "BIO_OPS_OWNER_CHANNEL_UNSUPPORTED"})
                continue
            queue_item = BioOpsOwnerQueueItem(
                candidate_id=str(candidate.get("candidate_id") or "").strip(),
                owner_organ=owner,
                channel_id=str(channel["channel_id"]),
                capability=str(candidate.get("capability") or "").strip(),
                output_kind=str(candidate.get("output_kind") or "").strip(),
                summary=str(candidate.get("summary") or "").strip(),
                source_packet_path=str(path),
                source_import_candidate_path=str(candidate.get("path") or "").strip(),
            )
            payload = queue_item.to_dict()
            if not dry_run:
                target = _owner_queue_item_path(
                    self.queue_root,
                    owner_organ=queue_item.owner_organ,
                    channel_id=queue_item.channel_id,
                    candidate_id=queue_item.candidate_id,
                )
                artifact = self.artifact_store.write_json(target, payload, artifact_type="owner_queue_item")
                payload["artifact"] = artifact.to_dict()
            queued.append(payload)

        receipt = {
            "ok": True,
            "status": OWNER_QUEUE_STATUS_QUEUED,
            "packet_id": packet.get("packet_id"),
            "packet_path": str(path),
            "owner_organ": owner,
            "dry_run": bool(dry_run),
            "queued_count": len(queued),
            "skipped_count": len(skipped),
            "queue_root": str(self.queue_root),
            "queue_items": queued,
            "skipped": skipped,
            "direct_long_term_write": False,
            "durable_write_performed": False,
            "write_policy": OWNER_QUEUE_WRITE_POLICY,
            "created_at_ms": now_ms(),
        }
        if not dry_run:
            receipt_artifact = self.artifact_store.write_json(
                self.queue_root / sanitize_segment(owner, "owner") / "last_consume_receipt.json",
                receipt,
                artifact_type="owner_consume_receipt",
            )
            receipt["artifact"] = receipt_artifact.to_dict()
        return receipt

    def consume_all(self, *, owner_organ: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        receipts: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        if self.inbox_root.exists():
            for packet_path in sorted(self.inbox_root.rglob("owner_intake_packet.json")):
                receipt = self.consume_packet(packet_path, owner_organ=owner_filter or None, dry_run=dry_run)
                if receipt.get("ok"):
                    receipts.append(receipt)
                else:
                    blocked.append(receipt)
        return {
            "ok": True,
            "inbox_root": str(self.inbox_root),
            "queue_root": str(self.queue_root),
            "owner_organ": owner_filter or None,
            "dry_run": bool(dry_run),
            "packet_count": len(receipts),
            "blocked_count": len(blocked),
            "queued_count": sum(int(item.get("queued_count") or 0) for item in receipts),
            "receipts": receipts,
            "blocked": blocked,
            "direct_long_term_write": False,
            "durable_write_performed": False,
        }

    def scan_queue(self, *, owner_organ: str | None = None) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        items: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        if self.queue_root.exists():
            for path in sorted(self.queue_root.rglob("queue_item.json")):
                item = _read_json(path)
                owner = str(item.get("owner_organ") or "").strip()
                if owner_filter and owner != owner_filter:
                    continue
                payload = {**item, "path": str(path)}
                items.append(payload)
                status = str(item.get("status") or "UNKNOWN")
                status_counts[status] = int(status_counts.get(status, 0)) + 1
        return {
            "ok": True,
            "queue_root": str(self.queue_root),
            "owner_organ": owner_filter or None,
            "count": len(items),
            "status_counts": status_counts,
            "items": items,
        }


__all__ = [
    "BioOpsOwnerIntakeConsumer",
    "BioOpsOwnerQueueItem",
    "DEFAULT_OWNER_QUEUE_ROOT",
    "OWNER_QUEUE_STATUS_BLOCKED",
    "OWNER_QUEUE_STATUS_QUEUED",
    "OWNER_QUEUE_WRITE_POLICY",
    "validate_owner_intake_packet",
]
