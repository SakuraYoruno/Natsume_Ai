"""Durable RuntimeGuardian observer for poisoned Review continuations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_SCHEMA = "natsume.review_continuation.failure_snapshot.v1"
_SLO_ID = "task-review-continuation-poison"
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class ReviewContinuationGuardianObserver:
    """Project redacted poison inventory into the app-local Guardian journal."""

    def __init__(self, *, guardian: Any) -> None:
        required = (
            "record_slo_breach",
            "resolve_slo_breach",
            "snapshot",
            "event_history",
        )
        if any(not callable(getattr(guardian, name, None)) for name in required):
            raise TypeError("guardian lacks durable SLO journal operations")
        self._guardian = guardian

    def __call__(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(observation or {})
        if item.get("schema") != _SCHEMA:
            raise ValueError("Review continuation observation schema is invalid")
        status = str(item.get("status") or "").strip().upper()
        if status == "FAILED":
            return self._record_failure(item)
        if status == "RECOVERED":
            return self._record_recovery()
        raise ValueError("Review continuation observation status is invalid")

    def _record_failure(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        count = observation.get("poisoned_count")
        digest = _DIGEST.fullmatch(
            str(observation.get("incident_digest") or "").strip().lower()
        )
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or digest is None
        ):
            raise ValueError("Review continuation poison identity is incomplete")
        snapshot = self._guardian.snapshot()
        active_owned = self._owned_active_breaches(snapshot.slo_breaches)
        if active_owned:
            return {
                "status": "FAILED",
                "guardian_revision": snapshot.revision,
                "slo_breaches": list(snapshot.slo_breaches),
            }
        snapshot = self._guardian.record_slo_breach(
            slo_id=_SLO_ID,
            observed_value=1.0,
            threshold=0.0,
            evidence_ref=(
                "review-continuation-poison://sha256/" + digest.group(1)
            ),
        )
        return {
            "status": "FAILED",
            "guardian_revision": snapshot.revision,
            "slo_breaches": list(snapshot.slo_breaches),
        }

    def _record_recovery(self) -> dict[str, Any]:
        snapshot = self._guardian.snapshot()
        active = set(snapshot.slo_breaches)
        if not active:
            return {
                "status": "RECOVERED",
                "guardian_revision": snapshot.revision,
                "resolved_breaches": [],
            }
        targets = self._owned_active_breaches(active)
        if not targets:
            return {
                "status": "RECOVERED",
                "guardian_revision": snapshot.revision,
                "resolved_breaches": [],
            }
        evidence_digest = hashlib.sha256(
            json.dumps(
                {"slo_id": _SLO_ID, "breach_refs": targets},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence_ref = (
            "review-continuation-restored://sha256/" + evidence_digest
        )
        for breach_ref in targets:
            snapshot = self._guardian.resolve_slo_breach(
                breach_ref=breach_ref,
                evidence_ref=evidence_ref,
            )
        return {
            "status": "RECOVERED",
            "guardian_revision": snapshot.revision,
            "resolved_breaches": list(targets),
        }

    def _owned_active_breaches(
        self,
        active_breaches: Any,
    ) -> tuple[str, ...]:
        active = {str(item) for item in active_breaches}
        owned = {
            str(event.get("payload", {}).get("breach_ref") or "")
            for event in self._guardian.event_history()
            if event.get("event_type") == "GUARDIAN_SLO_BREACH"
            and event.get("payload", {}).get("slo_id") == _SLO_ID
        }
        return tuple(sorted(active & owned))


__all__ = ["ReviewContinuationGuardianObserver"]
