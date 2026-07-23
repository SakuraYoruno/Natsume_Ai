"""Host-scoped monotonic intent sequence allocation for Tribe admission."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class IntentSequenceAllocator:
    """Allocate session sequence values inside one Host application scope.

    The durable ledger remains the source for the initial session baseline.
    Process-wide mutable sequence state and service-locator discovery are
    deliberately forbidden: composition creates one allocator for the
    Host-lifespan Tribe ports and supplies its ledger provider explicitly.
    """

    def __init__(self, *, ledger_provider: Callable[[], Any]) -> None:
        if not callable(ledger_provider):
            raise TypeError("ledger_provider must be callable")
        self._ledger_provider = ledger_provider
        self._sequence_lock = threading.RLock()
        self._last_sequence_by_session: dict[str, int] = {}

    def allocate(self, session_id: str, *, min_seq: int | None = None) -> int:
        """Return a scope-local monotonic sequence compatible with Governor."""

        normalized_session_id = _normalize_session_id(session_id)
        with self._sequence_lock:
            baseline = self._last_sequence_by_session.get(normalized_session_id)
            if baseline is None:
                baseline = self._read_durable_session_sequence(normalized_session_id)
            if min_seq is not None:
                try:
                    baseline = max(baseline, int(min_seq))
                except (TypeError, ValueError):
                    pass
            next_sequence = max(1, baseline + 1)
            self._last_sequence_by_session[normalized_session_id] = next_sequence
            return next_sequence

    def sync(self, session_id: str, sequence: int) -> None:
        """Advance the scope-local cache after an already committed sequence."""

        try:
            sequence_value = int(sequence)
        except (TypeError, ValueError):
            return
        if sequence_value <= 0:
            return
        normalized_session_id = _normalize_session_id(session_id)
        with self._sequence_lock:
            current = self._last_sequence_by_session.get(normalized_session_id, 0)
            if sequence_value > current:
                self._last_sequence_by_session[normalized_session_id] = sequence_value

    def _read_durable_session_sequence(self, session_id: str) -> int:
        ledger = self._ledger_provider()
        try:
            value = ledger.get_session_seq(session_id=session_id)
        except Exception:
            return 0
        if value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0


def _normalize_session_id(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    return normalized or "__unknown_session__"


__all__ = ["IntentSequenceAllocator"]
