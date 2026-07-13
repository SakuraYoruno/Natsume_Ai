from __future__ import annotations

from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_tactic_ledger_persists_recent_tactics_per_topic() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = SessionTacticLedgerStore(ledger)

    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-1",
        topic_key="identity",
        interaction_move="reconfirm",
        tactic_family="ack_name_then_wait",
        tactic_variant="probing",
        trajectory_stage="early_repeat",
        ts_ms=1000,
    )
    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-2",
        topic_key="identity",
        interaction_move="probe",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="probing",
        trajectory_stage="mid_repeat",
        ts_ms=2000,
    )

    block = store.fetch_block(session_id="sess-1")
    assert block.recent_tactic_families("identity") == (
        "ack_repeat_then_light_probe",
        "ack_name_then_wait",
    )


def test_tactic_ledger_ignores_fallback_entries_for_rotation() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = SessionTacticLedgerStore(ledger)

    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-1",
        topic_key="identity",
        interaction_move="reconfirm",
        tactic_family="fallback_minimal",
        tactic_variant="fallback",
        trajectory_stage="late_repeat",
        ts_ms=3000,
    )
    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-2",
        topic_key="identity",
        interaction_move="probe",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="probing",
        trajectory_stage="mid_repeat",
        ts_ms=2000,
    )

    block = store.fetch_block(session_id="sess-1")
    assert block.recent_tactic_families("identity") == ("ack_repeat_then_light_probe",)


def test_tactic_ledger_exposes_recent_signatures() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = SessionTacticLedgerStore(ledger)

    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-1",
        topic_key="identity",
        interaction_move="reconfirm",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="confirm_part",
        trajectory_stage="mid_repeat",
        ts_ms=2000,
    )
    store.record_reply_plan(
        session_id="sess-1",
        request_id="req-2",
        topic_key="identity",
        interaction_move="reconfirm",
        tactic_family="hold_name_then_open_floor",
        tactic_variant="open_other_topic",
        trajectory_stage="late_repeat",
        ts_ms=3000,
    )

    block = store.fetch_block(session_id="sess-1")
    assert block.recent_tactic_signatures("identity") == (
        "identity_open_other_topic",
        "identity_confirm_part",
    )
