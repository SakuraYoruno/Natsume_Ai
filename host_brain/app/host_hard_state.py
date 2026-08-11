"""Application-local ownership for the Host's immutable hard state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any

from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateJournalPort,
    HostHardStateSnapshotV1,
)
from host_brain.core.ids import new_event_id
from host_brain.core.metabolism import (
    ALLOWED_SYS_MODES,
    HardState,
    evolve_hard_state,
    normalize_hard_state,
)


EndocrineResolver = Callable[..., Mapping[str, Any]]
ClockMs = Callable[[], int]

_logger = logging.getLogger("Natsume.host_hard_state")
_ENDOCRINE_FALLBACK = {
    "dopamine_level": 0.5,
    "serotonin_level": 0.5,
    "cortisol_level": 0.25,
    "energy_reserve": 0.6,
    "gate_state": "open",
    "source": "endocrine_fallback",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_endocrine_state(**kwargs: Any) -> Mapping[str, Any]:
    """Resolve through the existing Tribe adapter without owning its lifecycle."""

    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

    return get_endocrine_agent().resolve_endocrine_state(**kwargs)


@dataclass(slots=True)
class HostHardStateOwner:
    """Linearizable, application-local owner of the immutable ``HardState``.

    The owner never reads or writes process environment state.  A composition
    root must supply the bootstrap mode explicitly.  External endocrine work is
    deliberately invoked after taking a frozen state snapshot and releasing
    the owner lock.
    """

    _state: HardState
    _endocrine_resolver: EndocrineResolver = field(repr=False)
    _clock_ms: ClockMs = field(repr=False)
    _require_durable: bool = field(default=False, repr=False)
    _journal: HostHardStateJournalPort | None = field(default=None, init=False, repr=False)
    _journal_snapshot: HostHardStateSnapshotV1 | None = field(
        default=None, init=False, repr=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def bootstrap(
        cls,
        *,
        initial_sys_mode: str,
        endocrine_resolver: EndocrineResolver = _resolve_endocrine_state,
        clock_ms: ClockMs = _now_ms,
        require_durable: bool = False,
    ) -> "HostHardStateOwner":
        """Create one owner from an explicit, bootstrap-only system mode."""

        normalized_mode = str(initial_sys_mode or "").strip().lower()
        if normalized_mode not in ALLOWED_SYS_MODES:
            _logger.error(
                "非法 bootstrap sys_mode=%r，Host 以 maintenance 失败关闭",
                initial_sys_mode,
            )
            normalized_mode = "maintenance"
        state = normalize_hard_state(
            {
                "energy_level": 60,
                "mood_state": "steady",
                "sys_mode": normalized_mode,
                "safety_flag": False,
                "ts_ms": int(clock_ms()),
            }
        )
        return cls(
            _state=state,
            _endocrine_resolver=endocrine_resolver,
            _clock_ms=clock_ms,
            _require_durable=bool(require_durable),
        )

    def bind_durable_journal(
        self,
        journal: HostHardStateJournalPort,
        *,
        initialization_event_id: str = "host-hard-state-initialized",
    ) -> HostHardStateSnapshotV1:
        """Recover or initialize the canonical mode before publishing routes."""

        if journal is None:
            raise RuntimeError("HOST_HARD_STATE_JOURNAL_REQUIRED")
        with self._lock:
            if self._journal is not None:
                raise RuntimeError("HOST_HARD_STATE_JOURNAL_ALREADY_BOUND")
            current = self._state
            commit = journal.load_or_initialize(
                initial_mode=current.sys_mode,
                event_id=str(initialization_event_id),
                transition_ts_ms=int(self._clock_ms()),
            )
            self._state = self._state_from_snapshot(current, commit.snapshot)
            self._journal_snapshot = commit.snapshot
            self._journal = journal
            return commit.snapshot

    def get_hard_state(self) -> HardState:
        """Return the current immutable snapshot."""

        with self._lock:
            return self._state

    def unbind_durable_journal(self) -> None:
        """Drop the lifespan-owned adapter while retaining its verified head."""

        with self._lock:
            self._journal = None

    def set_sys_mode(
        self,
        target_mode: str,
        *,
        expected_from_mode: str | None = None,
        event_id: str | None = None,
        source: str = "direct",
        reason: str = "",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """Atomically replace the state without recursive lock acquisition."""

        normalized_target = str(target_mode or "").strip().lower()
        if normalized_target not in ALLOWED_SYS_MODES:
            raise ValueError(
                "invalid sys_mode: "
                f"{target_mode!r}; expected one of "
                f"{', '.join(sorted(ALLOWED_SYS_MODES))}"
            )

        with self._lock:
            current = self._state
            transition_ts_ms = int(self._clock_ms() if ts_ms is None else ts_ms)
            explicit_event_id = str(event_id or "").strip()
            resolved_event_id = explicit_event_id or new_event_id()
            if not resolved_event_id:
                raise ValueError("event_id must not be empty")
            requested_from = (
                str(expected_from_mode).strip().lower()
                if expected_from_mode is not None
                else current.sys_mode
            )
            if requested_from not in ALLOWED_SYS_MODES:
                raise ValueError("expected_from_mode is invalid")
            resolved_source = str(source or "direct")
            resolved_reason = str(reason or "")
            if self._journal is None and self._require_durable:
                raise RuntimeError("HOST_HARD_STATE_DURABLE_AUTHORITY_NOT_BOUND")
            commit = None
            if self._journal is not None:
                snapshot = self._journal_snapshot
                if snapshot is None:
                    raise RuntimeError("HOST_HARD_STATE_DURABLE_HEAD_UNAVAILABLE")
                replay_only = (
                    requested_from != current.sys_mode
                    or current.sys_mode == normalized_target
                )
                if replay_only:
                    if not explicit_event_id:
                        reason_code = (
                            "HOST_HARD_STATE_OBSERVED_MODE_CONFLICT"
                            if requested_from != current.sys_mode
                            else "HOST_HARD_STATE_NOOP_TRANSITION_REJECTED"
                        )
                        raise RuntimeError(reason_code)
                    commit = self._journal.replay_transition(
                        target_mode=normalized_target,
                        event_id=resolved_event_id,
                        source=resolved_source,
                        reason=resolved_reason,
                    )
                    if commit is None:
                        reason_code = (
                            "HOST_HARD_STATE_OBSERVED_MODE_CONFLICT"
                            if requested_from != current.sys_mode
                            else "HOST_HARD_STATE_NOOP_TRANSITION_REJECTED"
                        )
                        raise RuntimeError(reason_code)
                else:
                    try:
                        commit = self._journal.transition(
                            expected_revision=snapshot.revision,
                            expected_mode=requested_from,
                            target_mode=normalized_target,
                            event_id=resolved_event_id,
                            source=resolved_source,
                            reason=resolved_reason,
                            transition_ts_ms=transition_ts_ms,
                        )
                    except Exception as transition_error:
                        try:
                            commit = self._journal.replay_transition(
                                target_mode=normalized_target,
                                event_id=resolved_event_id,
                                source=resolved_source,
                                reason=resolved_reason,
                            )
                        except Exception:
                            commit = None
                        if commit is None:
                            recovered = self._journal.load()
                            if recovered is not None:
                                self._state = self._state_from_snapshot(
                                    current, recovered
                                )
                                self._journal_snapshot = recovered
                            raise transition_error
                next_state = self._state_from_snapshot(current, commit.snapshot)
                self._journal_snapshot = commit.snapshot
                receipt_from_mode = commit.event.from_mode
                receipt_to_mode = commit.event.to_mode
                receipt_ts_ms = commit.event.transition_ts_ms
                receipt_revision = commit.event.revision
                receipt_event_digest = commit.event.event_digest
                exact_replay = commit.exact_replay
            else:
                if requested_from != current.sys_mode:
                    raise RuntimeError("HOST_HARD_STATE_OBSERVED_MODE_CONFLICT")
                if current.sys_mode == normalized_target:
                    raise RuntimeError("HOST_HARD_STATE_NOOP_TRANSITION_REJECTED")
                next_state = evolve_hard_state(
                    current,
                    sys_mode=normalized_target,
                    event_kind="mode_switch",
                    event_id=resolved_event_id,
                    ts_ms=transition_ts_ms,
                )
                receipt_from_mode = current.sys_mode
                receipt_to_mode = next_state.sys_mode
                receipt_ts_ms = transition_ts_ms
                receipt_revision = None
                receipt_event_digest = None
                exact_replay = False
            self._state = next_state

        if exact_replay:
            _logger.info(
                "sys_mode transition exact replay: %s → %s (%s)",
                receipt_from_mode,
                receipt_to_mode,
                resolved_event_id,
            )
        else:
            _logger.info(
                "sys_mode 已切换: %s → %s", receipt_from_mode, receipt_to_mode
            )
        return {
            "from_mode": receipt_from_mode,
            "to_mode": receipt_to_mode,
            "ts_ms": receipt_ts_ms,
            "event_id": resolved_event_id,
            "revision": receipt_revision,
            "event_digest": receipt_event_digest,
            "exact_replay": exact_replay,
            "current_mode": next_state.sys_mode,
            "current_revision": (
                self._journal_snapshot.revision
                if self._journal is not None and self._journal_snapshot is not None
                else None
            ),
            "current_event_digest": (
                self._journal_snapshot.event_digest
                if self._journal is not None and self._journal_snapshot is not None
                else None
            ),
        }

    def durable_history(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return verified successful transitions from the durable journal."""

        with self._lock:
            journal = self._journal
            required = self._require_durable
        if journal is None:
            if required:
                raise RuntimeError("HOST_HARD_STATE_DURABLE_AUTHORITY_NOT_BOUND")
            return []
        return [
            event.as_mode_history()
            for event in journal.history(limit=limit + 1)
            if event.event_kind == "MODE_CHANGED"
        ][:limit]

    def reopen_sys_mode_transition(
        self,
        *,
        event_id: str,
        target_mode: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None:
        """Reopen one exact canonical commit without mutating the live head."""

        with self._lock:
            journal = self._journal
            required = self._require_durable
        if journal is None:
            if required:
                raise RuntimeError("HOST_HARD_STATE_DURABLE_AUTHORITY_NOT_BOUND")
            return None
        return journal.replay_transition(
            event_id=event_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
        )

    def durability_status(self) -> dict[str, object]:
        with self._lock:
            snapshot = self._journal_snapshot
            return {
                "required": self._require_durable,
                "bound": self._journal is not None,
                "revision": snapshot.revision if snapshot is not None else None,
                "event_digest": (
                    snapshot.event_digest if snapshot is not None else None
                ),
            }

    @staticmethod
    def _state_from_snapshot(
        current: HardState,
        snapshot: HostHardStateSnapshotV1,
    ) -> HardState:
        return evolve_hard_state(
            current,
            sys_mode=snapshot.sys_mode,
            event_kind="mode_recovered",
            event_id=snapshot.event_id,
            ts_ms=snapshot.updated_at_ms,
        )

    def get_endocrine_state(
        self,
        *,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        """Resolve endocrine state from a lock-free immutable snapshot."""

        hard_state = self.get_hard_state()
        try:
            resolved = self._endocrine_resolver(
                hard_state=hard_state,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
            )
            return dict(resolved)
        except Exception as exc:
            _logger.warning("EndocrineState 计算失败（降级）: %s", exc)
            return dict(_ENDOCRINE_FALLBACK)


__all__ = ["HostHardStateOwner"]
