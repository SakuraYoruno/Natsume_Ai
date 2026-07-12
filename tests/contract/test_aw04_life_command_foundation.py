from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.contracts import LifeCommand, LifeCommandStatus
from host_brain.digital_life.outbox import LifeOutboxDispatcher
from host_brain.digital_life.projection import LifeProjectionDispatcher
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.reducer import DeterministicLifeReducer
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import (
    LIFE_AGGREGATE_CLAIM_FENCED,
    LIFE_COMMAND_BASELINE_REQUIRED,
    LIFE_COMMAND_CLAIM_FENCED,
    LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
    LifeCommandRepository,
    LifeCommandRepositoryError,
)


NOW = 1_800_000_000_000
DELIVERY_AUTHORIZATION = {
    "request_id": "request-delivery-authority",
    "governor_grant_ref": "governor-decision://sha256/" + "a" * 64,
    "authority_kind": "contract_test",
    "authority_principal_id": "principal-contract-test",
}


class MutableClock:
    def __init__(self, value: int = NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _repository(
    tmp_path: Path,
    *,
    fault_injector=None,  # noqa: ANN001
) -> tuple[Path, LifeCommandRepository]:
    db_path = tmp_path / "digital-life.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw04-life-foundation",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    return db_path, LifeCommandRepository(
        db_path,
        fault_injector=fault_injector,
    )


def _command(
    command_id: str,
    *,
    expected_version: int = 0,
    person_id: str = "person-1",
    deliveries: bool = True,
) -> LifeCommand:
    return LifeCommand(
        command_id=command_id,
        person_id=person_id,
        command_type="tick",
        expected_version=expected_version,
        created_at_ms=NOW + expected_version,
        payload={
            "state_patch": {"mood": "warm", "counters": {"ticks": expected_version + 1}},
            "organ_intents": [
                {"organ_id": "curiosity", "payload": {"drive": 0.6}}
            ],
            "projection_intents": [
                {"projection_id": "surface", "payload": {"tone": "gentle"}}
            ],
            "deliveries": (
                [
                    {
                        "destination": "gateway",
                        "payload": {"text": "hello"},
                        "authorization": DELIVERY_AUTHORIZATION,
                    }
                ]
                if deliveries
                else []
            ),
        },
    )


def _count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def test_submit_is_one_atomic_idempotent_command_event_projection_and_outbox(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    command = _command("command-1")

    first = service.submit(command)
    second = service.submit(command)

    assert first.status is LifeCommandStatus.APPLIED
    assert first.state_version == 1
    assert first.outbox_count == 1
    assert second.as_dict() == {**first.as_dict(), "replayed": True}
    assert reader.get_receipt(command.command_id) == first
    assert reader.get_snapshot("person-1").state["mood"] == "warm"
    assert [event.sequence for event in reader.list_events("person-1")] == [1]
    assert _count(db_path, "life_command") == 1
    assert _count(db_path, "life_event_journal") == 1
    assert _count(db_path, "life_projection_intent") == 2
    assert _count(db_path, "life_delivery_outbox") == 1
    assert _count(db_path, "life_command_receipt") == 1


def test_command_uow_fault_rolls_back_every_domain_record(tmp_path: Path) -> None:
    def fault(stage: str) -> None:
        if stage == "after_outbox":
            raise RuntimeError("injected crash")

    db_path, repository = _repository(tmp_path, fault_injector=fault)
    service = LifeCommandService(repository, clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="injected crash"):
        service.submit(_command("command-rollback"))

    for table in (
        "life_command",
        "life_event_journal",
        "life_projection_intent",
        "life_projection_claim",
        "life_projection_receipt",
        "life_delivery_outbox",
        "life_delivery_receipt",
        "life_command_receipt",
        "digital_life_state",
    ):
        assert _count(db_path, table) == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT claim_expires_at_ms FROM life_command_claim
            WHERE command_id = 'command-rollback'
            """
        ).fetchone() == (0,)


def test_claim_lease_fences_stale_command_owner(tmp_path: Path) -> None:
    _db_path, repository = _repository(tmp_path)
    command = _command("command-fence", deliveries=False)
    first = repository.try_claim_command(
        command,
        owner_token="owner-1",
        lease_ms=10,
        now_ms=NOW,
    )
    assert first is not None
    second = repository.try_claim_command(
        command,
        owner_token="owner-2",
        lease_ms=10,
        now_ms=NOW + 11,
    )
    assert second is not None
    preview = DeterministicLifeReducer().reduce(repository.get_snapshot("person-1"), command)

    with pytest.raises(LifeCommandRepositoryError) as exc_info:
        repository.commit_command(preview, claim=first, now_ms=NOW + 11)  # type: ignore[arg-type]
    assert exc_info.value.reason_code == LIFE_COMMAND_CLAIM_FENCED
    receipt = repository.commit_command(
        preview,
        claim=second,  # type: ignore[arg-type]
        now_ms=NOW + 11,
    )
    assert receipt.status is LifeCommandStatus.APPLIED


def test_shared_service_serializes_concurrent_commands_per_person(tmp_path: Path) -> None:
    _db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW)

    def submit(label: str):  # noqa: ANN202
        return service.submit_next(
            "person-1",
            lambda snapshot: _command(
                f"concurrent-{label}",
                expected_version=snapshot.version,
                deliveries=False,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(submit, ("a", "b")))

    assert all(receipt.status is LifeCommandStatus.APPLIED for receipt in receipts)
    assert sorted(receipt.state_version for receipt in receipts) == [1, 2]
    assert repository.get_snapshot("person-1").version == 2


def test_preview_and_replay_are_read_only_and_rebuild_is_deterministic(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    first = _command("command-replay-1", deliveries=False)

    preview = service.preview(first)
    assert preview.after.version == 1
    assert _count(db_path, "life_command") == 0
    assert _count(db_path, "life_event_journal") == 0
    assert _count(db_path, "life_delivery_outbox") == 0

    service.submit(first)
    service.submit(_command("command-replay-2", expected_version=1, deliveries=False))
    durable = reader.get_snapshot("person-1")
    replayed_once = service.replay("person-1")
    replayed_twice = service.replay("person-1")
    assert replayed_once.as_dict() == durable.as_dict()
    assert replayed_twice.as_dict() == replayed_once.as_dict()
    for domain in (
        "world",
        "appraisal",
        "affect",
        "homeostasis",
        "relationship",
        "scene",
        "intent",
    ):
        assert replayed_once.state[domain] == durable.state[domain]

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM digital_life_state WHERE person_id = 'person-1'")
        conn.commit()
    assert _count(db_path, "digital_life_state") == 0
    assert service.replay("person-1").as_dict() == replayed_once.as_dict()
    rebuilt = service.rebuild("person-1")
    assert rebuilt.as_dict() == replayed_once.as_dict()
    assert reader.get_snapshot("person-1").as_dict() == replayed_once.as_dict()


def test_delivery_outbox_recovers_crash_gap_with_same_delivery_key(tmp_path: Path) -> None:
    db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW)
    service.submit(_command("command-delivery"))

    abandoned = repository.claim_deliveries(
        owner_token="crashed-worker",
        now_ms=NOW,
        lease_ms=10,
        limit=10,
    )
    assert len(abandoned) == 1
    calls: list[str] = []
    dispatcher = LifeOutboxDispatcher(
        repository,
        lambda message: calls.append(message.delivery_key) or {"message_id": "sent"},
        owner_id="recovery-worker",
        clock=lambda: NOW + 11,
        claim_lease_ms=10,
    )

    summary = dispatcher.dispatch_pending(dispatch_at_ms=NOW + 11)

    assert summary.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert calls == [abandoned[0].delivery_key]
    row = repository.list_outbox(command_id="command-delivery")[0]
    assert row["status"] == "DELIVERED"
    assert row["attempt_count"] == 2
    assert row["authorization_request_id"] == DELIVERY_AUTHORIZATION["request_id"]
    assert row["governor_grant_ref"] == DELIVERY_AUTHORIZATION["governor_grant_ref"]
    receipt = repository.get_delivery_receipt(row["outbox_id"])
    assert receipt is not None
    assert receipt.delivery_key == row["delivery_key"]
    assert receipt.payload_digest == row["payload_digest"]
    assert receipt.authorization_digest == row["authorization_digest"]
    replayed = repository.complete_delivery(
        abandoned[0],
        owner_token="crashed-worker",
        result={"late": True},
        now_ms=NOW + 12,
    )
    assert replayed == receipt
    assert _count(db_path, "life_delivery_receipt") == 1
    assert repository.retry_delivery(row["outbox_id"], now_ms=NOW + 13) is False


def test_projection_dispatcher_recovers_crash_gap_with_exactly_one_receipt(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(
        _command("projection-crash", deliveries=False)
    )
    abandoned = repository.claim_projection_intents(
        owner_token="crashed-projector",
        now_ms=NOW,
        lease_ms=10,
        limit=1,
    )
    assert len(abandoned) == 1
    calls: list[str] = []
    dispatcher = LifeProjectionDispatcher(
        repository,
        lambda message: calls.append(message.intent_id) or {"applied": True},
        owner_id="projection-recovery",
        clock=lambda: NOW + 11,
        claim_lease_ms=10,
    )

    summary = dispatcher.dispatch_pending(dispatch_at_ms=NOW + 11)

    assert summary.as_dict() == {"claimed": 2, "delivered": 2, "failed": 0}
    assert abandoned[0].intent_id in calls
    assert _count(db_path, "life_projection_receipt") == 2
    replayed_receipt = repository.complete_projection(
        abandoned[0],
        owner_token="crashed-projector",
        result={"late": True},
        now_ms=NOW + 12,
    )
    assert replayed_receipt.intent_id == abandoned[0].intent_id
    assert _count(db_path, "life_projection_receipt") == 2


def test_async_projection_dispatcher_retries_failed_lease(tmp_path: Path) -> None:
    _db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(
        _command("projection-async", deliveries=False)
    )

    async def handler(message):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        if message.target == "curiosity":
            raise RuntimeError("organ unavailable")
        return {"target": message.target}

    dispatcher = LifeProjectionDispatcher(
        repository,
        handler,
        owner_id="projection-async",
        clock=lambda: NOW,
        base_retry_ms=5,
    )
    first = asyncio.run(dispatcher.dispatch_pending_async(dispatch_at_ms=NOW))

    assert first.as_dict() == {"claimed": 2, "delivered": 1, "failed": 1}
    claims = repository.list_projection_claims()
    assert len(claims) == 1
    assert claims[0]["status"] == "FAILED"
    assert claims[0]["claim_owner"] is None
    assert dispatcher.retry_projection(claims[0]["intent_id"], retry_at_ms=NOW + 1)

    async def recovered(message):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        return {"target": message.target}

    recovery = LifeProjectionDispatcher(
        repository,
        recovered,
        owner_id="projection-async-recovery",
        clock=lambda: NOW + 1,
    )
    second = asyncio.run(recovery.dispatch_pending_async(dispatch_at_ms=NOW + 1))
    assert second.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert repository.list_projection_claims() == []


def test_async_delivery_dispatcher_completes_and_retries_failed_leases(
    tmp_path: Path,
) -> None:
    _db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW + 2)
    service.submit(_command("async-ok"))
    service.submit(_command("async-fail", expected_version=1))

    async def handler(message):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        if message.command_id == "async-fail":
            raise RuntimeError("transport down")
        return {"transport_id": message.delivery_key}

    dispatcher = LifeOutboxDispatcher(
        repository,
        handler,
        owner_id="async-worker",
        clock=lambda: NOW + 2,
        claim_lease_ms=10,
    )
    first = asyncio.run(dispatcher.dispatch_pending_async(dispatch_at_ms=NOW + 2))

    assert first.as_dict() == {"claimed": 2, "delivered": 1, "failed": 1}
    rows = {row["command_id"]: row for row in repository.list_outbox()}
    assert rows["async-ok"]["status"] == "DELIVERED"
    assert rows["async-ok"]["claim_owner"] is None
    assert repository.get_delivery_receipt(rows["async-ok"]["outbox_id"]) is not None
    assert rows["async-fail"]["status"] == "FAILED"
    assert rows["async-fail"]["claim_owner"] is None
    assert dispatcher.retry_delivery(
        rows["async-fail"]["outbox_id"],
        retry_at_ms=NOW + 3,
    )

    async def recovered(message):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        return {"transport_id": message.delivery_key}

    recovery = LifeOutboxDispatcher(
        repository,
        recovered,
        owner_id="async-recovery",
        clock=lambda: NOW + 3,
        claim_lease_ms=10,
    )
    second = asyncio.run(recovery.dispatch_pending_async(dispatch_at_ms=NOW + 3))
    assert second.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    recovered_row = repository.list_outbox(command_id="async-fail")[0]
    assert recovered_row["status"] == "DELIVERED"
    assert recovered_row["attempt_count"] == 2
    assert len(repository.list_delivery_receipts()) == 2


def test_delivery_without_typed_authorization_fails_before_domain_commit(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    command = LifeCommand(
        command_id="delivery-without-authority",
        person_id="person-1",
        command_type="tick",
        payload={
            "deliveries": [
                {"destination": "proactive.gateway", "payload": {"text": "no"}}
            ]
        },
        expected_version=0,
        created_at_ms=NOW,
    )

    with pytest.raises(ValueError, match="authorization is required"):
        LifeCommandService(repository, clock=lambda: NOW).submit(command)

    assert _count(db_path, "life_command") == 0
    assert _count(db_path, "life_event_journal") == 0
    assert _count(db_path, "life_delivery_outbox") == 0
    assert _count(db_path, "life_delivery_receipt") == 0


def test_delivery_receipt_and_outbox_authority_are_database_immutable(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(
        _command("immutable-delivery-audit")
    )
    LifeOutboxDispatcher(
        repository,
        lambda message: {"transport_id": message.delivery_key},
        clock=lambda: NOW + 1,
    ).dispatch_pending(dispatch_at_ms=NOW + 1)
    outbox_id = repository.list_outbox()[0]["outbox_id"]

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="receipt is immutable"):
            conn.execute(
                "UPDATE life_delivery_receipt SET result_json = '{}' WHERE outbox_id = ?",
                (outbox_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="receipt is immutable"):
            conn.execute(
                "DELETE FROM life_delivery_receipt WHERE outbox_id = ?",
                (outbox_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="authority are immutable"):
            conn.execute(
                "UPDATE life_delivery_outbox SET governor_grant_ref = 'changed' WHERE outbox_id = ?",
                (outbox_id,),
            )


def test_dispatchers_claim_only_one_item_before_each_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(_command("single-claim"))
    projection_limits: list[int] = []
    delivery_limits: list[int] = []
    original_projection_claim = repository.claim_projection_intents
    original_delivery_claim = repository.claim_deliveries

    def claim_projection(**kwargs):  # noqa: ANN003, ANN202
        projection_limits.append(int(kwargs["limit"]))
        return original_projection_claim(**kwargs)

    def claim_delivery(**kwargs):  # noqa: ANN003, ANN202
        delivery_limits.append(int(kwargs["limit"]))
        return original_delivery_claim(**kwargs)

    monkeypatch.setattr(repository, "claim_projection_intents", claim_projection)
    monkeypatch.setattr(repository, "claim_deliveries", claim_delivery)
    projection = LifeProjectionDispatcher(
        repository,
        lambda message: {"intent_id": message.intent_id},
        clock=lambda: NOW,
    ).dispatch_pending(limit=10, dispatch_at_ms=NOW)
    delivery = LifeOutboxDispatcher(
        repository,
        lambda message: {"delivery_key": message.delivery_key},
        clock=lambda: NOW,
    ).dispatch_pending(limit=10, dispatch_at_ms=NOW)

    assert projection.delivered == 2
    assert delivery.delivered == 1
    assert projection_limits and set(projection_limits) == {1}
    assert delivery_limits and set(delivery_limits) == {1}


def test_projection_receipt_requires_durable_intent_identity_and_live_lease(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(
        _command("projection-fence", deliveries=False)
    )
    message = repository.claim_projection_intents(
        owner_token="projection-owner-a",
        now_ms=NOW,
        lease_ms=10,
        limit=1,
    )[0]

    with pytest.raises(LifeCommandRepositoryError, match="durable intent identity"):
        repository.renew_projection_claim(
            replace(message, target="forged-target"),
            owner_token="projection-owner-a",
            now_ms=NOW + 1,
            lease_ms=10,
        )
    with pytest.raises(LifeCommandRepositoryError) as expired:
        repository.complete_projection(
            message,
            owner_token="projection-owner-a",
            result={"late": True},
            now_ms=NOW + 11,
        )
    assert expired.value.reason_code == "LIFE_PROJECTION_CLAIM_FENCED"
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable intent"):
            conn.execute(
                """
                INSERT INTO life_projection_receipt(
                    intent_id, payload_digest, result_json, completed_at_ms
                ) VALUES (?, ?, '{}', ?)
                """,
                (message.intent_id, "f" * 64, NOW + 11),
            )

    reclaimed = repository.claim_projection_intents(
        owner_token="projection-owner-b",
        now_ms=NOW + 11,
        lease_ms=10,
        limit=1,
    )[0]
    assert reclaimed.intent_id == message.intent_id
    assert reclaimed.fencing_token > message.fencing_token


def test_delivery_completion_requires_live_lease_before_receipt(
    tmp_path: Path,
) -> None:
    _db_path, repository = _repository(tmp_path)
    LifeCommandService(repository, clock=lambda: NOW).submit(_command("delivery-fence"))
    message = repository.claim_deliveries(
        owner_token="delivery-owner-a",
        now_ms=NOW,
        lease_ms=10,
        limit=1,
    )[0]

    with pytest.raises(LifeCommandRepositoryError, match="durable outbox identity"):
        repository.renew_delivery_claim(
            replace(message, payload={"forged": True}),
            owner_token="delivery-owner-a",
            now_ms=NOW + 1,
            lease_ms=10,
        )
    with pytest.raises(LifeCommandRepositoryError) as expired:
        repository.complete_delivery(
            message,
            owner_token="delivery-owner-a",
            result={"late": True},
            now_ms=NOW + 11,
        )
    assert expired.value.reason_code == "LIFE_DELIVERY_CLAIM_FENCED"
    reclaimed = repository.claim_deliveries(
        owner_token="delivery-owner-b",
        now_ms=NOW + 11,
        lease_ms=10,
        limit=1,
    )[0]
    assert reclaimed.delivery_key == message.delivery_key
    assert reclaimed.fencing_token > message.fencing_token


def test_two_services_serialize_implicit_next_across_process_boundary(
    tmp_path: Path,
) -> None:
    db_path, first_repository = _repository(tmp_path)
    second_repository = LifeCommandRepository(db_path)
    first_service = LifeCommandService(
        first_repository,
        clock=lambda: NOW,
        owner_id="service-a",
        claim_wait_timeout_ms=2_000,
    )
    second_service = LifeCommandService(
        second_repository,
        clock=lambda: NOW,
        owner_id="service-b",
        claim_wait_timeout_ms=2_000,
    )
    first_factory_entered = threading.Event()

    def submit_first():  # noqa: ANN202
        def factory(snapshot):  # noqa: ANN001, ANN202
            first_factory_entered.set()
            time.sleep(0.05)
            return _command(
                "aggregate-concurrent-a",
                expected_version=snapshot.version,
                deliveries=False,
            )

        return first_service.submit_next("person-1", factory)

    def submit_second():  # noqa: ANN202
        assert first_factory_entered.wait(timeout=1.0)
        return second_service.submit_next(
            "person-1",
            lambda snapshot: _command(
                "aggregate-concurrent-b",
                expected_version=snapshot.version,
                deliveries=False,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = [
            future.result(timeout=3.0)
            for future in (pool.submit(submit_first), pool.submit(submit_second))
        ]

    assert [receipt.status for receipt in receipts] == [
        LifeCommandStatus.APPLIED,
        LifeCommandStatus.APPLIED,
    ]
    assert sorted(receipt.state_version for receipt in receipts) == [1, 2]
    assert first_repository.get_snapshot("person-1").version == 2


def test_aggregate_lease_expiry_takeover_fences_stale_owner(tmp_path: Path) -> None:
    _db_path, repository = _repository(tmp_path)
    stale = repository.acquire_aggregate_claim(
        "person-1",
        owner_token="aggregate-owner-a",
        lease_ms=10,
        now_ms=NOW,
    )
    assert stale is not None
    command = _command("aggregate-fenced-command", deliveries=False)
    command_claim = repository.try_claim_command(
        command,
        owner_token="command-owner-a",
        lease_ms=100,
        now_ms=NOW,
    )
    assert command_claim is not None
    preview = DeterministicLifeReducer().reduce(
        repository.get_snapshot("person-1"),
        command,
    )
    takeover = repository.acquire_aggregate_claim(
        "person-1",
        owner_token="aggregate-owner-b",
        lease_ms=10,
        now_ms=NOW + 10,
    )
    assert takeover is not None
    assert takeover.fencing_token == stale.fencing_token + 1

    with pytest.raises(LifeCommandRepositoryError) as exc_info:
        repository.commit_command(
            preview,
            claim=command_claim,  # type: ignore[arg-type]
            aggregate_claim=stale,
            now_ms=NOW + 10,
        )
    assert exc_info.value.reason_code == LIFE_AGGREGATE_CLAIM_FENCED
    assert repository.release_aggregate_claim(stale, now_ms=NOW + 10) is False
    assert repository.release_aggregate_claim(takeover, now_ms=NOW + 10) is True


def test_command_idempotency_identity_ignores_expected_version_only(
    tmp_path: Path,
) -> None:
    _db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW)
    original = _command("idempotency-expected-version", deliveries=False)
    first = service.submit(original)
    retry = replace(
        original,
        expected_version=99,
        created_at_ms=NOW + 99,
    )

    replayed = service.submit(retry)

    assert replayed.as_dict() == {**first.as_dict(), "replayed": True}
    changed = replace(original, payload={"state_patch": {"mood": "different"}})
    with pytest.raises(LifeCommandRepositoryError) as exc_info:
        service.submit(changed)
    assert exc_info.value.reason_code == LIFE_COMMAND_IDEMPOTENCY_CONFLICT


def test_delayed_tick_never_rolls_back_foreground_freshness(tmp_path: Path) -> None:
    _db_path, repository = _repository(tmp_path)
    service = LifeCommandService(repository, clock=lambda: NOW + 300)
    service.submit(
        LifeCommand(
            command_id="foreground-newer-than-tick",
            person_id="person-1",
            command_type="foreground_completed",
            payload={"assistant_reply": "newer foreground"},
            expected_version=0,
            created_at_ms=NOW + 200,
        )
    )
    before = repository.get_snapshot("person-1")
    service.submit(
        LifeCommand(
            command_id="delayed-scheduler-tick",
            person_id="person-1",
            command_type="tick",
            payload={"scheduled_at_ms": NOW, "source": "scheduler_clock"},
            expected_version=1,
            created_at_ms=NOW + 100,
        )
    )

    after = repository.get_snapshot("person-1")
    assert after.updated_at_ms >= before.updated_at_ms
    assert after.state["clock"]["last_integrated_ms"] >= before.updated_at_ms
    assert after.state["runtime"]["last_integrated_ms"] >= before.updated_at_ms
    assert after.state["last_command_at_ms"] >= before.updated_at_ms


def test_multi_person_legacy_projection_requires_explicit_ordered_adoption(
    tmp_path: Path,
) -> None:
    db_path, repository = _repository(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms)
            VALUES (?, 2, ?, ?)
            """,
            [
                ("person-z", '{"mood":"old-z","person_id":"person-z","version":2}', NOW - 2),
                ("person-a", '{"mood":"old-a","person_id":"person-a","version":2}', NOW - 1),
            ],
        )
        conn.commit()
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    assert reader.list_unadopted_projection_person_ids() == ["person-a", "person-z"]

    with pytest.raises(LifeCommandRepositoryError) as exc_info:
        service.submit(
            _command(
                "legacy-before-adoption",
                person_id="person-a",
                expected_version=2,
                deliveries=False,
            )
        )
    assert exc_info.value.reason_code == LIFE_COMMAND_BASELINE_REQUIRED

    with pytest.raises(LifeCommandRepositoryError) as limit_error:
        service.adopt_all_legacy_projections(adopted_at_ms=NOW, limit=1)
    assert limit_error.value.reason_code == "LIFE_COMMAND_ADOPTION_LIMIT_EXCEEDED"
    assert reader.list_unadopted_projection_person_ids() == ["person-a", "person-z"]

    receipts = service.adopt_all_legacy_projections(adopted_at_ms=NOW)
    assert [receipt.person_id for receipt in receipts] == ["person-a", "person-z"]
    assert reader.list_unadopted_projection_person_ids() == []
    for person_id in ("person-a", "person-z"):
        replayed = service.replay(person_id)
        assert replayed.version == 2
        assert replayed.as_dict() == reader.get_snapshot(person_id).as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM digital_life_state WHERE person_id = 'person-a'")
        conn.commit()
    rebuilt = service.rebuild("person-a")
    assert rebuilt.state["mood"] == "old-a"
    assert reader.get_snapshot("person-a").as_dict() == rebuilt.as_dict()

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="LIFE_COMMAND_STATE_EVENT_REQUIRED"):
            conn.execute(
                """
                UPDATE digital_life_state
                SET version = 3,
                    state_json = '{"person_id":"person-a","version":3}'
                WHERE person_id = 'person-a'
                """
            )


def test_sync_projection_heartbeat_prevents_reclaim_during_slow_handler(
    tmp_path: Path,
) -> None:
    _db_path, repository = _repository(tmp_path)
    started_at = time.monotonic()

    def clock() -> int:
        return NOW + int((time.monotonic() - started_at) * 1_000)

    command = _command("projection-heartbeat", deliveries=False)
    command = replace(
        command,
        payload={
            **command.payload,
            "organ_intents": [],
            "projection_intents": [
                {"projection_id": "surface", "payload": {"tone": "gentle"}}
            ],
        },
    )
    LifeCommandService(repository, clock=clock).submit(command)
    entered = threading.Event()
    side_effects: list[str] = []

    def slow_handler(message):  # noqa: ANN001, ANN202
        side_effects.append(message.intent_id)
        entered.set()
        time.sleep(0.8)
        return {"handled": True}

    first = LifeProjectionDispatcher(
        repository,
        slow_handler,
        owner_id="projection-heartbeat-a",
        clock=clock,
        claim_lease_ms=300,
    )
    second = LifeProjectionDispatcher(
        repository,
        lambda message: side_effects.append(message.intent_id),
        owner_id="projection-heartbeat-b",
        clock=clock,
        claim_lease_ms=300,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.dispatch_pending, limit=1, dispatch_at_ms=clock())
        assert entered.wait(timeout=1.0)
        time.sleep(0.5)
        competing = second.dispatch_pending(limit=1, dispatch_at_ms=clock())
        completed = pending.result(timeout=2.0)

    assert competing.as_dict() == {"claimed": 0, "delivered": 0, "failed": 0}
    assert completed.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert len(side_effects) == 1


def test_async_outbox_heartbeat_prevents_reclaim_during_slow_handler(
    tmp_path: Path,
) -> None:
    _db_path, repository = _repository(tmp_path)
    started_at = time.monotonic()

    def clock() -> int:
        return NOW + int((time.monotonic() - started_at) * 1_000)

    LifeCommandService(repository, clock=clock).submit(_command("outbox-heartbeat"))

    async def scenario():  # noqa: ANN202
        entered = asyncio.Event()
        side_effects: list[str] = []

        async def slow_handler(message):  # noqa: ANN001, ANN202
            side_effects.append(message.outbox_id)
            entered.set()
            await asyncio.sleep(0.8)
            return {"handled": True}

        first = LifeOutboxDispatcher(
            repository,
            slow_handler,
            owner_id="outbox-heartbeat-a",
            clock=clock,
            claim_lease_ms=300,
        )
        second = LifeOutboxDispatcher(
            repository,
            lambda message: side_effects.append(message.outbox_id),
            owner_id="outbox-heartbeat-b",
            clock=clock,
            claim_lease_ms=300,
        )
        pending = asyncio.create_task(
            first.dispatch_pending_async(limit=1, dispatch_at_ms=clock())
        )
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        await asyncio.sleep(0.5)
        competing = await second.dispatch_pending_async(
            limit=1,
            dispatch_at_ms=clock(),
        )
        return await pending, competing, side_effects

    completed, competing, side_effects = asyncio.run(scenario())
    assert competing.as_dict() == {"claimed": 0, "delivered": 0, "failed": 0}
    assert completed.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert len(side_effects) == 1
