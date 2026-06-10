from __future__ import annotations

import pytest

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    DRIVE_TRUST_CAPABILITIES,
    evaluate_trust_gradient,
    list_capability_specs,
    maintain_desire_lattice,
    metabolize_intention,
    regulate_trust_thermostat,
    resolve_drive_conflict_layer,
    separate_need_want_impulse,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_drive_trust_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(DRIVE_TRUST_CAPABILITIES) <= registered
    assert list(DRIVE_TRUST_CAPABILITIES) == [
        "trust_gradient",
        "trust_thermostat",
        "intention_metabolism",
        "drive_conflict_layer",
        "desire_lattice",
        "need_want_impulse",
    ]


def test_trust_gradient_requires_review_for_mixed_agent_worker_history(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "trust-gradient",
            "capability": "trust_gradient",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "trust_target": "agent_worker",
                "recent_successes": 3,
                "recent_timeouts": 1,
            },
        }
    )
    trust = evaluate_trust_gradient({"trust_target": "agent_worker", "recent_successes": 2, "recent_failures": 3})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["trust"]["policy"] == "allow_inspect_require_review_for_worker"
    assert trust["trust"]["policy"] == "inspect_only_until_repaired"
    assert resolve_bio_ops_adoption_policy("trust_gradient")["mode"] == "report_only"


def test_trust_thermostat_cools_overheated_autonomy(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "trust-thermostat",
            "capability": "trust_thermostat",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"trust_target": "agent_worker", "trust_temperature": 0.88, "recent_successes": 1},
        }
    )
    thermostat = regulate_trust_thermostat({"trust_temperature": 0.2, "recent_failures": 1})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["trust"]["thermostat_action"] == "cool_down_autonomy"
    assert thermostat["trust"]["thermostat_action"] == "warm_up_only_after_evidence"


def test_intention_metabolism_converts_proactive_want_to_quiet_presence(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "intention-metabolism",
            "capability": "intention_metabolism",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"intention": "proactive_check_in", "user_stress": "high"},
        }
    )
    intention = metabolize_intention({"intention": "我会之后提醒你"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["classification"] == "want"
    assert result["findings"][0]["decision"] == "convert_to_quiet_presence"
    assert intention["classification"] == "promise"


def test_drive_conflict_prefers_immune_restraint_for_risky_context(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "drive-conflict",
            "capability": "drive_conflict_layer",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "drives": ["closeness", "efficiency", "immune_safety"],
                "context": "deploy secret risk",
            },
        }
    )
    conflict = resolve_drive_conflict_layer({"drives": ["closeness", "efficiency"], "context": "technical decision"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["winner"] == "immune_restraint"
    assert conflict["winner"] == "efficiency"


def test_desire_lattice_blocks_checkin_under_sleep_pressure(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "desire-lattice",
            "capability": "desire_lattice",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "desire": "maintain_relationship_warmth",
                "sleep_pressure": "high",
                "user_stress": "high",
            },
        }
    )
    lattice = maintain_desire_lattice({"desire": "maintain_relationship_warmth", "sleep_pressure": "high"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["desires"][0]["allowed_actions"] == ["quiet_presence"]
    assert lattice["direct_write_allowed"] is False


def test_need_want_impulse_separates_proactive_impulse(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "need-want-impulse",
            "capability": "need_want_impulse",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"drive": "proactive_talk", "user_stress": "overloaded"},
        }
    )
    separated = separate_need_want_impulse({"drive": "production incident"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["classification"] == "impulse"
    assert result["findings"][0]["decision"] == "convert_to_quiet_presence"
    assert separated["classification"] == "need"


@pytest.mark.parametrize("capability", DRIVE_TRUST_CAPABILITIES)
def test_drive_trust_degrades_without_evidence(tmp_path, capability: str) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": f"missing-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {},
        }
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "BIO_OPS_MISSING_EVIDENCE"


@pytest.mark.parametrize("capability", DRIVE_TRUST_CAPABILITIES)
def test_drive_trust_blocks_sensitive_targets_and_ownerless_write_candidates(tmp_path, capability: str) -> None:
    runner = _runner(tmp_path)

    sensitive = runner.run(
        {
            "task_id": f"sensitive-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"target_paths": ["host_brain/canon/core.yaml"], "evidence_refs": ["evt-1"]},
        }
    )
    ownerless = runner.run(
        {
            "task_id": f"ownerless-{capability}",
            "capability": capability,
            "mode": "candidate",
            "inputs": {"evidence_refs": ["evt-1"], "write_candidate": True},
        }
    )

    assert sensitive["status"] == "BLOCKED"
    assert sensitive["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"
    assert ownerless["status"] == "BLOCKED"
    assert ownerless["reason_code"] == "BIO_OPS_OWNER_REQUIRED"
