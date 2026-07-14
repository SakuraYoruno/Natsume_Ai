"""Session-scoped monotonic intent sequence allocator."""

from __future__ import annotations

import threading

_seq_lock = threading.RLock()
_last_seq_by_session: dict[str, int] = {}


def _normalize_session_id(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if normalized:
        return normalized
    return "__unknown_session__"


def _read_session_seq_from_ledger(session_id: str) -> int:
    try:
        from host_brain.composition.process_services import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
        value = ledger.get_session_seq(session_id=session_id)
        if value is None:
            return 0
        return max(0, int(value))
    except Exception:
        return 0


def allocate_intent_seq(session_id: str, *, min_seq: int | None = None) -> int:
    """Return a process-local monotonic seq compatible with Governor session guard."""
    sid = _normalize_session_id(session_id)
    with _seq_lock:
        base = _last_seq_by_session.get(sid)
        if base is None:
            base = _read_session_seq_from_ledger(sid)
        if min_seq is not None:
            try:
                base = max(base, int(min_seq))
            except (TypeError, ValueError):
                pass
        next_seq = max(1, base + 1)
        _last_seq_by_session[sid] = next_seq
        return next_seq


def sync_intent_seq(session_id: str, seq: int) -> None:
    """Sync local allocator state with an already committed seq."""
    sid = _normalize_session_id(session_id)
    try:
        seq_value = int(seq)
    except (TypeError, ValueError):
        return
    if seq_value <= 0:
        return
    with _seq_lock:
        current = _last_seq_by_session.get(sid, 0)
        if seq_value > current:
            _last_seq_by_session[sid] = seq_value
