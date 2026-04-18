from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_resolve_fetch_table_skips_legacy_invalid_name() -> None:
    ledger = SQLiteLedger(":memory:")
    assert ledger.can_fetch_table("qa_violation_log") is True
    assert ledger.can_fetch_table("qa_violation") is False
    assert ledger.resolve_fetch_table("qa_violation", "qa_violation_log") == "qa_violation_log"


def test_resolve_fetch_table_returns_none_when_no_candidate_exists() -> None:
    ledger = SQLiteLedger(":memory:")
    assert ledger.resolve_fetch_table("episodic_store_log", "talk_gate_log") is None
