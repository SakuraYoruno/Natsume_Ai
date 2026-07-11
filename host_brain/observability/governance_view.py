"""Compact read-only Capability, Health, Identity and isolation projection."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any


GOVERNANCE_VIEW_SCHEMA_VERSION = "Natsume.governance.snapshot.v1"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("to_dict", "as_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            result = method()
            return dict(result) if isinstance(result, Mapping) else {}
    return {}


def _text(value: Any, limit: int = 512) -> str:
    # Never stringify containers: a mapping could contain an identity body.
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, (int, float, bool)):
        return str(value).strip()[:limit]
    enum_value = getattr(value, "value", None)
    return enum_value.strip()[:limit] if isinstance(enum_value, str) else ""


def _capability_item(value: Any) -> dict[str, Any]:
    card = _mapping(value)
    current = int(card.get("maturity_level_value") or 0)
    ceiling = int(card.get("static_maturity_ceiling") or 0)
    evidence: list[dict[str, Any]] = []
    for raw in list(card.get("evidence") or ())[:32]:
        item = _mapping(raw)
        observed = int(item.get("observed_level") or 0)
        outcome = _text(item.get("outcome"), 32).upper()
        evidence_ref = _text(item.get("evidence_ref"))
        evidence.append(
            {
                "evidence_ref": evidence_ref,
                "outcome": outcome,
                "observed_level": observed,
                "observed_at_ms": int(item.get("observed_at_ms") or 0),
            }
        )
    raw_candidate = _mapping(card.get("upgrade_candidate"))
    candidate = None
    if raw_candidate:
        operator_review_id = _text(
            raw_candidate.get("operator_review_id")
            or raw_candidate.get("review_id")
            or raw_candidate.get("review_ref")
        )
        candidate = {
            "candidate_ref": _text(raw_candidate.get("candidate_ref")),
            "review_ref": operator_review_id or None,
            "materialized_to_review_store": bool(operator_review_id),
            "review_authority": _text(raw_candidate.get("review_authority"), 32),
            "status": _text(raw_candidate.get("status"), 32),
            "proposed_level": _text(raw_candidate.get("proposed_level"), 16),
            "evidence_refs": [
                _text(ref) for ref in list(raw_candidate.get("evidence_refs") or ()) if _text(ref)
            ],
            "automatic_upgrade_available": False,
        }
    return {
        "capability_id": _text(card.get("capability_id"), 256),
        "source_kind": _text(card.get("source_kind"), 64),
        "source_ref": _text(card.get("source_ref")),
        "maturity": _text(card.get("maturity_level"), 16),
        "maturity_level": current,
        "maturity_label": _text(card.get("maturity_level"), 16),
        "maturity_level_value": current,
        "canonical_risk": int(card.get("canonical_risk") or 5),
        "canonical_static_risk": int(card.get("canonical_risk") or 5),
        "static_maturity_ceiling": ceiling,
        "execution_mode": _text(card.get("execution_mode"), 64),
        "status": _text(card.get("status"), 64),
        "evidence": evidence,
        "evidence_refs": [item["evidence_ref"] for item in evidence if item["evidence_ref"]],
        "upgrade_candidate": candidate,
        "automatic_upgrade_available": False,
        "read_only": True,
    }


def _capabilities(values: Sequence[Any] | None) -> dict[str, Any]:
    if values is None:
        return {
            "status": "UNAVAILABLE",
            "reason_code": "CAPABILITY_SCORECARDS_UNAVAILABLE",
            "items": [],
            "automatic_upgrade_available": False,
            "read_only": True,
        }
    items = sorted((_capability_item(value) for value in values), key=lambda row: row["capability_id"])
    return {
        "status": "AVAILABLE",
        "count": len(items),
        "items": items,
        "automatic_upgrade_available": False,
        "read_only": True,
    }


def _degradation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {
            "status": "UNAVAILABLE",
            "reason_code": "DEGRADATION_STATE_UNAVAILABLE",
            "active_dependencies": [],
            "recovery_pending_dependencies": [],
            "reason_codes": ["DEGRADATION_STATE_UNAVAILABLE"],
            "evidence_refs": [],
            "capability_restrictions": [],
            "restrictions": [],
            "affected_capabilities": [],
            "high_risk_latched": True,
            "recovery": {
                "status": "UNAVAILABLE",
                "eligible": False,
                "evidence_refs": [],
                "reason_codes": ["DEGRADATION_STATE_UNAVAILABLE"],
                "review_ref": None,
            },
            "recovery_mutation_available": False,
            "read_only": True,
        }
    state = _mapping(value)
    restrictions = []
    for raw in list(state.get("capability_restrictions") or ()):
        item = _mapping(raw)
        restrictions.append(
            {
                "capability_id": _text(item.get("capability_id"), 256),
                "status": _text(item.get("status"), 32),
                "high_risk": bool(item.get("high_risk")),
                "origin_dependencies": list(item.get("origin_dependencies") or ()),
                "safe_fallbacks": list(item.get("safe_fallbacks") or ()),
                "review_required": bool(item.get("review_required")),
                "automatic_release": False,
            }
        )
    active = list(state.get("active_dependencies") or ())
    pending = list(state.get("recovery_pending_dependencies") or ())
    recovery_evidence = list(state.get("recovery_evidence_refs") or ())
    reason_codes = [
        _text(_mapping(row).get("reason_code"), 128)
        for row in (*active, *pending)
        if _text(_mapping(row).get("reason_code"), 128)
    ]
    review_ref = ""
    if pending:
        first_pending = _mapping(pending[0])
        review_ref = _text(
            first_pending.get("review_id") or first_pending.get("review_subject_ref")
        )
    affected = list(state.get("affected_capabilities") or ()) or [
        row["capability_id"] for row in restrictions if row["capability_id"]
    ]
    return {
        "status": _text(state.get("status"), 64) or "UNAVAILABLE",
        "state_id": _text(state.get("state_id"), 256),
        "revision": int(state.get("revision") or 0),
        "updated_at_ms": int(state.get("updated_at_ms") or 0),
        "active_dependencies": active,
        "recovery_pending_dependencies": pending,
        "reason_codes": reason_codes,
        "evidence_refs": list(state.get("evidence_refs") or ()),
        "recovery_evidence_refs": recovery_evidence,
        "capability_restrictions": restrictions,
        "restrictions": [row["capability_id"] for row in restrictions if row["capability_id"]],
        "affected_capabilities": affected,
        "high_risk_latched": any(row["high_risk"] for row in restrictions),
        "durability_status": _text(state.get("durability_status"), 64),
        "permission_effect": "RESTRICT_ONLY",
        "recovery_mutation_available": False,
        "capability_expansion_requires_review": True,
        "recovery": {
            "status": "REVIEW_REQUIRED" if pending else "NOT_PENDING",
            "eligible": bool(pending and recovery_evidence),
            "evidence_refs": recovery_evidence,
            "reason_codes": reason_codes,
            "review_ref": review_ref or None,
        },
        "read_only": True,
    }


def _snapshot_envelope(value: Any) -> dict[str, Any]:
    snapshot = _mapping(value)
    return {
        "snapshot_ref": _text(snapshot.get("snapshot_ref")),
        "release_ref": _text(snapshot.get("release_ref")),
        "previous_release_ref": _text(snapshot.get("previous_release_ref")) or None,
        "created_at_ms": int(snapshot.get("created_at_ms") or 0),
        "identity_digest": _text(snapshot.get("identity_digest"), 128),
        "snapshot_hash": _text(snapshot.get("snapshot_hash"), 128),
    }


def _identity(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {
            "status": "UNAVAILABLE",
            "reason_code": "IDENTITY_SNAPSHOTS_UNAVAILABLE",
            "private_body_included": False,
            "current_snapshot_ref": None,
            "release_ref": None,
            "current": None,
            "previous": None,
            "diff": {"severity": "UNAVAILABLE", "items": []},
            "gate": None,
            "review": {"required": False, "review_ref": None, "status": "UNAVAILABLE"},
            "identity_acceptance_available": False,
            "read_only": True,
        }
    raw = dict(value)
    raw_diff = _mapping(raw.get("diff"))
    differences = []
    for raw_item in list(raw_diff.get("differences") or ()):
        item = _mapping(raw_item)
        differences.append(
            {
                "section": _text(item.get("section"), 64),
                "field": _text(item.get("field"), 128),
                "severity": _text(item.get("severity"), 32),
                "reason_code": _text(item.get("reason_code"), 128),
            }
        )
    raw_gate = _mapping(raw.get("gate"))
    raw_candidate = _mapping(raw_gate.get("review_candidate"))
    candidate = None
    if raw_candidate:
        operator_review_id = _text(
            raw_candidate.get("operator_review_id")
            or raw_candidate.get("review_id")
            or raw_candidate.get("review_ref")
        )
        candidate = {
            "candidate_ref": _text(raw_candidate.get("candidate_ref")),
            "review_ref": operator_review_id or None,
            "materialized_to_review_store": bool(operator_review_id),
            "review_lane": _text(raw_candidate.get("review_lane"), 32),
            "status": _text(raw_candidate.get("status"), 32),
            "diff_ref": _text(raw_candidate.get("diff_ref")),
            "automatic_apply": False,
        }
    current = _snapshot_envelope(raw.get("current"))
    previous = _snapshot_envelope(raw.get("previous"))
    diff = {
        "severity": _text(raw_diff.get("severity"), 32),
        "major_difference": bool(raw_diff.get("major_difference")),
        "diff_ref": _text(raw_diff.get("diff_ref")),
        "diff_hash": _text(raw_diff.get("diff_hash"), 128),
        "differences": differences,
        "items": differences,
    }
    review = {
        "required": bool(candidate) or diff["major_difference"],
        "review_ref": candidate["review_ref"] if candidate else None,
        "status": candidate["status"] if candidate else "NOT_REQUIRED",
    }
    return {
        "status": "AVAILABLE",
        "private_body_included": False,
        "current_snapshot_ref": current["snapshot_ref"] or None,
        "release_ref": current["release_ref"] or None,
        "current": current,
        "previous": previous,
        "diff": diff,
        "gate": {
            "status": _text(raw_gate.get("status"), 64),
            "publish_allowed": bool(raw_gate.get("publish_allowed")),
            "mutation_effect": "NONE",
            "review_candidate": candidate,
        },
        "review": review,
        "identity_acceptance_available": False,
        "read_only": True,
    }


def _domain_isolation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    environment = dict(value or {})
    lab_mode = bool(environment.get("lab_mode"))
    allow_exploit = bool(environment.get("allow_exploit"))
    return {
        "pentest": {
            "lab_mode": lab_mode,
            "allow_exploit_requested": allow_exploit,
            "allow_exploit_effective": lab_mode and allow_exploit,
            "high_risk_entrypoints_disabled": not lab_mode,
            "high_risk_entry_enabled": lab_mode,
            "high_risk_entrypoints": [
                {
                    "entrypoint_id": entry_id,
                    "lab_only": True,
                    "status": "ENABLED" if lab_mode else "DISABLED_NON_LAB",
                    "executable_in_current_mode": lab_mode,
                }
                for entry_id in ("approve_side_effect", "approve_autonomous", "continue_side_effect")
            ],
            "world_canon_access": False,
            "domain_mode": _text(environment.get("domain_mode") or "domain", 32).lower(),
            "read_only": True,
        },
        "read_only": True,
    }


def build_governance_view(
    *,
    capability_scorecards: Sequence[Any] | None,
    degradation_state: Mapping[str, Any] | None,
    identity: Mapping[str, Any] | None = None,
    pentest_environment: Mapping[str, Any] | None = None,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": GOVERNANCE_VIEW_SCHEMA_VERSION,
        "generated_at_ms": int(generated_at_ms if generated_at_ms is not None else time.time() * 1000),
        "read_only": True,
        "write_api": False,
        "automatic_upgrade_available": False,
        "capabilities": _capabilities(capability_scorecards),
        "degradation": _degradation(degradation_state),
        "identity": _identity(identity),
        "domain_isolation": _domain_isolation(pentest_environment),
    }


__all__ = ["GOVERNANCE_VIEW_SCHEMA_VERSION", "build_governance_view"]
