"""Durable RuntimeGuardian observer for Agent Workspace cleanup passes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_SCHEMA = "natsume.agent_workspace.cleanup_observation.v1"
_SLO_ID = "agent-workspace-cleanup-continuation"
_ERROR_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")


class WorkspaceCleanupGuardianObserver:
    """Persist redacted cleanup failures and resolve them after recovery."""

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
            raise ValueError("Workspace cleanup observation schema is invalid")
        status = str(item.get("status") or "").strip().upper()
        if status == "FAILED":
            return self._record_failure(item)
        if status == "RECOVERED":
            return self._record_recovery()
        raise ValueError("Workspace cleanup observation status is invalid")

    def _record_failure(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        error_type = str(observation.get("error_type") or "").strip()
        digest = _ERROR_DIGEST.fullmatch(
            str(observation.get("error_digest") or "").strip().lower()
        )
        failures = observation.get("consecutive_failures")
        if (
            _ERROR_TYPE.fullmatch(error_type) is None
            or digest is None
            or isinstance(failures, bool)
            or not isinstance(failures, int)
            or failures < 1
        ):
            raise ValueError("Workspace cleanup failure identity is incomplete")
        snapshot = self._guardian.record_slo_breach(
            slo_id=_SLO_ID,
            # One durable breach represents one consecutive failure incident.
            # The local health port exposes the current failure count without
            # appending one Guardian event per polling interval.
            observed_value=1.0,
            threshold=0.0,
            evidence_ref=(
                "workspace-cleanup-failure://sha256/" + digest.group(1)
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
        evidence_digest = hashlib.sha256(
            json.dumps(
                {"slo_id": _SLO_ID, "breach_refs": targets},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence_ref = (
            "workspace-cleanup-restored://sha256/" + evidence_digest
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


__all__ = ["WorkspaceCleanupGuardianObserver"]
