"""Application service for deterministic Digital Life commands."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable
import uuid

from host_brain.core.ids import now_ms
from natsume_contracts.life import (
    LifeAggregateClaim,
    LifeCommand,
    LifeCommandClaim,
    LifeCommandPreview,
    LifeCommandReceipt,
    LifeCommandStatus,
    LifeEvent,
    LifeSnapshot,
    digest_json,
)
from host_brain.digital_life.reducer import DeterministicLifeReducer
from natsume_contracts.life import (
    LIFE_AGGREGATE_CLAIM_BUSY,
    LIFE_COMMAND_BASELINE_REQUIRED,
    LIFE_COMMAND_CLAIM_BUSY,
    LIFE_COMMAND_DURABLE_DATA_INVALID,
    LifeCommandRepositoryError,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


class LifeCommandService:
    def __init__(
        self,
        repository: LifeCommandRepositoryPort,
        reducer: DeterministicLifeReducer | None = None,
        *,
        clock: Callable[[], int] = now_ms,
        owner_id: str = "life-command-service",
        claim_lease_ms: int = 60_000,
        claim_wait_timeout_ms: int = 65_000,
        claim_poll_interval_ms: int = 10,
        aggregate_lease_ms: int | None = None,
        person_lock_stripes: int = 64,
    ) -> None:
        self.repository = repository
        self.reducer = reducer or DeterministicLifeReducer()
        self._clock = clock
        self.owner_id = str(owner_id or "life-command-service")
        self.claim_lease_ms = max(1, int(claim_lease_ms))
        self.claim_wait_timeout_ms = max(1, int(claim_wait_timeout_ms))
        self.claim_poll_interval_ms = max(1, int(claim_poll_interval_ms))
        self.aggregate_lease_ms = max(
            1,
            int(
                aggregate_lease_ms
                if aggregate_lease_ms is not None
                else self.claim_lease_ms + self.claim_wait_timeout_ms
            ),
        )
        self._person_locks = tuple(
            threading.RLock() for _ in range(max(1, min(int(person_lock_stripes), 1_024)))
        )

    def submit(self, command: LifeCommand) -> LifeCommandReceipt:
        if not isinstance(command, LifeCommand):
            raise TypeError("command must be a LifeCommand")
        # This bounded lock serializes one process' aggregate UoW. Cross-process
        # contenders remain fail-closed through expected-version CAS/fencing.
        with self._person_lock(command.person_id):
            return self._submit_locked(command)

    def submit_next(
        self,
        person_id: str,
        command_factory: Callable[[LifeSnapshot], LifeCommand],
    ) -> LifeCommandReceipt:
        """Build and submit against the next serialized aggregate version."""

        with self._person_lock(person_id):
            aggregate_owner = f"{self.owner_id}:aggregate:{uuid.uuid4().hex}"
            deadline = time.monotonic() + self.claim_wait_timeout_ms / 1000.0
            aggregate_claim: LifeAggregateClaim | None = None
            while aggregate_claim is None:
                aggregate_claim = self.repository.acquire_aggregate_claim(
                    person_id,
                    owner_token=aggregate_owner,
                    lease_ms=self.aggregate_lease_ms,
                    now_ms=int(self._clock()),
                )
                if aggregate_claim is not None:
                    break
                if time.monotonic() >= deadline:
                    raise LifeCommandRepositoryError(
                        LIFE_AGGREGATE_CLAIM_BUSY,
                        "timed out waiting for aggregate admission claim",
                    )
                time.sleep(self.claim_poll_interval_ms / 1000.0)
            try:
                snapshot = self.repository.get_snapshot(person_id)
                command = command_factory(snapshot)
                if not isinstance(command, LifeCommand):
                    raise TypeError("command_factory must return LifeCommand")
                if command.person_id != snapshot.person_id:
                    raise ValueError("command_factory returned a different person_id")
                if command.expected_version != snapshot.version:
                    raise ValueError("command_factory returned a stale expected_version")
                return self._submit_locked(
                    command,
                    aggregate_claim=aggregate_claim,
                )
            finally:
                self.repository.release_aggregate_claim(
                    aggregate_claim,
                    now_ms=int(self._clock()),
                )

    def _submit_locked(
        self,
        command: LifeCommand,
        *,
        aggregate_claim: LifeAggregateClaim | None = None,
    ) -> LifeCommandReceipt:
        owner_token = f"{self.owner_id}:{uuid.uuid4().hex}"
        deadline = time.monotonic() + self.claim_wait_timeout_ms / 1000.0
        while True:
            current_ms = int(self._clock())
            outcome = self.repository.try_claim_command(
                command,
                owner_token=owner_token,
                lease_ms=self.claim_lease_ms,
                now_ms=current_ms,
            )
            if isinstance(outcome, LifeCommandReceipt):
                return outcome
            if isinstance(outcome, LifeCommandClaim):
                try:
                    before = self.repository.get_snapshot(command.person_id)
                    preview = self.reducer.reduce(before, command)
                    return self.repository.commit_command(
                        preview,
                        claim=outcome,
                        now_ms=int(self._clock()),
                        aggregate_claim=aggregate_claim,
                    )
                except Exception:
                    try:
                        self.repository.release_command_claim(
                            outcome,
                            now_ms=int(self._clock()),
                        )
                    except LifeCommandRepositoryError:
                        pass
                    raise
            if time.monotonic() >= deadline:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_CLAIM_BUSY,
                    "timed out waiting for command admission claim",
                )
            time.sleep(self.claim_poll_interval_ms / 1000.0)

    def _person_lock(self, person_id: str) -> threading.RLock:
        digest = hashlib.sha256(str(person_id).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(self._person_locks)
        return self._person_locks[index]

    def preview(self, command: LifeCommand) -> LifeCommandPreview:
        before = self.repository.get_snapshot(command.person_id)
        if before.version > 0 and before.last_event_id is None:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_BASELINE_REQUIRED,
                "legacy state projection requires explicit baseline adoption",
            )
        if before.version != command.expected_version:
            raise LifeCommandRepositoryError(
                "LIFE_COMMAND_VERSION_CONFLICT",
                "preview expected_version does not match the current snapshot",
            )
        return self.reducer.reduce(before, command)

    def replay(self, person_id: str) -> LifeSnapshot:
        rows = self.repository.list_replay_rows(person_id)
        if not rows:
            durable = self.repository.get_snapshot(person_id)
            if durable.version > 0:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_BASELINE_REQUIRED,
                    "legacy state projection requires explicit baseline adoption",
                )
            return durable
        current: LifeSnapshot | None = None
        for index, row in enumerate(rows):
            command = LifeCommand(
                command_id=str(row["command_id"]),
                person_id=str(row["person_id"]),
                command_type=str(row["command_type"]),
                payload=_json_object(row["payload_json"]),
                expected_version=int(row["expected_version"]),
                created_at_ms=int(row["created_at_ms"]),
                correlation_id=(
                    str(row["correlation_id"])
                    if row["correlation_id"] is not None
                    else None
                ),
                causation_id=(
                    str(row["causation_id"])
                    if row["causation_id"] is not None
                    else None
                ),
                metadata=_json_object(row["metadata_json"]),
            )
            if command.command_hash != str(row["command_hash"]):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "command journal hash drift",
                )
            if command.command_type == "baseline_adopted":
                if index != 0:
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_DURABLE_DATA_INVALID,
                        "baseline adoption must be the first journal event",
                    )
                event_payload = _json_object(row["event_payload_json"])
                baseline_state = event_payload.get("state_after")
                if not isinstance(baseline_state, dict):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_DURABLE_DATA_INVALID,
                        "baseline event does not contain state_after",
                    )
                state_digest = digest_json(baseline_state)
                event_digest = _event_digest(
                    event_id=str(row["event_id"]),
                    command=command,
                    sequence=int(row["sequence"]),
                    event_type=str(row["event_type"]),
                    payload=event_payload,
                    state_before_digest=str(row["state_before_digest"]),
                    state_after_digest=str(row["state_after_digest"]),
                    previous_event_digest=None,
                )
                if (
                    command.expected_version != int(row["sequence"])
                    or int(baseline_state.get("version") or -1) != int(row["sequence"])
                    or state_digest != str(row["state_before_digest"])
                    or state_digest != str(row["state_after_digest"])
                    or row["previous_event_digest"] is not None
                    or event_digest != str(row["event_digest"])
                ):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_DURABLE_DATA_INVALID,
                        "baseline event is not deterministically replayable",
                    )
                current = LifeSnapshot(
                    person_id=command.person_id,
                    version=int(row["sequence"]),
                    state=baseline_state,
                    state_digest=state_digest,
                    last_event_id=str(row["event_id"]),
                    last_event_digest=str(row["event_digest"]),
                    updated_at_ms=int(
                        command.payload.get("baseline_updated_at_ms")
                        or command.created_at_ms
                    ),
                )
                continue
            if current is None:
                event_payload = _json_object(row["event_payload_json"])
                state_before = event_payload.get("state_before")
                if not isinstance(state_before, dict):
                    raise LifeCommandRepositoryError(
                        LIFE_COMMAND_DURABLE_DATA_INVALID,
                        "first event does not carry its deterministic replay base",
                    )
                current = LifeSnapshot(
                    person_id=command.person_id,
                    version=command.expected_version,
                    state=state_before,
                    state_digest=digest_json(state_before),
                    last_event_digest=(
                        str(row["previous_event_digest"])
                        if row["previous_event_digest"] is not None
                        else None
                    ),
                )
            if current.version != command.expected_version:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    f"event sequence {index + 1} has an expected-version gap",
                )
            preview = self.reducer.reduce(current, command)
            event = preview.event
            if (
                event.event_id != str(row["event_id"])
                or event.sequence != int(row["sequence"])
                or event.event_type != str(row["event_type"])
                or event.state_before_digest != str(row["state_before_digest"])
                or event.state_after_digest != str(row["state_after_digest"])
                or event.previous_event_digest
                != (
                    str(row["previous_event_digest"])
                    if row["previous_event_digest"] is not None
                    else None
                )
                or dict(event.payload) != _json_object(row["event_payload_json"])
                or event.event_digest != str(row["event_digest"])
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    f"event sequence {event.sequence} is not deterministically replayable",
                )
            current = preview.after
        assert current is not None
        return current

    def rebuild(self, person_id: str) -> LifeSnapshot:
        snapshot = self.replay(person_id)
        self.repository.rebuild_projection(snapshot)
        return snapshot

    def adopt_legacy_projection(
        self,
        person_id: str,
        *,
        adopted_at_ms: int | None = None,
    ) -> LifeCommandReceipt:
        snapshot = self.repository.get_snapshot(person_id)
        if snapshot.version <= 0:
            raise LifeCommandRepositoryError(
                "LIFE_COMMAND_NO_LEGACY_PROJECTION",
                "person has no non-empty legacy projection to adopt",
            )
        adopted_at = int(
            adopted_at_ms if adopted_at_ms is not None else self._clock()
        )
        baseline_identity = {
            "person_id": snapshot.person_id,
            "state_digest": snapshot.state_digest,
        }
        command_id = f"life_baseline_{digest_json(baseline_identity)[:32]}"
        command = LifeCommand(
            command_id=command_id,
            person_id=snapshot.person_id,
            command_type="baseline_adopted",
            payload={
                "baseline_state": dict(snapshot.state),
                "baseline_updated_at_ms": snapshot.updated_at_ms,
            },
            expected_version=snapshot.version,
            created_at_ms=adopted_at,
            metadata={"source": "legacy_projection_cutover"},
        )
        event_id = f"life_evt_{digest_json({'command_id': command_id})[:32]}"
        event_payload = {
            "command_hash": command.command_hash,
            "command_type": command.command_type,
            "command_payload": dict(command.payload),
            "state_before": dict(snapshot.state),
            "state_after": dict(snapshot.state),
            "correlation_id": None,
            "causation_id": None,
        }
        event_digest = _event_digest(
            event_id=event_id,
            command=command,
            sequence=snapshot.version,
            event_type="baseline_adopted",
            payload=event_payload,
            state_before_digest=snapshot.state_digest,
            state_after_digest=snapshot.state_digest,
            previous_event_digest=None,
        )
        event = LifeEvent(
            event_id=event_id,
            command_id=command_id,
            person_id=snapshot.person_id,
            sequence=snapshot.version,
            event_type="baseline_adopted",
            payload=event_payload,
            state_before_digest=snapshot.state_digest,
            state_after_digest=snapshot.state_digest,
            previous_event_digest=None,
            event_digest=event_digest,
            created_at_ms=adopted_at,
        )
        receipt = LifeCommandReceipt(
            command_id=command_id,
            command_hash=command.command_hash,
            person_id=snapshot.person_id,
            status=LifeCommandStatus.APPLIED,
            reason_code=None,
            event_id=event_id,
            state_version=snapshot.version,
            state_digest=snapshot.state_digest,
            outbox_count=0,
            created_at_ms=adopted_at,
        )
        return self.repository.adopt_legacy_projection(
            command=command,
            event=event,
            receipt=receipt,
        )

    def adopt_all_legacy_projections(
        self,
        *,
        adopted_at_ms: int | None = None,
        limit: int = 1_000,
    ) -> tuple[LifeCommandReceipt, ...]:
        adopted_at = int(
            adopted_at_ms if adopted_at_ms is not None else self._clock()
        )
        bounded_limit = max(1, min(int(limit), 9_999))
        person_ids = self.repository.list_unadopted_projection_person_ids(
            limit=bounded_limit + 1
        )
        if len(person_ids) > bounded_limit:
            raise LifeCommandRepositoryError(
                "LIFE_COMMAND_ADOPTION_LIMIT_EXCEEDED",
                "legacy projection count exceeds the bounded startup adoption limit",
            )
        return tuple(
            self.adopt_legacy_projection(
                person_id,
                adopted_at_ms=adopted_at,
            )
            for person_id in person_ids
        )


def _json_object(value: object) -> dict:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "journal contains invalid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "journal payload must be a JSON object",
        )
    return decoded


def _event_digest(
    *,
    event_id: str,
    command: LifeCommand,
    sequence: int,
    event_type: str,
    payload: dict,
    state_before_digest: str,
    state_after_digest: str,
    previous_event_digest: str | None,
) -> str:
    return digest_json(
        {
            "event_id": event_id,
            "command_id": command.command_id,
            "person_id": command.person_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": payload,
            "state_before_digest": state_before_digest,
            "state_after_digest": state_after_digest,
            "previous_event_digest": previous_event_digest,
            "created_at_ms": command.created_at_ms,
        }
    )


__all__ = ["LifeCommandService"]
