"""T-NLE-23: Capability/Health/Identity governance UI backend contract."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from host_brain.app import obs_routes_governance as routes
from host_brain.app.settings.core import get_core_settings
from host_brain.observability.governance_view import build_governance_view
from natsume_contracts.operator.v1 import ProjectionDocument


PRIVATE = "PRIVATE_IDENTITY_BODY_MUST_NOT_LEAK"


def _card() -> dict[str, object]:
    return {
        "capability_id": "repo_reader",
        "source_kind": "SKILL_META",
        "source_ref": "skill-registry://repo_reader/v1",
        "scorecard_ref": "capability-scorecard://sha256/abc",
        "maturity_level": "L2",
        "maturity_level_value": 2,
        "canonical_risk": 2,
        "static_maturity_ceiling": 4,
        "execution_mode": "REVIEW_REQUIRED",
        "status": "STABLE",
        "upgrade_candidate": {
            "candidate_ref": "capability-upgrade://repo-reader/L4",
            "operator_review_id": "review-capability-repo-reader-l4",
            "review_authority": "MASTER",
            "status": "REVIEW_REQUIRED",
            "proposed_level": "L4",
            "evidence_refs": ["test-run://T-NLE-23/capability"],
        },
        "evidence": [
            {
                "evidence_ref": "test-run://T-NLE-23/capability",
                "outcome": "PASS",
                "observed_level": 4,
                "observed_at_ms": 1000,
            }
        ],
    }


def _degradation() -> dict[str, object]:
    return {
        "state_id": "runtime-degradation",
        "status": "RECOVERY_PENDING",
        "revision": 2,
        "updated_at_ms": 2000,
        "active_dependencies": [],
        "recovery_pending_dependencies": [
            {
                "dependency_id": "MODEL",
                "evidence_refs": ["probe://model/recovered"],
                "review_required": True,
            }
        ],
        "evidence_refs": ["probe://model/down"],
        "recovery_evidence_refs": ["probe://model/recovered"],
        "durability_status": "READY",
        "capability_restrictions": [
            {
                "capability_id": "high_risk_execution",
                "status": "DISABLED",
                "high_risk": True,
                "origin_dependencies": ["MODEL"],
                "safe_fallbacks": ["human_review"],
                "review_required": True,
                "automatic_release": False,
            }
        ],
    }


def _identity() -> dict[str, object]:
    return {
        "current": {
            "snapshot_ref": "identity-snapshot://current",
            "release_ref": "release://v2",
            "previous_release_ref": "release://v1",
            "created_at_ms": 2000,
            "identity_digest": "sha256:" + "2" * 64,
            "snapshot_hash": "sha256:" + "3" * 64,
            "self_model": {"private_body": PRIVATE},
        },
        "previous": {
            "snapshot_ref": "identity-snapshot://previous",
            "release_ref": "release://v1",
            "created_at_ms": 1000,
            "identity_digest": "sha256:" + "0" * 64,
            "snapshot_hash": "sha256:" + "1" * 64,
            "bond": {"private_body": PRIVATE},
        },
        "diff": {
            "severity": "MAJOR",
            "major_difference": True,
            "diff_ref": "identity-diff://safe",
            "diff_hash": "sha256:" + "4" * 64,
            "differences": [
                {
                    "section": "self_model",
                    "field": "self_boundaries_digest",
                    "severity": "MAJOR",
                    "reason_code": "IDENTITY_SELF_BOUNDARY_CHANGED",
                    "previous_value": PRIVATE,
                    "current_value": PRIVATE,
                }
            ],
        },
        "gate": {
            "status": "REVIEW_REQUIRED",
            "publish_allowed": False,
            "mutation_effect": "NONE",
            "review_candidate": {
                "candidate_ref": "identity-review://safe",
                "operator_review_id": "review-identity-safe",
                "review_lane": "MASTER",
                "status": "PENDING",
                "diff_ref": "identity-diff://safe",
                "automatic_apply": True,
            },
        },
    }


def test_t_nle_23_view_is_read_only_and_upgrade_is_review_only() -> None:
    view = build_governance_view(
        capability_scorecards=[_card()],
        degradation_state=_degradation(),
        generated_at_ms=3000,
    )

    assert view["schema_version"] == "Natsume.governance.snapshot.v1"
    assert view["read_only"] is True and view["write_api"] is False
    assert view["automatic_upgrade_available"] is False
    item = view["capabilities"]["items"][0]
    assert item["maturity"] == "L2"
    assert item["maturity_level"] == 2 and item["maturity_label"] == "L2"
    assert item["canonical_risk"] == 2
    assert item["canonical_static_risk"] == 2
    assert item["static_maturity_ceiling"] == 4
    assert item["evidence"][0]["evidence_ref"].startswith("test-run://")
    assert item["evidence_refs"] == ["test-run://T-NLE-23/capability"]
    assert item["automatic_upgrade_available"] is False
    assert item["upgrade_candidate"]["review_authority"] == "MASTER"
    assert item["upgrade_candidate"]["automatic_upgrade_available"] is False
    assert item["upgrade_candidate"]["review_ref"] == "review-capability-repo-reader-l4"
    assert item["upgrade_candidate"]["materialized_to_review_store"] is True


def test_t_nle_23_degradation_exposes_failure_recovery_and_high_risk_latch() -> None:
    view = build_governance_view(
        capability_scorecards=[],
        degradation_state=_degradation(),
        generated_at_ms=3000,
    )
    health = view["degradation"]
    assert health["status"] == "RECOVERY_PENDING"
    assert health["recovery_evidence_refs"] == ["probe://model/recovered"]
    assert health["restrictions"] == ["high_risk_execution"]
    assert health["affected_capabilities"] == ["high_risk_execution"]
    assert health["high_risk_latched"] is True
    assert health["capability_restrictions"][0]["automatic_release"] is False
    assert health["recovery_mutation_available"] is False
    assert health["capability_expansion_requires_review"] is True
    assert health["recovery"]["status"] == "REVIEW_REQUIRED"
    assert health["recovery"]["eligible"] is True


def test_t_nle_23_identity_diff_links_review_without_private_values() -> None:
    view = build_governance_view(
        capability_scorecards=[],
        degradation_state={},
        identity=_identity(),
        generated_at_ms=3000,
    )
    identity = view["identity"]
    assert identity["diff"]["major_difference"] is True
    assert identity["diff"]["items"] == identity["diff"]["differences"]
    assert identity["private_body_included"] is False
    assert identity["current_snapshot_ref"] == "identity-snapshot://current"
    assert identity["release_ref"] == "release://v2"
    assert identity["gate"]["status"] == "REVIEW_REQUIRED"
    assert identity["gate"]["review_candidate"]["candidate_ref"] == "identity-review://safe"
    assert identity["gate"]["review_candidate"]["review_ref"] == "review-identity-safe"
    assert identity["gate"]["review_candidate"]["materialized_to_review_store"] is True
    assert identity["gate"]["review_candidate"]["automatic_apply"] is False
    assert identity["identity_acceptance_available"] is False
    assert identity["review"] == {
        "required": True,
        "review_ref": "review-identity-safe",
        "status": "PENDING",
    }
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    assert PRIVATE not in serialized
    assert "previous_value" not in serialized and "current_value" not in serialized
    assert "self_model" not in identity["current"]
    assert "bond" not in identity["previous"]


def test_t_nle_23_candidate_refs_never_masquerade_as_materialized_reviews() -> None:
    card = _card()
    card["upgrade_candidate"] = dict(card["upgrade_candidate"])
    card["upgrade_candidate"].pop("operator_review_id")
    identity_source = _identity()
    identity_source["gate"] = dict(identity_source["gate"])
    identity_source["gate"]["review_candidate"] = dict(
        identity_source["gate"]["review_candidate"]
    )
    identity_source["gate"]["review_candidate"].pop("operator_review_id")

    view = build_governance_view(
        capability_scorecards=[card],
        degradation_state={},
        identity=identity_source,
        generated_at_ms=3000,
    )

    upgrade = view["capabilities"]["items"][0]["upgrade_candidate"]
    assert upgrade["candidate_ref"].startswith("capability-upgrade://")
    assert upgrade["review_ref"] is None
    assert upgrade["materialized_to_review_store"] is False
    identity_review = view["identity"]["review"]
    assert identity_review["required"] is True
    assert identity_review["review_ref"] is None
    assert view["identity"]["gate"]["review_candidate"]["materialized_to_review_store"] is False


def test_t_nle_23_non_lab_never_exposes_executable_high_risk_pentest_entry() -> None:
    view = build_governance_view(
        capability_scorecards=[],
        degradation_state={},
        pentest_environment={"lab_mode": False, "allow_exploit": True},
        generated_at_ms=3000,
    )
    pentest = view["domain_isolation"]["pentest"]
    assert pentest["high_risk_entrypoints_disabled"] is True
    assert pentest["high_risk_entry_enabled"] is False
    assert pentest["allow_exploit_effective"] is False
    assert pentest["world_canon_access"] is False
    assert all(item["status"] == "DISABLED_NON_LAB" for item in pentest["high_risk_entrypoints"])
    assert all(item["executable_in_current_mode"] is False for item in pentest["high_risk_entrypoints"])


def test_t_nle_23_flag_defaults_off_before_store_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = 0

    class ForbiddenStore:
        def get(self, _kind: str, _key: str) -> None:
            nonlocal called
            called += 1
            raise AssertionError("feature gate must run before projection read")

    monkeypatch.delenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", raising=False)
    # CoreSettings intentionally supports a deployment .env.  Isolate this
    # default-value contract from the developer's live acceptance settings.
    monkeypatch.chdir(tmp_path)
    get_core_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as blocked:
            asyncio.run(routes._get_governance_snapshot(ForbiddenStore()))
        assert blocked.value.status_code == 404
        assert called == 0
    finally:
        get_core_settings.cache_clear()


def test_t_nle_23_enabled_get_uses_injected_projection_store(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"schema_version": "Natsume.governance.snapshot.v1", "read_only": True}
    now_ms = int(time.time() * 1000)

    class ProjectedStore:
        def get(self, kind: str, key: str) -> ProjectionDocument:
            return ProjectionDocument(
                kind=kind,
                key=key,
                payload=expected,
                projection_version=1,
                projection_cursor=1,
                source_cursor=1,
                source_digest="sha256:source",
                payload_digest="sha256:payload",
                freshness_ts_ms=now_ms,
            )

    monkeypatch.setenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    try:
        result = asyncio.run(routes._get_governance_snapshot(ProjectedStore()))
        assert result["schema_version"] == expected["schema_version"]
        assert result["read_only"] is True
        assert result["projection"]["projection_version"] == 1
    finally:
        get_core_settings.cache_clear()


def test_t_nle_23_route_surface_has_no_governance_mutation() -> None:
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert '@router.get("/governance/snapshot")' in source
    assert '@router.post("/governance' not in source
    assert '@router.put("/governance' not in source
    assert '@router.delete("/governance' not in source
