from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.memory.life_memory.contracts import (
    ChatMemoryCandidate,
    MemoryAdmissionClaim,
)
from host_brain.memory.life_memory.projection import (
    LegacyEpisodicProjection,
    LifeMemoryProjectionWorker,
)
from host_brain.memory.life_memory.service import (
    ChatMemoryGuardRejected,
    LifeMemoryAdmissionPolicy,
    LifeMemoryService,
)
from host_brain.persistence.life_memory_repository import (
    LIFE_MEMORY_ADMISSION_CONFLICT,
    LifeMemoryRepository,
    LifeMemoryRepositoryError,
)


NOW = 1_800_000_000_000


class CountingWorth:
    def __init__(self, decision: str = "write") -> None:
        self.decision = decision
        self.calls = 0

    def evaluate(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        self.calls += 1
        score = {"skip": 0.05, "write_light": 0.2, "write": 0.8}[self.decision]
        return SimpleNamespace(
            decision=self.decision,
            worth_score=score,
            tags=["new_info"],
            reason="test",
        )


class BlockingWorth(CountingWorth):
    def __init__(self) -> None:
        super().__init__("write")
        self.entered = threading.Event()
        self.release = threading.Event()

    def evaluate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test policy was not released")
        return super().evaluate(*args, **kwargs)


class CountingGuard:
    def __init__(self, *, reject: bool = False) -> None:
        self.calls = 0
        self.reject = reject

    def validate(self, _candidate, *, tags):  # noqa: ANN001, ANN201
        self.calls += 1
        assert tuple(tags) == ("new_info",)
        if self.reject:
            raise ChatMemoryGuardRejected("TEST_GUARD_REJECTED", "guard rejected")


class ProjectionStore:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail_first = fail_first

    def write_memory(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(dict(kwargs))
        if self.fail_first and len(self.calls) == 1:
            return SimpleNamespace(success=False, reason_code="VECTOR_DOWN", detail="down")
        return SimpleNamespace(success=True, reason_code=None, detail="ok", memory_id="projection-1")


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "life-memory.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-memory-migration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _candidate(*, request_id: str = "req-memory-1", user_message: str = "请记住我喜欢简洁的报告") -> ChatMemoryCandidate:
    return ChatMemoryCandidate(
        request_id=request_id,
        session_id="qq_dm_1",
        person_id="owner",
        intent_id=request_id,
        user_message=user_message,
        assistant_reply="记住了，之后我会先给结论。",
        emotion_tag="NEUTRAL",
        turn_count=3,
        attachments=({"type": "image", "url": "qq://image/1"},),
        multimodal_observations={"primary_modality": "image"},
        attachment_summary="一张报告截图",
        metadata={"reply_channel": "qq"},
        ts_ms=NOW,
    )


def _service(
    db_path: Path,
    *,
    worth: CountingWorth,
    guard: CountingGuard,
    projection_store: ProjectionStore | None = None,
    fault_injector=None,  # noqa: ANN001
) -> tuple[LifeMemoryService, LifeMemoryRepository]:
    repository = LifeMemoryRepository(db_path, clock=lambda: NOW + 100, fault_injector=fault_injector)
    worker = None
    if projection_store is not None:
        worker = LifeMemoryProjectionWorker(
            repository=repository,
            projector=LegacyEpisodicProjection(projection_store),
            owner_id="test-projector",
        )
    service = LifeMemoryService(
        repository=repository,
        policy=LifeMemoryAdmissionPolicy(worth_evaluator=worth, guard=guard),
        projection_dispatcher=worker,
    )
    return service, repository


def _count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def test_chat_admission_is_one_policy_one_uow_and_exact_replay(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    worth = CountingWorth("write")
    guard = CountingGuard()
    projection = ProjectionStore()
    service, _repository = _service(
        db_path,
        worth=worth,
        guard=guard,
        projection_store=projection,
    )

    first = service.admit_chat(_candidate())
    second = service.admit_chat(
        replace(
            _candidate(),
            ts_ms=NOW + 999,
            metadata={
                "reply_channel": "qq",
                "trace_stage_meta": {"total_latency_ms": 999},
            },
        )
    )

    assert first.status == "COMMITTED"
    assert first.projection_status == "PUBLISHED"
    assert second.replayed is True
    assert second.episode_id == first.episode_id
    assert worth.calls == 1
    assert guard.calls == 1
    assert len(projection.calls) == 1
    assert _count(db_path, "life_episode") == 1
    assert _count(db_path, "life_evidence") == 4
    assert _count(db_path, "life_memory_admission_receipt") == 1
    assert _count(db_path, "life_memory_projection_outbox") == 1
    with sqlite3.connect(db_path) as conn:
        payload = str(
            conn.execute(
                "SELECT payload_json FROM life_memory_projection_outbox"
            ).fetchone()[0]
        )
    assert "请记住" not in payload
    assert "记住了" not in payload


@pytest.mark.parametrize(
    ("decision", "episodes", "outboxes", "status"),
    [
        ("skip", 0, 0, "SKIPPED"),
        ("write_light", 1, 0, "COMMITTED"),
    ],
)
def test_skip_and_light_decisions_never_emit_projection(
    tmp_path: Path,
    decision: str,
    episodes: int,
    outboxes: int,
    status: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    worth = CountingWorth(decision)
    guard = CountingGuard()
    service, _repository = _service(db_path, worth=worth, guard=guard)

    receipt = service.admit_chat(_candidate())

    assert receipt.status == status
    assert receipt.outbox_id is None
    assert guard.calls == 0
    assert _count(db_path, "life_episode") == episodes
    assert _count(db_path, "life_memory_projection_outbox") == outboxes


def test_guard_rejection_degrades_to_light_ssot_without_projection(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    service, _repository = _service(
        db_path,
        worth=CountingWorth("write"),
        guard=CountingGuard(reject=True),
    )

    receipt = service.admit_chat(_candidate())

    assert receipt.status == "COMMITTED"
    assert receipt.decision.value == "WRITE_LIGHT"
    assert receipt.reason_code == "MEMORY_GUARD_LIGHT_ONLY:TEST_GUARD_REJECTED"
    assert receipt.outbox_id is None
    assert _count(db_path, "life_episode") == 1


def test_fault_after_evidence_rolls_back_episode_evidence_receipt_and_outbox(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)

    def fault(stage: str) -> None:
        if stage == "after_evidence":
            raise RuntimeError("crash")

    service, _repository = _service(
        db_path,
        worth=CountingWorth("write"),
        guard=CountingGuard(),
        fault_injector=fault,
    )
    with pytest.raises(RuntimeError, match="crash"):
        service.admit_chat(_candidate())

    for table in (
        "life_memory_admission_claim",
        "life_episode",
        "life_evidence",
        "life_memory_admission_receipt",
        "life_memory_projection_outbox",
    ):
        assert _count(db_path, table) == 0


def test_same_request_with_changed_candidate_fails_closed_before_policy(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    worth = CountingWorth("write_light")
    service, _repository = _service(
        db_path,
        worth=worth,
        guard=CountingGuard(),
    )
    service.admit_chat(_candidate())

    with pytest.raises(LifeMemoryRepositoryError) as exc_info:
        service.admit_chat(_candidate(user_message="changed"))

    assert exc_info.value.reason_code == LIFE_MEMORY_ADMISSION_CONFLICT
    assert worth.calls == 1


def test_failed_projection_replays_from_outbox_without_duplicate_ssot_or_policy(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    worth = CountingWorth("write")
    projection = ProjectionStore(fail_first=True)
    service, _repository = _service(
        db_path,
        worth=worth,
        guard=CountingGuard(),
        projection_store=projection,
    )

    first = service.admit_chat(_candidate())
    replay = service.admit_chat(_candidate())

    assert first.status == "COMMITTED"
    assert first.projection_status == "FAILED"
    assert replay.replayed is True
    assert replay.projection_status == "PUBLISHED"
    assert worth.calls == 1
    assert len(projection.calls) == 2
    assert _count(db_path, "life_episode") == 1
    assert _count(db_path, "life_memory_admission_receipt") == 1


def test_concurrent_exact_replay_has_one_durable_policy_owner(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    owner_worth = BlockingWorth()
    waiter_worth = CountingWorth("write")
    owner_service, _owner_repository = _service(
        db_path,
        worth=owner_worth,
        guard=CountingGuard(),
    )
    waiter_service, _waiter_repository = _service(
        db_path,
        worth=waiter_worth,
        guard=CountingGuard(),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(owner_service.admit_chat, _candidate())
        assert owner_worth.entered.wait(timeout=2)
        waiter_future = pool.submit(waiter_service.admit_chat, _candidate())
        time.sleep(0.05)
        assert waiter_future.done() is False
        assert waiter_worth.calls == 0
        owner_worth.release.set()
        owner_receipt = owner_future.result(timeout=3)
        waiter_receipt = waiter_future.result(timeout=3)

    assert owner_worth.calls == 1
    assert waiter_worth.calls == 0
    assert owner_receipt.episode_id == waiter_receipt.episode_id
    assert owner_receipt.replayed is False
    assert waiter_receipt.replayed is True
    assert _count(db_path, "life_memory_admission_claim") == 0
    assert _count(db_path, "life_memory_admission_receipt") == 1


def test_active_claim_rejects_payload_drift_before_second_policy(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    owner_worth = BlockingWorth()
    drift_worth = CountingWorth("write")
    owner_service, _owner_repository = _service(
        db_path,
        worth=owner_worth,
        guard=CountingGuard(),
    )
    drift_service, _drift_repository = _service(
        db_path,
        worth=drift_worth,
        guard=CountingGuard(),
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner_future = pool.submit(owner_service.admit_chat, _candidate())
        assert owner_worth.entered.wait(timeout=2)
        try:
            with pytest.raises(LifeMemoryRepositoryError) as exc_info:
                drift_service.admit_chat(_candidate(user_message="changed while active"))
            assert exc_info.value.reason_code == LIFE_MEMORY_ADMISSION_CONFLICT
            assert drift_worth.calls == 0
        finally:
            owner_worth.release.set()
        owner_future.result(timeout=3)


def test_expired_claim_is_recovered_after_owner_crash(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    wall_clock = lambda: int(time.time() * 1000)
    dead_repository = LifeMemoryRepository(db_path, clock=wall_clock)
    dead_claim = dead_repository.try_claim_chat_admission(
        _candidate(),
        owner_token="crashed-owner",
        lease_ms=10,
    )
    assert isinstance(dead_claim, MemoryAdmissionClaim)
    time.sleep(0.03)

    recovery_worth = CountingWorth("write_light")
    recovery_repository = LifeMemoryRepository(db_path, clock=wall_clock)
    recovery_service = LifeMemoryService(
        repository=recovery_repository,
        policy=LifeMemoryAdmissionPolicy(
            worth_evaluator=recovery_worth,
            guard=CountingGuard(),
        ),
        claim_lease_ms=1_000,
        claim_wait_timeout_ms=2_000,
        claim_poll_interval_ms=1,
    )

    receipt = recovery_service.admit_chat(_candidate())

    assert receipt.status == "COMMITTED"
    assert recovery_worth.calls == 1
    assert _count(db_path, "life_memory_admission_claim") == 0
    assert _count(db_path, "life_memory_admission_receipt") == 1
