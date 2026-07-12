"""Durable, replay-safe references to official Governor decisions."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


GOVERNOR_GRANT_MISSING = "GOVERNOR_GRANT_MISSING"
GOVERNOR_GRANT_INVALID = "GOVERNOR_GRANT_INVALID"


class GovernorGrantError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNOR_GRANT_INVALID)
        self.detail = str(detail or "Governor grant validation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class DurableGovernorGrantResolver:
    """Resolve an immutable approved decision from the canonical audit ledger."""

    def __init__(self, ledger: Any) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        self._ledger = ledger

    def require(self, *, request_id: str, expected_ref: str) -> dict[str, Any]:
        request_ref = str(request_id or "").strip()
        grant_ref = str(expected_ref or "").strip()
        if not request_ref or not grant_ref:
            raise GovernorGrantError(
                GOVERNOR_GRANT_MISSING,
                "request_id and expected Governor grant reference are required",
            )
        trace = self._ledger.trace_request(request_ref)
        rows = list(trace.get("governor_log") or []) if isinstance(trace, Mapping) else []
        approved = [
            dict(row)
            for row in rows
            if isinstance(row, Mapping)
            and str(row.get("decision") or "").strip().upper() == "APPROVE"
        ]
        approved.sort(key=lambda row: int(row.get("ts_ms") or 0), reverse=True)
        for decision in approved:
            if governor_grant_ref(request_ref, decision) == grant_ref:
                return decision
        raise GovernorGrantError(
            GOVERNOR_GRANT_INVALID,
            "Governor grant does not match any durable approved decision",
        )


def governor_grant_ref(request_id: str, decision: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "decision": str(decision.get("decision") or "").strip().upper(),
            "intent_id": str(decision.get("intent_id") or "").strip(),
            "request_id": str(request_id or "").strip(),
            "ts_ms": int(decision.get("ts_ms") or 0),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"governor-decision://sha256/{digest}"


__all__ = [
    "GOVERNOR_GRANT_INVALID",
    "GOVERNOR_GRANT_MISSING",
    "DurableGovernorGrantResolver",
    "GovernorGrantError",
    "governor_grant_ref",
]
