"""AIRLOCK ingress quarantine classification and draft downgrading."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import AIRLOCK_BLOCKED_INGRESS
from host_brain.core.ids import now_ms


CLASS_FACT = "FACT"
CLASS_DRAFT_CODE = "DRAFT_CODE"
CLASS_DRAFT_INSTRUCTION = "DRAFT_INSTRUCTION"
CLASS_UNKNOWN = "UNKNOWN"

CLASSIFICATION_SET = {
    CLASS_FACT,
    CLASS_DRAFT_CODE,
    CLASS_DRAFT_INSTRUCTION,
    CLASS_UNKNOWN,
}

_CODE_HINT = re.compile(
    r"```|^\s*(def\s+\w+|class\s+\w+|import\s+\w+|from\s+\w+\s+import|function\s+\w+|SELECT\s+|INSERT\s+|UPDATE\s+|DELETE\s+)",
    re.IGNORECASE | re.MULTILINE,
)
_INSTRUCTION_HINT = re.compile(
    r"\b(run|execute|delete|drop|sudo|rm\s+-rf|powershell|cmd|pip\s+install|npm\s+install|curl|wget)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IngressQuarantineResult:
    """Result of ingress content quarantine."""

    classification: str
    draft_only: bool
    allow_execute: bool
    reason_code: str
    quarantine_digest: str
    payload_digest: str
    quarantined_payload: dict[str, Any]


def quarantine_ingress(
    inbound_payload: Any,
    *,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> IngressQuarantineResult:
    """Classify inbound content and force draft-only handling."""

    normalized_text = _normalize_text(inbound_payload)
    payload_digest = _digest_json({"payload": normalized_text})
    classification = classify_ingress(normalized_text)
    quarantined_payload = {
        "content": normalized_text,
        "classification": classification,
        "draft_only": True,
    }
    quarantine_digest = _digest_json(quarantined_payload)
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    if ledger is not None:
        ledger.log_airlock(
            {
                "request_id": request_id,
                "direction": "INGRESS",
                "reason_code": AIRLOCK_BLOCKED_INGRESS,
                "removed_fields": [],
                "redactions": [],
                "payload_digest": payload_digest,
                "quarantine_digest": quarantine_digest,
                "ingress_classification": classification,
                "blocked": 1,
                "detail": "ingress quarantined as draft-only",
                "ts_ms": now_value,
            }
        )

    return IngressQuarantineResult(
        classification=classification,
        draft_only=True,
        allow_execute=False,
        reason_code=AIRLOCK_BLOCKED_INGRESS,
        quarantine_digest=quarantine_digest,
        payload_digest=payload_digest,
        quarantined_payload=quarantined_payload,
    )


def classify_ingress(content: str) -> str:
    """Classify inbound text into fixed AIRLOCK buckets."""

    text = (content or "").strip()
    if not text:
        return CLASS_UNKNOWN
    if _CODE_HINT.search(text):
        return CLASS_DRAFT_CODE
    if _INSTRUCTION_HINT.search(text):
        return CLASS_DRAFT_INSTRUCTION
    if len(text) < 4:
        return CLASS_UNKNOWN
    return CLASS_FACT


def is_valid_classification(value: str) -> bool:
    return value in CLASSIFICATION_SET


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _digest_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
