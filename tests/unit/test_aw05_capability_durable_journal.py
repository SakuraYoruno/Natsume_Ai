from __future__ import annotations

import threading
import time

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.broker import CapabilityBroker, CapabilityDescriptor
from host_brain.capability_plane.broker import CapabilityInvocationError
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest, ReceiptStatus
from host_brain.capability_plane.journal import SQLiteCapabilityExecutionJournal
from host_brain.capability_plane.policies import PredicateCapabilityPolicy


def _allow() -> PredicateCapabilityPolicy:
    return PredicateCapabilityPolicy(
        lambda request, _descriptor: (True, "OK", f"governed://{request.request_id}")
    )


def _request(*, key: str, deadline_at_ms: int | None = None) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="request-1",
        attempt_id="attempt-1",
        idempotency_key=key,
        capability_id="demo",
        kind=CapabilityKind.DOMAIN,
        args={"value": 1},
        context={"principal_id": "host-task"},
        created_at_ms=int(time.time() * 1000),
        deadline_at_ms=deadline_at_ms,
    )


def _broker(ledger: SQLiteLedger, calls: list[int], adapter=None) -> CapabilityBroker:
    broker = CapabilityBroker(
        policy=_allow(),
        execution_journal=SQLiteCapabilityExecutionJournal(ledger),
    )
    broker.register(CapabilityDescriptor(
        "demo",
        CapabilityKind.DOMAIN,
        "1",
        2,
        adapter or (lambda *_args: calls.append(1) or {"ok": True}),
    ))
    return broker


def test_two_brokers_share_one_durable_receipt_and_do_not_repeat_side_effect(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ledger_a = SQLiteLedger(db_path=db_path, check_same_thread=False)
    ledger_b = SQLiteLedger(db_path=db_path, check_same_thread=False)
    calls: list[int] = []

    first = _broker(ledger_a, calls).execute(_request(key="shared-key"))
    replay = _broker(ledger_b, calls).execute(_request(key="shared-key"))

    assert first.status is ReceiptStatus.SUCCEEDED
    assert replay.status is ReceiptStatus.SUCCEEDED
    assert replay.replayed is True
    assert calls == [1]
    rows = ledger_a.fetch_by_time_range("capability_execution_receipt", limit=10)
    assert len(rows) == 1
    assert rows[0]["request_hash"].startswith("sha256:")


def test_abandoned_claim_requires_reconciliation_instead_of_takeover(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "abandoned.sqlite3", check_same_thread=False)
    journal = SQLiteCapabilityExecutionJournal(ledger)
    request = _request(key="abandoned")
    journal.claim(
        request,
        owner_token="dead-worker",
        now_ms=10,
        lease_expires_at_ms=11,
    )
    calls: list[int] = []

    receipt = _broker(ledger, calls).execute(request)

    assert receipt.status is ReceiptStatus.IN_DOUBT
    assert receipt.reason_code == "CAPABILITY_EXECUTION_RECONCILIATION_REQUIRED"
    assert calls == []


def test_timed_out_running_adapter_is_in_doubt_and_late_outcome_is_audited(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "timeout.sqlite3", check_same_thread=False)
    calls: list[int] = []
    completed = threading.Event()

    def _slow(*_args):
        time.sleep(0.06)
        calls.append(1)
        completed.set()
        return {"late": True}

    deadline = int(time.time() * 1000) + 20
    receipt = _broker(ledger, calls, adapter=_slow).execute(
        _request(key="timeout", deadline_at_ms=deadline)
    )

    assert receipt.status is ReceiptStatus.IN_DOUBT
    assert receipt.reason_code == "CAPABILITY_ADAPTER_OUTCOME_UNKNOWN"
    assert completed.wait(timeout=1)
    for _ in range(20):
        rows = ledger.fetch_by_time_range("capability_execution_late_outcome", limit=10)
        if rows:
            break
        time.sleep(0.01)
    assert calls == [1]
    assert rows[0]["status"] == "LATE_SUCCEEDED"
    replay = _broker(ledger, calls).execute(_request(key="timeout", deadline_at_ms=deadline))
    assert replay.status is ReceiptStatus.IN_DOUBT
    assert replay.replayed is True
    assert calls == [1]


def test_durable_receipt_redacts_secret_output_and_replay_is_identical(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "redaction.sqlite3", check_same_thread=False)
    calls: list[int] = []

    def _secret_output(*_args):
        calls.append(1)
        return {
            "api_key": "sk-secret-material",
            "token": "tok_live_SUPERSECRET",
            "approval_token_ref": "approve_live_SUPERSECRET",
            "credential": "credential_live_SUPERSECRET",
            "message": "authorization=Bearer sensitive-token safe text",
        }

    broker = _broker(ledger, calls, adapter=_secret_output)
    first = broker.execute(_request(key="secret-output"))
    replay = broker.execute(_request(key="secret-output"))

    assert first.output == replay.output
    assert first.output["api_key"] == "[REDACTED]"
    assert first.output["token"] == "[REDACTED]"
    assert first.output["approval_token_ref"] == "[REDACTED]"
    assert first.output["credential"] == "[REDACTED]"
    assert "sensitive-token" not in first.output["message"]
    assert calls == [1]
    durable = ledger.fetch_by_time_range("capability_execution_receipt", limit=10)[0]
    assert "sk-secret-material" not in str(durable)
    assert "sensitive-token" not in str(durable)
    assert "SUPERSECRET" not in str(durable)


def test_durable_failure_detail_is_redacted(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "failure.sqlite3", check_same_thread=False)
    calls: list[int] = []

    def _failure(*_args):
        raise CapabilityInvocationError(
            "DEMO_FAILED",
            "credential=credential_live_SUPERSECRET token=tok_live_SUPERSECRET",
        )

    receipt = _broker(ledger, calls, adapter=_failure).execute(
        _request(key="secret-failure")
    )

    assert receipt.status is ReceiptStatus.FAILED
    assert "SUPERSECRET" not in receipt.detail
    durable = ledger.fetch_by_time_range("capability_execution_receipt", limit=10)[0]
    assert "SUPERSECRET" not in str(durable)


def test_object_string_and_bare_token_refs_are_redacted_from_receipt(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "object.sqlite3", check_same_thread=False)
    calls: list[int] = []

    class _SecretObject:
        def __str__(self) -> str:
            return "token=OBJECT_SUPERSECRET"

    object_receipt = _broker(
        ledger,
        calls,
        adapter=lambda *_args: _SecretObject(),
    ).execute(_request(key="secret-object"))

    def _bare_ref_failure(*_args):
        raise CapabilityInvocationError(
            "DEMO_FAILED",
            "failed with approval::BARE_SUPERSECRET and confirm::CONFIRM_SUPERSECRET",
        )

    failure_receipt = _broker(ledger, calls, adapter=_bare_ref_failure).execute(
        _request(key="bare-token-ref")
    )

    durable = ledger.fetch_by_time_range("capability_execution_receipt", limit=10)
    assert "SUPERSECRET" not in str(object_receipt.output)
    assert "SUPERSECRET" not in failure_receipt.detail
    assert "SUPERSECRET" not in str(durable)
