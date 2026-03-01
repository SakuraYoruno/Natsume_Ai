"""Artifact Vault with manifest/undo/GC audit contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import MANIFEST_INVALID, TOKEN_INVALID, UNDO_REQUIRED
from host_brain.core.ids import now_ms
from host_brain.governor.tokens import TokenService


S_VAULT_MANIFEST = "S_VAULT_MANIFEST"
S_VAULT_UNDO = "S_VAULT_UNDO"
S_VAULT_GC = "S_VAULT_GC"


@dataclass(frozen=True)
class VaultResult:
    success: bool
    reason_code: str | None
    detail: str
    manifest_ref: str | None = None
    manifest_id: str | None = None


class ArtifactVault:
    """Single-writer vault for reversible artifacts and manifest indexing."""

    def __init__(
        self,
        *,
        root_dir: str | Path = "host_brain/data/vault",
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.artifacts_dir = self.root_dir / "artifacts"
        self.manifests_dir = self.root_dir / "manifests"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or SQLiteLedger()

    def create_manifest(
        self,
        *,
        intent_id: str,
        items: list[dict[str, Any]],
        evidence_refs: list[str],
        policy_profile_id: str,
        request_id: str | None = None,
        undo_ref: str | None = None,
        reversible: bool = False,
        skill_name: str | None = None,
        session_id: str | None = None,
        seq: int | None = None,
        ts_ms: int | None = None,
    ) -> VaultResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_refs(evidence_refs)
        if not refs:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_MANIFEST,
                reason_code=MANIFEST_INVALID,
                detail="manifest requires non-empty evidence_refs",
            )
            return VaultResult(False, MANIFEST_INVALID, "manifest requires non-empty evidence_refs")

        if reversible and not undo_ref:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_MANIFEST,
                reason_code=UNDO_REQUIRED,
                detail="reversible manifest requires undo_ref",
            )
            return VaultResult(False, UNDO_REQUIRED, "reversible manifest requires undo_ref")

        if not items:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_MANIFEST,
                reason_code=MANIFEST_INVALID,
                detail="manifest requires at least one item",
            )
            return VaultResult(False, MANIFEST_INVALID, "manifest requires at least one item")

        manifest_id = f"manifest_{uuid.uuid4().hex}"
        built_items: list[dict[str, Any]] = []
        for item in items:
            built = self._create_artifact_entry(item=item, fallback_refs=refs, ts_ms=ts_value)
            built_items.append(built)

        payload = {
            "manifest_id": manifest_id,
            "intent_id": intent_id,
            "items": built_items,
            "policy_profile_id": policy_profile_id,
            "undo_ref": undo_ref,
            "reversible": bool(reversible),
            "skill_name": skill_name,
            "session_id": session_id,
            "seq": int(seq) if seq is not None else None,
            "ts_ms": ts_value,
        }
        integrity_digest = _manifest_digest(payload)
        payload["integrity_digest"] = integrity_digest

        manifest_path = self.manifests_dir / f"{manifest_id}.json"
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.ledger.log_vault_manifest(
            {
                "request_id": request_id,
                "manifest_id": manifest_id,
                "intent_id": intent_id,
                "integrity_digest": integrity_digest,
                "manifest_path": str(manifest_path),
                "items_count": len(built_items),
                "evidence_refs": refs,
                "policy_profile_id": policy_profile_id,
                "undo_ref": undo_ref,
                "reversible": int(bool(reversible)),
                "skill_name": skill_name,
                "session_id": session_id,
                "seq": int(seq) if seq is not None else None,
                "status": "ACTIVE",
                "ts_ms": ts_value,
            }
        )

        return VaultResult(
            success=True,
            reason_code=None,
            detail="manifest created",
            manifest_ref=f"manifest::{manifest_id}",
            manifest_id=manifest_id,
        )

    def undo_manifest(
        self,
        *,
        manifest_ref: str,
        undo_token_ref: str,
        token_service: TokenService,
        request_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> VaultResult:
        ts_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        manifest_id = _parse_manifest_ref(manifest_ref)
        if not manifest_id:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_UNDO,
                reason_code=MANIFEST_INVALID,
                detail=f"invalid manifest_ref: {manifest_ref}",
            )
            return VaultResult(False, MANIFEST_INVALID, "invalid manifest_ref")

        payload = self._read_manifest(manifest_id)
        if payload is None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_UNDO,
                reason_code=MANIFEST_INVALID,
                detail=f"manifest not found: {manifest_id}",
            )
            return VaultResult(False, MANIFEST_INVALID, "manifest not found")

        if not self._verify_manifest_digest(payload):
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_UNDO,
                reason_code=MANIFEST_INVALID,
                detail=f"manifest digest mismatch: {manifest_id}",
            )
            self._log_manifest_status(payload, request_id=request_id, status="UNDO_REJECTED_DIGEST", ts_ms=ts_value)
            return VaultResult(False, MANIFEST_INVALID, "manifest digest mismatch", manifest_id=manifest_id)

        session_id = str(payload.get("session_id") or "")
        seq_value = int(payload.get("seq") or 0)
        skill_name = str(payload.get("skill_name") or "")

        token_result = token_service.validate_bound_token(
            undo_token_ref,
            expected_type="undo",
            intent_id=str(payload.get("intent_id") or ""),
            skill_name=skill_name,
            session_id=session_id,
            seq=seq_value,
            now_ts_ms=ts_value,
            consume=True,
        )
        if not token_result.valid:
            code = token_result.reason_code or TOKEN_INVALID
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VAULT_UNDO,
                reason_code=code,
                detail=token_result.detail or "undo token validation failed",
            )
            self._log_manifest_status(payload, request_id=request_id, status="UNDO_REJECTED_TOKEN", ts_ms=ts_value)
            return VaultResult(False, code, token_result.detail or "undo token validation failed", manifest_id=manifest_id)

        self._log_manifest_status(payload, request_id=request_id, status="UNDONE", ts_ms=ts_value)
        return VaultResult(True, None, "manifest undo accepted", manifest_id=manifest_id, manifest_ref=manifest_ref)

    def garbage_collect(
        self,
        *,
        policy_profile_id: str,
        evidence_refs: list[str],
        retention_ms: int,
        request_id: str | None = None,
        token_service: TokenService | None = None,
        now_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        ts_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        refs = _normalize_refs(evidence_refs)

        deleted_manifest_ids: list[str] = []
        deleted_artifact_ids: list[str] = []
        blocked_manifest_ids: list[str] = []

        for manifest_path in sorted(self.manifests_dir.glob("manifest_*.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - defensive guard
                self.ledger.log_error(
                    request_id=request_id,
                    stage=S_VAULT_GC,
                    reason_code=MANIFEST_INVALID,
                    detail=f"invalid manifest file {manifest_path}: {exc}",
                )
                continue

            age_ms = ts_value - int(payload.get("ts_ms") or 0)
            if age_ms < int(retention_ms):
                continue

            if self._has_active_undo_window(payload, token_service=token_service, now_ts_ms=ts_value):
                blocked_manifest_ids.append(str(payload.get("manifest_id") or ""))
                continue

            for item in payload.get("items") or []:
                artifact_id = str(item.get("artifact_id") or "")
                artifact_path = self.artifacts_dir / f"{artifact_id}.txt"
                if artifact_path.exists():
                    artifact_path.unlink()
                    deleted_artifact_ids.append(artifact_id)

            manifest_path.unlink(missing_ok=True)
            deleted_manifest_ids.append(str(payload.get("manifest_id") or ""))

        status = "SUCCESS"
        if blocked_manifest_ids and not deleted_manifest_ids:
            status = "NOOP_BLOCKED"
        elif blocked_manifest_ids and deleted_manifest_ids:
            status = "PARTIAL_BLOCKED"

        self.ledger.log_vault_gc(
            {
                "request_id": request_id,
                "policy_profile_id": policy_profile_id,
                "deleted_manifest_ids": deleted_manifest_ids,
                "deleted_artifact_ids": deleted_artifact_ids,
                "blocked_manifest_ids": blocked_manifest_ids,
                "evidence_refs": refs,
                "status": status,
                "ts_ms": ts_value,
            }
        )

        return {
            "status": status,
            "deleted_manifest_ids": deleted_manifest_ids,
            "deleted_artifact_ids": deleted_artifact_ids,
            "blocked_manifest_ids": blocked_manifest_ids,
        }

    def _create_artifact_entry(
        self,
        *,
        item: dict[str, Any],
        fallback_refs: list[str],
        ts_ms: int,
    ) -> dict[str, Any]:
        artifact_id = str(item.get("artifact_id") or f"artifact_{uuid.uuid4().hex}")
        kind = str(item.get("kind") or "generic")
        content = str(item.get("content") or "")
        item_refs = _normalize_refs(item.get("evidence_refs")) or list(fallback_refs)

        artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        artifact_path = self.artifacts_dir / f"{artifact_id}.txt"
        artifact_path.write_text(content, encoding="utf-8")

        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "hash": artifact_hash,
            "created_ts": ts_ms,
            "evidence_refs": item_refs,
        }

    def _read_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        path = self.manifests_dir / f"{manifest_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _verify_manifest_digest(self, payload: dict[str, Any]) -> bool:
        actual = str(payload.get("integrity_digest") or "")
        if not actual:
            return False
        expected = _manifest_digest({k: v for k, v in payload.items() if k != "integrity_digest"})
        return actual == expected

    def _log_manifest_status(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None,
        status: str,
        ts_ms: int,
    ) -> None:
        self.ledger.log_vault_manifest(
            {
                "request_id": request_id,
                "manifest_id": payload.get("manifest_id"),
                "intent_id": payload.get("intent_id"),
                "integrity_digest": payload.get("integrity_digest"),
                "manifest_path": str(self.manifests_dir / f"{payload.get('manifest_id')}.json"),
                "items_count": len(payload.get("items") or []),
                "evidence_refs": _collect_manifest_refs(payload),
                "policy_profile_id": payload.get("policy_profile_id"),
                "undo_ref": payload.get("undo_ref"),
                "reversible": int(bool(payload.get("reversible"))),
                "skill_name": payload.get("skill_name"),
                "session_id": payload.get("session_id"),
                "seq": payload.get("seq"),
                "status": status,
                "ts_ms": ts_ms,
            }
        )

    def _has_active_undo_window(
        self,
        payload: dict[str, Any],
        *,
        token_service: TokenService | None,
        now_ts_ms: int,
    ) -> bool:
        if not bool(payload.get("reversible")):
            return False

        undo_ref = str(payload.get("undo_ref") or "")
        if not undo_ref:
            return False

        if token_service is None:
            return True

        snapshot = token_service.export_snapshot()
        token = snapshot.get(undo_ref)
        if token is None:
            return False

        expiry = int(token.get("expiry_ts_ms") or 0)
        used = bool(token.get("used"))
        return (not used) and expiry > now_ts_ms


def _manifest_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_manifest_ref(manifest_ref: str) -> str:
    value = str(manifest_ref).strip()
    if not value:
        return ""
    if value.startswith("manifest::"):
        return value.split("::", 1)[1]
    return value


def _normalize_refs(refs: Any) -> list[str]:
    if not refs:
        return []
    if not isinstance(refs, list):
        return []
    normalized: list[str] = []
    for item in refs:
        value = str(item).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _collect_manifest_refs(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in payload.get("items") or []:
        item_refs = _normalize_refs(item.get("evidence_refs"))
        for ref in item_refs:
            if ref not in refs:
                refs.append(ref)
    return refs
