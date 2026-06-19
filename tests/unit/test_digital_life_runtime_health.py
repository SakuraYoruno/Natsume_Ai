from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.runtime.life_continuity import get_life_runtime_health


def test_life_runtime_health_returns_default_snapshot() -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        payload = get_life_runtime_health(ledger=ledger, person_id="default")
    finally:
        ledger.close()

    assert payload["person_id"] == "default"
    assert payload["alive_status"] in {"alive", "degraded_alive", "stale"}
    assert "autonomy" in payload
    assert "self_growth" in payload
    assert "autonomous_cycle_stalled_minutes" in payload
