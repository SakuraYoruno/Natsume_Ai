from __future__ import annotations

from pathlib import Path

from scripts.runtime.run_task_terminal_reconciliation_canary import run_canary


ROOT = Path(__file__).resolve().parents[2]


def test_real_sqlite_terminal_reconciliation_canary() -> None:
    receipt = run_canary(repository_root=ROOT)

    expected_head = max(
        (ROOT / "host_brain" / "audit" / "migrations").glob("*.sql"),
        key=lambda path: (int(path.name[:4]), path.name),
    )

    assert receipt["status"] == "PASS"
    assert receipt["database_kind"] == "ISOLATED_REAL_SQLITE"
    assert receipt["migration_head"] == expected_head.name
    assert receipt["migration_head_version"] == int(expected_head.name[:4])
    assert receipt["terminal_inventory"] == {
        "candidate_count": 2,
        "exact_repairable_count": 1,
        "non_exact_count": 1,
    }
    assert receipt["exact_auto"]["status"] == "PASS"
    assert receipt["exact_auto"]["reconciled_count"] == 1
    assert receipt["exact_auto"]["authority_kind"] == "SYSTEM_DURABLE_EVIDENCE"
    assert receipt["exact_auto"]["graph_status"] == "FAILED"
    assert receipt["exact_auto"]["budget_status"] == "RELEASED"
    assert receipt["non_exact_fence"]["status"] == "PASS"
    assert receipt["non_exact_fence"]["proof_absent"] is True
    assert receipt["non_exact_fence"]["graph_status"] == "CANCELLED"
    assert receipt["non_exact_fence"]["budget_status"] == "ACTIVE"
    assert (
        receipt["non_exact_fence"]["production_gate"]["status"]
        == "BLOCKED_AS_EXPECTED"
    )
    assert receipt["operator_bundle_preflight"]["status"] == "PASS"
    assert receipt["operator_bundle_preflight"]["private_key_persisted"] is False
    assert receipt["external_side_effects"] == 0
    assert receipt["private_or_user_data_used"] is False
    assert receipt["temporary_database_removed"] is True
