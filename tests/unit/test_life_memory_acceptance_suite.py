from __future__ import annotations

from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore, RETENTION_WARM
from scripts.runtime.run_life_memory_acceptance_suite import run_acceptance_suite
from scripts.runtime.run_life_memory_entrypoint_scenario_batch import _seed_entrypoints


def test_life_memory_acceptance_suite_passes_with_seeded_runtime_db(tmp_path: Path) -> None:
    db_path = tmp_path / "acceptance.sqlite3"
    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        _seed_entrypoints(store=store)
        for index, summary in enumerate(
            [
                "后台整理普通候选标题。",
                "后台确认普通候选链接。",
            ],
            start=1,
        ):
            store.write_background_episode(
                event_source="scheduler",
                event_id=f"acceptance-compact-{index}",
                task_id="background_progress",
                person_id="owner",
                session_id="acceptance_session",
                status="OK",
                detail=summary,
                ts_ms=1_800_000_000_000 + index,
                metadata={"retention_state": RETENTION_WARM},
            )
    finally:
        ledger.close()

    report = run_acceptance_suite(
        db_path=db_path,
        include_synthetic=True,
        include_seeded_e2e=False,
    )

    assert report["success"] is True
    assert report["failed_count"] == 0
    check_ids = {item["check_id"] for item in report["checks"]}
    assert {
        "deterministic_recall_scenarios",
        "deterministic_entrypoint_coverage",
        "production_ledger_coverage",
        "sleep_replay_dry_run",
    }.issubset(check_ids)
    sleep = next(item for item in report["checks"] if item["check_id"] == "sleep_replay_dry_run")
    assert sleep["summary"]["protected_compaction_violation_count"] == 0


def test_life_memory_acceptance_suite_fails_missing_runtime_db(tmp_path: Path) -> None:
    report = run_acceptance_suite(
        db_path=tmp_path / "missing.sqlite3",
        include_synthetic=True,
        include_seeded_e2e=False,
    )

    assert report["success"] is False
    failed = {item["check_id"] for item in report["checks"] if not item["passed"]}
    assert {"production_ledger_coverage", "sleep_replay_dry_run"}.issubset(failed)
