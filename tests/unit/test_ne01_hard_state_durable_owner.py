from __future__ import annotations

from dataclasses import dataclass

import pytest

from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateEventV1,
    HostHardStateSnapshotV1,
)


def _event(
    *,
    revision: int,
    from_mode: str,
    to_mode: str,
    event_id: str,
    ts_ms: int,
    source: str | None = None,
    reason: str = "test",
) -> HostHardStateEventV1:
    return HostHardStateEventV1(
        runtime_scope_id="scope",
        revision=revision,
        event_id=event_id,
        event_kind="INITIALIZED" if revision == 1 else "MODE_CHANGED",
        from_mode=from_mode,
        to_mode=to_mode,
        source=(source or ("host_bootstrap" if revision == 1 else "manual")),
        reason=reason,
        previous_event_digest="" if revision == 1 else "sha256:" + "a" * 64,
        event_digest="sha256:" + ("a" if revision == 1 else "b") * 64,
        runtime_owner_id="owner",
        runtime_lease_id="lease",
        runtime_fencing_token=1,
        runtime_issued_at_ms=1,
        runtime_expires_at_ms=100_000,
        transition_ts_ms=ts_ms,
        created_at_ms=ts_ms,
    )


def _snapshot(event: HostHardStateEventV1) -> HostHardStateSnapshotV1:
    return HostHardStateSnapshotV1(
        runtime_scope_id=event.runtime_scope_id,
        revision=event.revision,
        sys_mode=event.to_mode,
        event_id=event.event_id,
        event_digest=event.event_digest,
        updated_at_ms=event.transition_ts_ms,
    )


@dataclass
class _Journal:
    current: HostHardStateEventV1
    lose_response: bool = False

    def __post_init__(self) -> None:
        self.events = [self.current]
        self.transition_calls = []

    def load(self):
        return _snapshot(self.current)

    def load_or_initialize(self, **_kwargs):
        return HostHardStateCommitV1(
            snapshot=_snapshot(self.current), event=self.current
        )

    def transition(self, **kwargs):
        self.transition_calls.append(dict(kwargs))
        next_event = _event(
            revision=self.current.revision + 1,
            from_mode=self.current.to_mode,
            to_mode=kwargs["target_mode"],
            event_id=kwargs["event_id"],
            ts_ms=kwargs["transition_ts_ms"],
            source=kwargs["source"],
            reason=kwargs["reason"],
        )
        self.current = next_event
        self.events.append(next_event)
        if self.lose_response:
            raise RuntimeError("response lost after commit")
        return HostHardStateCommitV1(
            snapshot=_snapshot(next_event), event=next_event
        )

    def replay_transition(self, **kwargs):
        existing = next(
            (
                event
                for event in self.events
                if event.event_id == kwargs["event_id"]
            ),
            None,
        )
        if existing is None:
            return None
        if (
            existing.to_mode != kwargs["target_mode"]
            or existing.source != kwargs["source"]
            or existing.reason != kwargs["reason"]
        ):
            raise RuntimeError("HOST_HARD_STATE_REPLAY_DRIFT")
        return HostHardStateCommitV1(
            snapshot=_snapshot(self.current),
            event=existing,
            exact_replay=True,
        )

    def history(self, *, limit=100):
        return tuple(reversed(self.events[-limit:]))


def test_required_owner_rejects_prebind_write_and_recovers_durable_head() -> None:
    clock = lambda: 100
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=clock, require_durable=True
    )
    with pytest.raises(RuntimeError, match="DURABLE_AUTHORITY_NOT_BOUND"):
        owner.set_sys_mode("focus")
    durable = _event(
        revision=1,
        from_mode="gaming",
        to_mode="gaming",
        event_id="init",
        ts_ms=50,
    )
    owner.bind_durable_journal(_Journal(durable))
    state = owner.get_hard_state()
    assert state.sys_mode == "gaming"
    assert state.last_event_id == "init"
    assert state.ts_ms == 50


def test_owner_cas_binds_observed_mode_and_updates_only_after_commit() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 100, require_durable=True
    )
    journal = _Journal(
        _event(
            revision=1,
            from_mode="normal",
            to_mode="normal",
            event_id="init",
            ts_ms=90,
        )
    )
    owner.bind_durable_journal(journal)
    result = owner.set_sys_mode(
        "focus",
        expected_from_mode="normal",
        event_id="focus-1",
        source="manual",
        reason="focus",
        ts_ms=100,
    )
    assert result["revision"] == 2
    assert owner.get_hard_state().sys_mode == "focus"
    with pytest.raises(RuntimeError, match="OBSERVED_MODE_CONFLICT"):
        owner.set_sys_mode(
            "normal",
            expected_from_mode="gaming",
            event_id="stale",
            ts_ms=101,
        )
    assert len(journal.transition_calls) == 1


def test_commit_response_loss_reconciles_exact_durable_event() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 100, require_durable=True
    )
    journal = _Journal(
        _event(
            revision=1,
            from_mode="normal",
            to_mode="normal",
            event_id="init",
            ts_ms=90,
        ),
        lose_response=True,
    )
    owner.bind_durable_journal(journal)
    result = owner.set_sys_mode(
        "focus", event_id="focus-lost", source="manual", ts_ms=100
    )
    assert result["event_id"] == "focus-lost"
    assert result["revision"] == 2
    assert owner.get_hard_state().last_event_id == "focus-lost"


def test_visible_history_excludes_initialization_and_durable_wins() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 100, require_durable=True
    )
    journal = _Journal(
        _event(
            revision=1,
            from_mode="normal",
            to_mode="normal",
            event_id="init",
            ts_ms=90,
        )
    )
    owner.bind_durable_journal(journal)
    owner.set_sys_mode("focus", event_id="focus-1", source="manual", ts_ms=100)
    history = owner.durable_history(limit=20)
    assert [item["event_id"] for item in history] == ["focus-1"]
    assert history[0]["revision"] == 2


def test_unbind_allows_new_lifespan_adapter_to_recover() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 100, require_durable=True
    )
    first = _Journal(
        _event(
            revision=1,
            from_mode="normal",
            to_mode="normal",
            event_id="init",
            ts_ms=90,
        )
    )
    owner.bind_durable_journal(first)
    owner.unbind_durable_journal()
    owner.bind_durable_journal(first)
    assert owner.durability_status()["bound"] is True


def test_restart_same_mode_replays_canonical_receipt_without_new_event() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 200, require_durable=True
    )
    initialized = _event(
        revision=1,
        from_mode="normal",
        to_mode="normal",
        event_id="init",
        ts_ms=90,
    )
    journal = _Journal(initialized)
    owner.bind_durable_journal(journal)
    first = owner.set_sys_mode(
        "focus",
        event_id="focus-retry",
        source="manual",
        reason="same request",
        ts_ms=100,
    )
    replay = owner.set_sys_mode(
        "focus",
        event_id="focus-retry",
        source="manual",
        reason="same request",
        ts_ms=200,
    )
    assert first["exact_replay"] is False
    assert replay["exact_replay"] is True
    assert replay["ts_ms"] == 100
    assert replay["event_digest"] == first["event_digest"]
    assert len(journal.events) == 2


def test_replay_payload_drift_is_never_flipped_to_success() -> None:
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal", clock_ms=lambda: 200, require_durable=True
    )
    journal = _Journal(
        _event(
            revision=1,
            from_mode="normal",
            to_mode="normal",
            event_id="init",
            ts_ms=90,
        )
    )
    owner.bind_durable_journal(journal)
    owner.set_sys_mode(
        "focus",
        event_id="focus-drift",
        source="manual",
        reason="original",
        ts_ms=100,
    )
    with pytest.raises(RuntimeError, match="REPLAY_DRIFT"):
        owner.set_sys_mode(
            "focus",
            event_id="focus-drift",
            source="manual",
            reason="tampered",
            ts_ms=200,
        )
    assert len(journal.events) == 2
