from __future__ import annotations

from pathlib import Path

from host_brain.release.compatibility_registry import (
    RETIRED_CORE_PATHS,
    validate_registry,
    validate_retired_paths_absent,
)
from host_brain.release.release_manifest import (
    CORE_RELEASE_GATE_COUNT,
    CORE_RELEASE_MANIFEST_SCHEMA_VERSION,
    generate_core_release_manifest,
    generate_core_sbom,
    validate_core_release_manifest,
)


ROOT = Path(__file__).resolve().parents[2]


def _passing_gates() -> list[dict[str, object]]:
    return [
        {"gate": index, "status": "PASS", "evidence_refs": [f"architecture-gate://{index}"]}
        for index in range(1, CORE_RELEASE_GATE_COUNT + 1)
    ]


def test_core_release_manifest_freezes_all_input_groups() -> None:
    manifest = generate_core_release_manifest(root=ROOT, architecture_gates=_passing_gates())

    assert manifest["schema_version"] == CORE_RELEASE_MANIFEST_SCHEMA_VERSION
    assert len(manifest["architecture_gates"]) == 15
    assert manifest["migration_head"].endswith(
        "0138_evolution_lab_trusted_sandbox.sql"
    )
    migration_files = manifest["asset_groups"]["migrations"]["files"]
    assert (
        "host_brain/audit/migrations/0104_host_embodiment_runtime_authority.sql"
        in migration_files
    )
    assert "host_brain/audit/migrations/0105_planning_search_calibration.sql" in migration_files
    assert "host_brain/audit/migrations/0106_code_historical_benchmark_ledger.sql" in migration_files
    assert "host_brain/audit/migrations/0107_research_shadow_ledger.sql" in migration_files
    assert "host_brain/audit/migrations/0108_evolution_lab_ledger.sql" in migration_files
    assert "host_brain/audit/migrations/0109_skill_lifecycle.sql" in migration_files
    assert "host_brain/audit/migrations/0110_evolution_governance_ledger.sql" in migration_files
    assert "host_brain/audit/migrations/0111_world_observation_journal.sql" in migration_files
    assert "host_brain/audit/migrations/0112_runtime_context_authority.sql" in migration_files
    assert (
        "host_brain/audit/migrations/0113_service_catalog_rollout_authority.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0114_task_runtime_tool_worker_projection.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0115_host_hard_state_journal.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0116_agent_workspace_lifecycle.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0117_scheduler_clock_retry_authority.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0118_conversation_acceptance_effects.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0119_terminal_operator_signed_command.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0120_task_runtime_feature_canary_receipt.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0121_research_shadow_arm_operation.sql"
        in migration_files
    )
    assert "host_brain/audit/migrations/0122_evolution_lab_v2_evidence.sql" in migration_files
    assert (
        "host_brain/audit/migrations/0123_resource_manager_status_journal.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0124_task_graph_admission_authority.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0125_task_acceptance_effect_staging_authority.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0127_worker_projection_immutability.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0128_acceptance_effect_general_reconciliation.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0129_host_mode_policy_journal.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/0130_task_operation_recovery_hold.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0131_task_operation_recovery_hold_binding.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0132_task_node_budget_reservation.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0134_task_node_acceptance_decision.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0135_task_review_denial_settlement.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0136_planning_search_cohort_ledger.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0137_long_term_goal_authority.sql"
        in migration_files
    )
    assert (
        "host_brain/audit/migrations/"
        "0138_evolution_lab_trusted_sandbox.sql"
        in migration_files
    )
    assert "host_brain/audit/migrations/0103_retire_llm_node_call_log.sql" in migration_files
    assert "host_brain/audit/migrations/0102_task_runtime_node_artifact.sql" in migration_files
    assert "host_brain/audit/migrations/0101_agent_workspace_authority.sql" in migration_files
    assert "host_brain/audit/migrations/0100_task_runtime_graph_revision_supersession.sql" in migration_files
    assert (
        "host_brain/audit/migrations/0099_task_runtime_terminal_reconciliation.sql"
        in migration_files
    )
    assert "host_brain/audit/migrations/0098_task_runtime_effect_reconciliation.sql" in migration_files
    assert "host_brain/audit/migrations/0097_node_grant_batch_uniqueness.sql" in migration_files
    assert "host_brain/audit/migrations/0096_task_runtime_review_continuation.sql" in migration_files
    assert "host_brain/audit/migrations/0095_task_runtime_revisioned_graph_outbox.sql" in migration_files
    assert "host_brain/audit/migrations/0094_task_runtime_graph_cancellation_continuation.sql" in migration_files
    assert "host_brain/audit/migrations/0093_task_runtime_outbox_claim.sql" in migration_files
    assert "host_brain/audit/migrations/0092_task_runtime_graph_cancellation.sql" in migration_files
    assert "host_brain/audit/migrations/0091_task_runtime_parallel_success_decision.sql" in migration_files
    assert "host_brain/audit/migrations/0090_task_runtime_parallel_settlement_decision.sql" in migration_files
    assert "host_brain/audit/migrations/0089_task_runtime_dag_scheduling.sql" in migration_files
    assert "host_brain/audit/migrations/0088_host_command_graph_revision.sql" in migration_files
    assert "host_brain/audit/migrations/0087_task_runtime_acceptance_effect_intent.sql" in migration_files
    assert "host_brain/audit/migrations/0086_task_runtime_worker_projection.sql" in migration_files
    assert "host_brain/audit/migrations/0085_task_runtime_budget_usage.sql" in migration_files
    assert "host_brain/audit/migrations/0084_capability_forge_proposal_log.sql" in migration_files
    assert "host_brain/audit/migrations/0083_host_embodiment_outbox.sql" in migration_files
    assert "edge_client/gateway/migrations/0002_embodiment_command_authority.sql" in migration_files
    assert "edge_client/gateway/migrations/0001_embodiment_transport.sql" in migration_files
    for name in (
        "packages",
        "migrations",
        "contracts",
        "configuration",
        "model_prompt_domain",
        "edge_device",
        "generated_operator_client",
        "release_acceptance",
    ):
        group = manifest["asset_groups"][name]
        assert group["file_count"] > 0
        assert group["digest"].startswith("sha256:")


def test_core_release_manifest_validator_rechecks_checksums() -> None:
    manifest = generate_core_release_manifest(root=ROOT, architecture_gates=_passing_gates())
    manifest["git"]["dirty_paths"] = []

    result = validate_core_release_manifest(root=ROOT, manifest=manifest)

    assert result == {
        "schema_version": CORE_RELEASE_MANIFEST_SCHEMA_VERSION,
        "status": "PASS",
        "issues": [],
    }


def test_core_release_requires_empty_compatibility_registry() -> None:
    assert validate_registry(ROOT, require_empty=True) == {
        "schema_version": "natsume.compatibility-registry.v1",
        "status": "PASS",
        "active_count": 0,
        "usage_count": 0,
        "issues": [],
    }


def test_core_release_retired_authorities_cannot_be_restored() -> None:
    result = validate_retired_paths_absent(ROOT, RETIRED_CORE_PATHS)

    assert result == {
        "schema_version": "natsume.compatibility-registry.v1",
        "status": "PASS",
        "remaining_paths": [],
    }


def test_core_sbom_is_deterministic_cyclonedx_inventory() -> None:
    first = generate_core_sbom(root=ROOT)
    second = generate_core_sbom(root=ROOT)

    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.5"
    assert first["components"]


def test_hashed_monitor_build_output_is_not_tracked() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "host_brain/app/static/monitor"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
