from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from host_brain.app.digital_life_routes import router as digital_life_router
from host_brain.app.memory.state_arbiter import StateArbiter, StateSource
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.runtime import life_continuity as lc
from host_brain.runtime.world_model import (
    SHORT_INFERENCE_TTL_MS,
    WORLD_PROJECTION_DISABLED,
    WORLD_SNAPSHOT_SCHEMA_VERSION,
    WorldProjectionReadModel,
    build_world_snapshot,
)


REQUIRED_FACT_FIELDS = {
    "key",
    "value",
    "source",
    "source_ref",
    "confidence",
    "observed_at_ms",
    "expires_at_ms",
    "ttl_ms",
    "epistemic_type",
    "conflict_state",
}


def _ledger(tmp_path: Path, name: str = "world.sqlite3") -> SQLiteLedger:
    db_path = tmp_path / name
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="world-projection-contract",
        ts_ms=1_900_000_000_000,
    )
    assert migrated.success is True, migrated.as_dict()
    return SQLiteLedger(
        db_path=str(db_path),
        check_same_thread=False,
        initialize_schema=False,
    )


def _close(ledger: SQLiteLedger) -> None:
    ledger._conn.close()


def _table_exists(ledger: SQLiteLedger, table: str) -> bool:
    row = ledger._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _life_state_count(ledger: SQLiteLedger) -> int:
    return int(
        ledger._conn.execute("SELECT COUNT(*) FROM digital_life_state").fetchone()[0]
    )


def test_t_nle_15_world_projection_is_read_only_and_does_not_initialize_life_state(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "readonly.sqlite3")
    try:
        assert _table_exists(ledger, "digital_life_state") is True
        before = _life_state_count(ledger)
        snapshot = asyncio.run(build_world_snapshot(ledger=ledger, person_id="default"))
        after = _life_state_count(ledger)

        assert before == 0
        assert after == 0
        assert snapshot["schema_version"] == WORLD_SNAPSHOT_SCHEMA_VERSION
        assert snapshot["read_only"] is True
        assert snapshot["write_api_available"] is False
        assert snapshot["write_methods"] == []
        assert any(fact["key"] == "life.state_available" and fact["conflict_state"] == "SOURCE_UNAVAILABLE" for fact in snapshot["facts"])
    finally:
        _close(ledger)


def test_t_nle_15_world_projection_facts_include_source_confidence_ttl_and_epistemic_type(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "facts.sqlite3")
    try:
        lc.ensure_life_schema(ledger)
        state = lc.get_life_state(ledger, "default")
        state["affect"]["mood"] = "curious"
        state["needs"]["contact"] = 0.44
        lc._save_state(ledger, state, command_ref="seed-world-projection", source_kind="test_seed")

        snapshot = asyncio.run(build_world_snapshot(ledger=ledger, person_id="default", now_ms=int(time.time() * 1000)))
        facts = snapshot["facts"]
        assert facts
        assert all(REQUIRED_FACT_FIELDS.issubset(fact.keys()) for fact in facts)
        assert all(0.0 <= float(fact["confidence"]) <= 1.0 for fact in facts)
        assert all(int(fact["expires_at_ms"]) > int(fact["observed_at_ms"]) for fact in facts)
        inferred = [fact for fact in facts if fact["epistemic_type"] == "inferred"]
        assert inferred
        assert all(float(fact["confidence"]) < 0.8 for fact in inferred)
        assert all(int(fact["ttl_ms"]) <= SHORT_INFERENCE_TTL_MS for fact in inferred)
    finally:
        _close(ledger)


def test_t_nle_15_expired_work_fact_is_visible_as_expired(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "expired.sqlite3")
    try:
        lc.ensure_life_schema(ledger)
        lc.get_life_state(ledger, "default")
        now = 1_800_000_000_000
        old = now - (25 * 60 * 60 * 1000)
        ledger._conn.execute(
            "INSERT INTO work_environment_event(event_id, person_id, event_type, work_id, summary, urgency, emotional_effect_json, created_at_ms, active) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("work-old", "default", "task_progress", "w1", "old task", "medium", "{}", old, 0),
        )
        ledger._conn.commit()

        snapshot = asyncio.run(build_world_snapshot(ledger=ledger, person_id="default", now_ms=now))
        latest = next(fact for fact in snapshot["facts"] if fact["key"] == "work.latest_event")
        assert latest["conflict_state"] == "EXPIRED"
        assert latest["confidence"] < 0.5
        assert snapshot["expired_count"] >= 1
    finally:
        _close(ledger)


def test_t_nle_15_state_arbiter_conflict_is_visible_without_promotion(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "arbiter.sqlite3")
    arbiter = StateArbiter()
    arbiter.register_source(StateSource.WORLD_CANON, lambda fact_key: "canon-value" if fact_key == "user.preference" else None)
    arbiter.register_source(StateSource.EPISODIC_STORE, lambda fact_key: "episode-value" if fact_key == "user.preference" else None)
    try:
        snapshot = asyncio.run(
            build_world_snapshot(
                ledger=ledger,
                person_id="default",
                state_arbiter=arbiter,
                fact_keys=["user.preference"],
                now_ms=1_800_000_000_000,
            )
        )
        fact = next(item for item in snapshot["facts"] if item["key"] == "state_arbiter.user.preference")
        assert fact["value"] == "canon-value"
        assert fact["conflict_state"] == "CONFLICT"
        assert "STATE_ARBITER_CONFLICT" in fact["reason_codes"]
        assert fact["metadata"]["winner_source"] == StateSource.WORLD_CANON.value
        assert snapshot["conflict_count"] >= 1
    finally:
        _close(ledger)


def test_t_nle_15_world_projection_has_no_write_route_or_write_facade_method() -> None:
    world_routes = [route for route in digital_life_router.routes if "/world/" in getattr(route, "path", "")]
    assert world_routes
    assert all(set(getattr(route, "methods", set())) <= {"GET"} for route in world_routes)
    forbidden = {"write", "update", "apply", "promote", "commit", "save"}
    public_methods = {name for name in dir(WorldProjectionReadModel) if not name.startswith("_")}
    assert forbidden.isdisjoint(public_methods)


def test_t_nle_15_world_projection_disabled_returns_read_only_disabled_snapshot(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "disabled.sqlite3")
    try:
        snapshot = asyncio.run(build_world_snapshot(ledger=ledger, person_id="default", enabled=False))
        assert snapshot["status"] == "DISABLED"
        assert snapshot["reason_code"] == WORLD_PROJECTION_DISABLED
        assert snapshot["read_only"] is True
        assert snapshot["write_api_available"] is False
        assert snapshot["facts"] == []
    finally:
        _close(ledger)
