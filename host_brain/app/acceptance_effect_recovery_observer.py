"""Durable RuntimeGuardian observer for Acceptance Effect recovery passes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_ERROR_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_SLO_ID = "acceptance-effect-recovery-pass"


class AcceptanceEffectRecoveryGuardianObserver:
    """Project recovery failures into the app-local Guardian journal.

    The observer never persists exception text.  It accepts only the typed
    exception class and an opaque digest, then resolves every still-active
    breach for this SLO after a subsequent real recovery pass.  This also
    closes breaches restored from a previous process.
    """

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
        if item.get("schema") != "natsume.acceptance_effect.recovery_observation.v1":
            raise ValueError("Acceptance Effect recovery observation schema is invalid")
        status = str(item.get("status") or "").strip().upper()
        if status == "FAILED":
            return self._record_failure(item)
        if status == "RECOVERED":
            return self._record_recovery()
        raise ValueError("Acceptance Effect recovery observation status is invalid")

    def _record_failure(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        error_type = str(observation.get("error_type") or "").strip()
        match = _ERROR_DIGEST.fullmatch(
            str(observation.get("error_digest") or "").strip().lower()
        )
        if not error_type or match is None:
            raise ValueError("Acceptance Effect failure identity is incomplete")
        # The URI contains only the opaque SHA-256 digest.  Exception messages
        # can contain paths, provider payloads or secrets and are never stored.
        evidence_ref = (
            "effect-recovery-failure://sha256/" + match.group(1)
        )
        snapshot = self._guardian.record_slo_breach(
            slo_id=_SLO_ID,
            observed_value=1.0,
            threshold=0.0,
            evidence_ref=evidence_ref,
        )
        return {
            "status": "FAILED",
            "guardian_revision": snapshot.revision,
            "slo_breaches": list(snapshot.slo_breaches),
        }

    def _record_recovery(self) -> dict[str, Any]:
        snapshot = self._guardian.snapshot()
        active = set(snapshot.slo_breaches)
        owned = {
            str(event.get("payload", {}).get("breach_ref") or "")
            for event in self._guardian.event_history()
            if event.get("event_type") == "GUARDIAN_SLO_BREACH"
            and event.get("payload", {}).get("slo_id") == _SLO_ID
        }
        targets = tuple(sorted(active & owned))
        if not targets:
            return {
                "status": "RECOVERED",
                "guardian_revision": snapshot.revision,
                "resolved_breaches": [],
            }
        digest = hashlib.sha256(
            json.dumps(
                {"slo_id": _SLO_ID, "breach_refs": targets},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence_ref = (
            "effect-recovery-restored://sha256/" + digest
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


__all__ = ["AcceptanceEffectRecoveryGuardianObserver"]
