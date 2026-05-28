from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from scripts.runtime.audit_life_memory_coverage import build_coverage_report
from scripts.runtime.run_life_memory_entrypoint_scenario_batch import (
    REQUIRED_ENTRYPOINTS,
    run_entrypoint_scenario_batch,
)


def test_entrypoint_scenario_batch_covers_all_required_life_memory_anchors() -> None:
    report = run_entrypoint_scenario_batch()
    counts = {
        item["entrypoint"]: int(item["count"])
        for item in report["entrypoint_counts"]
    }

    assert report["success"] is True
    assert report["summary"]["critical_issue_count"] == 0
    assert report["summary"]["entrypoint_critical_issue_count"] == 0
    for entrypoint in REQUIRED_ENTRYPOINTS:
        assert counts.get(entrypoint, 0) > 0


def test_coverage_report_flags_missing_required_entrypoint() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        report = build_coverage_report(
            ledger._conn,
            include_synthetic=True,
            required_entrypoints=["surface_chat"],
        )

        assert report["summary"]["entrypoint_critical_issue_count"] == 1
        assert report["entrypoint_issue_counts"] == {"missing_required_entrypoint": 1}
        assert report["entrypoint_issues"][0]["entrypoint"] == "surface_chat"
    finally:
        ledger.close()
