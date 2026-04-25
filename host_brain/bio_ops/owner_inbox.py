"""Owner inbox for BioOps adoption decisions.

This layer turns owner decision artifacts into validated import candidates for
the owning organ. It still does not write durable memory, Bond, Canon, persona,
secrets, or production configuration.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from host_brain.bio_ops.adoption import ADOPTION_DECISION_ACCEPTED, ADOPTION_DECISION_REJECTED
from host_brain.bio_ops.artifact_store import BioOpsArtifactStore, DEFAULT_BIO_OPS_ROOT
from host_brain.bio_ops.safety import BIO_OPS_OWNER_ORGANS, is_bio_ops_sensitive_path
from host_brain.codex_runtime.artifact_store import REPO_ROOT, sanitize_segment
from host_brain.core.ids import now_ms


DEFAULT_OWNER_INBOX_ROOT = REPO_ROOT / "artifacts" / "bio_ops_owner_inbox"
OWNER_INBOX_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
OWNER_INBOX_STATUS_READY = "READY_FOR_OWNER_IMPORT"
OWNER_INBOX_STATUS_REJECTED = "REJECTED_BY_OWNER"
OWNER_INBOX_STATUS_BLOCKED = "BLOCKED"
OWNER_IMPORT_STATUS_READY = "READY_FOR_ORGAN_CONSUMPTION"
OWNER_IMPORT_STATUS_BLOCKED = "BLOCKED"
OWNER_INBOX_STATUSES = {
    OWNER_INBOX_STATUS_PENDING_REVIEW,
    OWNER_INBOX_STATUS_READY,
    OWNER_INBOX_STATUS_REJECTED,
    OWNER_INBOX_STATUS_BLOCKED,
}


@dataclass(slots=True)
class BioOpsOwnerInboxItem:
    candidate_id: str
    capability: str
    owner_organ: str
    output_kind: str
    status: str
    import_allowed: bool
    summary: str
    evidence_refs: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    source_candidate_path: str = ""
    decision_path: str = ""
    decision: str | None = None
    reason_code: str | None = None
    detail: str | None = None
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsOwnerImportItem:
    candidate_id: str
    capability: str
    owner_organ: str
    output_kind: str
    status: str
    summary: str
    path: str
    import_allowed: bool = True
    reason_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsOwnerIntakePacket:
    packet_id: str
    owner_organ: str
    item_count: int
    import_candidates: list[dict[str, Any]]
    direct_long_term_write: bool = False
    write_policy: str = "owner_intake_packet_only"
    created_at_ms: int = field(default_factory=now_ms)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _relative_ref(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _candidate_id(candidate: dict[str, Any], source_path: Path) -> str:
    raw = str(candidate.get("candidate_id") or "").strip()
    if raw:
        return raw
    return sanitize_segment(source_path.parent.name, "candidate")


def _sensitive_hits(value: Any, *, key_active: bool = False) -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key or "").lower()
            active = key_active or any(token in normalized for token in ("target", "path", "file", "secret", "bond", "canon", "personality"))
            hits.extend(_sensitive_hits(child, key_active=active))
        return hits
    if isinstance(value, list):
        for item in value:
            hits.extend(_sensitive_hits(item, key_active=key_active))
        return hits
    if key_active and is_bio_ops_sensitive_path(value):
        hits.append(str(value))
    return hits


def validate_owner_import_candidate(candidate: dict[str, Any], decision: dict[str, Any]) -> tuple[str, bool, str | None, str | None]:
    candidate = _as_dict(candidate)
    decision = _as_dict(decision)
    if not decision:
        return OWNER_INBOX_STATUS_PENDING_REVIEW, False, None, None

    decision_value = str(decision.get("decision") or "").strip().upper()
    if decision_value == ADOPTION_DECISION_REJECTED:
        return OWNER_INBOX_STATUS_REJECTED, False, "BIO_OPS_OWNER_REJECTED", str(decision.get("reason") or "").strip() or None
    if decision_value != ADOPTION_DECISION_ACCEPTED:
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_OWNER_DECISION_INVALID", decision_value

    owner_organ = str(candidate.get("owner_organ") or "").strip()
    decision_owner = str(decision.get("owner_organ") or "").strip()
    if not owner_organ:
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_OWNER_REQUIRED", "Candidate has no owner organ."
    if owner_organ not in BIO_OPS_OWNER_ORGANS:
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_OWNER_UNKNOWN", owner_organ
    if decision_owner != owner_organ:
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_OWNER_MISMATCH", f"candidate={owner_organ} decision={decision_owner}"
    if not _text_list(candidate.get("evidence_refs")):
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_EVIDENCE_REQUIRED", "Owner import candidates require evidence_refs."

    hits = _sensitive_hits(candidate.get("payload"))
    hits.extend(_sensitive_hits(candidate.get("artifacts")))
    if hits:
        return OWNER_INBOX_STATUS_BLOCKED, False, "BIO_OPS_SENSITIVE_TARGET_BLOCKED", ", ".join(sorted(set(hits)))

    return OWNER_INBOX_STATUS_READY, True, None, None


def validate_owner_import_artifact(payload: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    payload = _as_dict(payload)
    owner_organ = str(payload.get("owner_organ") or "").strip()
    if not owner_organ:
        return False, "BIO_OPS_OWNER_REQUIRED", "Owner import artifact has no owner organ."
    if owner_organ not in BIO_OPS_OWNER_ORGANS:
        return False, "BIO_OPS_OWNER_UNKNOWN", owner_organ
    if bool(payload.get("direct_long_term_write")):
        return False, "BIO_OPS_DIRECT_WRITE_FORBIDDEN", "Owner import artifacts must not write durable state directly."
    if str(payload.get("write_policy") or "").strip() != "owner_import_candidate_only":
        return False, "BIO_OPS_IMPORT_POLICY_INVALID", str(payload.get("write_policy") or "")
    if not _as_dict(payload.get("candidate")):
        return False, "BIO_OPS_IMPORT_CANDIDATE_REQUIRED", "Owner import artifact must include the original candidate."
    if not _as_dict(payload.get("decision")):
        return False, "BIO_OPS_IMPORT_DECISION_REQUIRED", "Owner import artifact must include the owner decision."
    return True, None, None


class BioOpsOwnerInbox:
    def __init__(
        self,
        *,
        source_root: str | Path | None = None,
        inbox_root: str | Path | None = None,
        artifact_store: BioOpsArtifactStore | None = None,
    ) -> None:
        self.source_root = Path(source_root).expanduser() if source_root else DEFAULT_BIO_OPS_ROOT
        self.inbox_root = Path(inbox_root).expanduser() if inbox_root else DEFAULT_OWNER_INBOX_ROOT
        self.artifact_store = artifact_store or BioOpsArtifactStore(root=self.inbox_root)

    def _records(self) -> list[tuple[BioOpsOwnerInboxItem, dict[str, Any], dict[str, Any]]]:
        if not self.source_root.exists():
            return []

        records: list[tuple[BioOpsOwnerInboxItem, dict[str, Any], dict[str, Any]]] = []
        for candidate_path in sorted(self.source_root.rglob("adoption_candidate.json")):
            candidate = _read_json(candidate_path)
            if not candidate:
                continue
            decision_path = candidate_path.parent / "adoption_decision.json"
            decision = _read_json(decision_path)
            status, import_allowed, reason_code, detail = validate_owner_import_candidate(candidate, decision)
            item = BioOpsOwnerInboxItem(
                candidate_id=_candidate_id(candidate, candidate_path),
                capability=str(candidate.get("capability") or "").strip(),
                owner_organ=str(candidate.get("owner_organ") or "").strip(),
                output_kind=str(candidate.get("output_kind") or "").strip(),
                status=status,
                import_allowed=import_allowed,
                summary=str(candidate.get("summary") or "").strip(),
                evidence_refs=_text_list(candidate.get("evidence_refs")),
                risk_flags=[*_text_list(candidate.get("risk_flags")), *(_text_list(decision.get("risk_flags")) if decision else [])],
                next_actions=_text_list(candidate.get("next_actions")),
                source_candidate_path=str(candidate_path),
                decision_path=str(decision_path) if decision else "",
                decision=str(decision.get("decision") or "").strip().upper() or None,
                reason_code=reason_code,
                detail=detail,
                created_at_ms=int(candidate.get("created_at_ms") or 0) or now_ms(),
            )
            records.append((item, candidate, decision))
        return records

    def scan(self, *, owner_organ: str | None = None, status: str = "all") -> dict[str, Any]:
        normalized_status = str(status or "all").strip().upper()
        if normalized_status != "ALL" and normalized_status not in OWNER_INBOX_STATUSES:
            raise ValueError("status must be one of ALL, PENDING_REVIEW, READY_FOR_OWNER_IMPORT, REJECTED_BY_OWNER, BLOCKED")

        owner_filter = str(owner_organ or "").strip()
        items: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        for item, _, _ in self._records():
            if owner_filter and item.owner_organ != owner_filter:
                continue
            if normalized_status != "ALL" and item.status != normalized_status:
                continue
            payload = item.to_dict()
            items.append(payload)
            status_counts[item.status] = int(status_counts.get(item.status, 0)) + 1

        return {
            "ok": True,
            "source_root": str(self.source_root),
            "inbox_root": str(self.inbox_root),
            "owner_organ": owner_filter or None,
            "status": normalized_status,
            "count": len(items),
            "status_counts": status_counts,
            "items": items,
        }

    def materialize_ready(self, *, owner_organ: str | None = None) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        artifacts: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for item, candidate, decision in self._records():
            if owner_filter and item.owner_organ != owner_filter:
                continue
            if item.status != OWNER_INBOX_STATUS_READY:
                skipped.append({"candidate_id": item.candidate_id, "status": item.status, "reason_code": item.reason_code})
                continue

            target = (
                self.inbox_root
                / sanitize_segment(item.owner_organ, "owner")
                / sanitize_segment(item.candidate_id, "candidate")
                / "owner_import_candidate.json"
            )
            artifact = self.artifact_store.write_json(
                target,
                {
                    "candidate_id": item.candidate_id,
                    "owner_organ": item.owner_organ,
                    "capability": item.capability,
                    "output_kind": item.output_kind,
                    "summary": item.summary,
                    "candidate": candidate,
                    "decision": decision,
                    "source_candidate_path": item.source_candidate_path,
                    "decision_path": item.decision_path,
                    "direct_long_term_write": False,
                    "write_policy": "owner_import_candidate_only",
                    "created_at_ms": now_ms(),
                    "next_actions": [
                        f"{item.owner_organ} may import this candidate through its own durable state path.",
                        "Do not treat this inbox artifact as a completed memory, Bond, Canon, or persona write.",
                    ],
                },
                artifact_type="owner_import_candidate",
            )
            artifacts.append(artifact.to_dict())

        return {
            "ok": True,
            "source_root": str(self.source_root),
            "inbox_root": str(self.inbox_root),
            "owner_organ": owner_filter or None,
            "materialized_count": len(artifacts),
            "skipped_count": len(skipped),
            "artifacts": artifacts,
            "skipped": skipped,
        }

    def scan_import_candidates(self, *, owner_organ: str | None = None) -> dict[str, Any]:
        owner_filter = str(owner_organ or "").strip()
        items: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}

        if self.inbox_root.exists():
            for path in sorted(self.inbox_root.rglob("owner_import_candidate.json")):
                payload = _read_json(path)
                owner = str(payload.get("owner_organ") or "").strip()
                if owner_filter and owner != owner_filter:
                    continue
                allowed, reason_code, detail = validate_owner_import_artifact(payload)
                status = OWNER_IMPORT_STATUS_READY if allowed else OWNER_IMPORT_STATUS_BLOCKED
                item = BioOpsOwnerImportItem(
                    candidate_id=str(payload.get("candidate_id") or "").strip() or sanitize_segment(path.parent.name, "candidate"),
                    capability=str(payload.get("capability") or "").strip(),
                    owner_organ=owner,
                    output_kind=str(payload.get("output_kind") or "").strip(),
                    status=status,
                    summary=str(payload.get("summary") or "").strip(),
                    path=str(path),
                    import_allowed=allowed,
                    reason_code=reason_code,
                    detail=detail,
                )
                items.append(item.to_dict())
                status_counts[status] = int(status_counts.get(status, 0)) + 1

        return {
            "ok": True,
            "inbox_root": str(self.inbox_root),
            "owner_organ": owner_filter or None,
            "count": len(items),
            "status_counts": status_counts,
            "items": items,
        }

    def materialize_intake_packets(self, *, owner_organ: str | None = None) -> dict[str, Any]:
        scan = self.scan_import_candidates(owner_organ=owner_organ)
        grouped: dict[str, list[dict[str, Any]]] = {}
        blocked: list[dict[str, Any]] = []
        for item in scan["items"]:
            if not item.get("import_allowed"):
                blocked.append(item)
                continue
            grouped.setdefault(str(item.get("owner_organ") or ""), []).append(item)

        artifacts: list[dict[str, Any]] = []
        packets: list[dict[str, Any]] = []
        for owner, items in sorted(grouped.items()):
            if not owner:
                blocked.extend([{**item, "reason_code": "BIO_OPS_OWNER_REQUIRED"} for item in items])
                continue
            packet = BioOpsOwnerIntakePacket(
                packet_id=f"owner_intake_{sanitize_segment(owner)}",
                owner_organ=owner,
                item_count=len(items),
                import_candidates=[
                    {
                        "candidate_id": item["candidate_id"],
                        "capability": item["capability"],
                        "output_kind": item["output_kind"],
                        "summary": item["summary"],
                        "path": item["path"],
                        "relative_ref": _relative_ref(Path(item["path"]), self.inbox_root),
                    }
                    for item in items
                ],
                next_actions=[
                    f"{owner} should load only its own packet.",
                    "Owner import packets are handoff artifacts; durable writes remain owner-specific.",
                ],
            )
            target = self.inbox_root / sanitize_segment(owner, "owner") / "owner_intake_packet.json"
            artifact = self.artifact_store.write_json(target, packet.to_dict(), artifact_type="owner_intake_packet")
            artifacts.append(artifact.to_dict())
            packets.append(packet.to_dict())

        return {
            "ok": True,
            "inbox_root": str(self.inbox_root),
            "owner_organ": str(owner_organ or "").strip() or None,
            "packet_count": len(packets),
            "blocked_count": len(blocked),
            "packets": packets,
            "artifacts": artifacts,
            "blocked": blocked,
        }


__all__ = [
    "BioOpsOwnerInbox",
    "BioOpsOwnerInboxItem",
    "BioOpsOwnerImportItem",
    "BioOpsOwnerIntakePacket",
    "DEFAULT_OWNER_INBOX_ROOT",
    "OWNER_INBOX_STATUS_BLOCKED",
    "OWNER_INBOX_STATUS_PENDING_REVIEW",
    "OWNER_INBOX_STATUS_READY",
    "OWNER_INBOX_STATUS_REJECTED",
    "OWNER_IMPORT_STATUS_BLOCKED",
    "OWNER_IMPORT_STATUS_READY",
    "validate_owner_import_candidate",
    "validate_owner_import_artifact",
]
