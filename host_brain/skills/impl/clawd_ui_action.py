"""ClawdBot UI action guardrails with auditable before/after evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import MANIFEST_INVALID, UI_CONFIDENCE_LOW, UI_ELEMENT_NOT_FOUND, VISUAL_REDLINE
from host_brain.core.ids import now_ms
from host_brain.lsp.vault import ArtifactVault


S_CLAWD_UI_GUARD = "S_CLAWD_UI_GUARD"
S_CLAWD_UI_EXEC = "S_CLAWD_UI_EXEC"

STATUS_REJECTED = "REJECTED"
STATUS_EXECUTED = "EXECUTED"

MIN_CONFIDENCE = 0.80


@dataclass(frozen=True)
class ClawdUIActionResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    request_id: str
    intent_id: str
    target_desc: str
    action_name: str
    confidence: float
    before_artifact_ref: str | None
    after_artifact_ref: str | None
    manifest_ref: str | None


def execute_clawd_ui_action(
    *,
    request_id: str,
    intent_id: str,
    target_desc: str,
    action_name: str,
    confidence: float,
    element_found: bool,
    redline_detected: bool,
    before_snapshot: str,
    after_snapshot: str,
    ledger: SQLiteLedger,
    vault: ArtifactVault,
    evidence_refs: list[str] | None = None,
    policy_profile_id: str = "vault_default",
    ts_ms: int | None = None,
) -> ClawdUIActionResult:
    """Apply ClawdBot UI action with deterministic safety gates."""

    ts_value = int(ts_ms if ts_ms is not None else now_ms())
    conf_value = float(confidence)
    refs = _normalize_refs(evidence_refs, intent_id=intent_id, request_id=request_id)

    if redline_detected:
        return _log_reject(
            request_id=request_id,
            intent_id=intent_id,
            target_desc=target_desc,
            action_name=action_name,
            confidence=conf_value,
            reason_code=VISUAL_REDLINE,
            detail="redline scene blocked",
            ledger=ledger,
            ts_ms=ts_value,
        )

    if not element_found:
        return _log_reject(
            request_id=request_id,
            intent_id=intent_id,
            target_desc=target_desc,
            action_name=action_name,
            confidence=conf_value,
            reason_code=UI_ELEMENT_NOT_FOUND,
            detail="target UI element not found",
            ledger=ledger,
            ts_ms=ts_value,
        )

    if conf_value < MIN_CONFIDENCE:
        return _log_reject(
            request_id=request_id,
            intent_id=intent_id,
            target_desc=target_desc,
            action_name=action_name,
            confidence=conf_value,
            reason_code=UI_CONFIDENCE_LOW,
            detail=f"confidence {conf_value:.3f} < {MIN_CONFIDENCE:.2f}",
            ledger=ledger,
            ts_ms=ts_value,
        )

    before_ref = f"artifact::{intent_id}::before"
    after_ref = f"artifact::{intent_id}::after"
    manifest = vault.create_manifest(
        intent_id=intent_id,
        items=[
            {
                "artifact_id": _artifact_id_from_ref(before_ref),
                "kind": "ui_before",
                "content": str(before_snapshot),
                "evidence_refs": refs,
            },
            {
                "artifact_id": _artifact_id_from_ref(after_ref),
                "kind": "ui_after",
                "content": str(after_snapshot),
                "evidence_refs": refs,
            },
        ],
        evidence_refs=refs,
        policy_profile_id=policy_profile_id,
        request_id=request_id,
        reversible=False,
        skill_name="clawd_ui_action",
        session_id=None,
        seq=None,
        ts_ms=ts_value,
    )

    if not manifest.success:
        return _log_reject(
            request_id=request_id,
            intent_id=intent_id,
            target_desc=target_desc,
            action_name=action_name,
            confidence=conf_value,
            reason_code=manifest.reason_code or MANIFEST_INVALID,
            detail=manifest.detail,
            ledger=ledger,
            ts_ms=ts_value,
        )

    detail = "ui action executed with before/after evidence"
    ledger.log_clawd_action(
        {
            "request_id": request_id,
            "intent_id": intent_id,
            "target_desc": target_desc,
            "action_name": action_name,
            "confidence": conf_value,
            "before_artifact_ref": before_ref,
            "after_artifact_ref": after_ref,
            "manifest_ref": manifest.manifest_ref,
            "status": STATUS_EXECUTED,
            "reason_code": None,
            "stage": S_CLAWD_UI_EXEC,
            "detail": detail,
            "ts_ms": ts_value,
        }
    )
    return ClawdUIActionResult(
        success=True,
        status=STATUS_EXECUTED,
        reason_code=None,
        stage=S_CLAWD_UI_EXEC,
        detail=detail,
        request_id=request_id,
        intent_id=intent_id,
        target_desc=target_desc,
        action_name=action_name,
        confidence=conf_value,
        before_artifact_ref=before_ref,
        after_artifact_ref=after_ref,
        manifest_ref=manifest.manifest_ref,
    )


def _log_reject(
    *,
    request_id: str,
    intent_id: str,
    target_desc: str,
    action_name: str,
    confidence: float,
    reason_code: str,
    detail: str,
    ledger: SQLiteLedger,
    ts_ms: int,
) -> ClawdUIActionResult:
    ledger.log_clawd_action(
        {
            "request_id": request_id,
            "intent_id": intent_id,
            "target_desc": target_desc,
            "action_name": action_name,
            "confidence": float(confidence),
            "before_artifact_ref": None,
            "after_artifact_ref": None,
            "manifest_ref": None,
            "status": STATUS_REJECTED,
            "reason_code": reason_code,
            "stage": S_CLAWD_UI_GUARD,
            "detail": detail,
            "ts_ms": int(ts_ms),
        }
    )
    return ClawdUIActionResult(
        success=False,
        status=STATUS_REJECTED,
        reason_code=reason_code,
        stage=S_CLAWD_UI_GUARD,
        detail=detail,
        request_id=request_id,
        intent_id=intent_id,
        target_desc=target_desc,
        action_name=action_name,
        confidence=float(confidence),
        before_artifact_ref=None,
        after_artifact_ref=None,
        manifest_ref=None,
    )


def _normalize_refs(refs: list[str] | None, *, intent_id: str, request_id: str) -> list[str]:
    out: list[str] = []
    for item in refs or []:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    if out:
        return out
    return [f"evt::{intent_id or request_id}::clawd_ui"]


def _artifact_id_from_ref(ref: str) -> str:
    value = str(ref).strip()
    return value.replace("artifact::", "artifact_").replace("::", "_")
