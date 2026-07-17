from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.model_attempt_projection import (
    latest_terminal_model_attempts,
    model_attempt_node_id,
    model_attempt_token_counts,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _apply_through_0102(db_path: Path, migrations_dir: Path) -> None:
    migrations_dir.mkdir(parents=True)
    for asset in MIGRATIONS.glob("*.sql"):
        try:
            version = int(asset.name.split("_", 1)[0])
        except ValueError:
            continue
        if version <= 102:
            shutil.copy2(asset, migrations_dir / asset.name)
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=migrations_dir,
    ).apply_migrations(request_id="llm-cold-migration-fixture", ts_ms=1_000)
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 102


def test_0103_fresh_database_never_exposes_legacy_llm_node_table(tmp_path) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="llm-cold-migration-fresh",
        ts_ms=2_000,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 103
    assert "0103_retire_llm_node_call_log" in {
        record.migration_id for record in result.records
    }

    with sqlite3.connect(db_path) as conn:
        assert _table_exists(conn, "model_attempt_log") is True
        assert _table_exists(conn, "llm_node_call_log") is False


def test_0103_backfills_legacy_rows_without_copying_raw_pseudo_digests_and_replays(
    tmp_path,
) -> None:
    db_path = tmp_path / "upgrade.sqlite3"
    _apply_through_0102(db_path, tmp_path / "migrations-0102")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE llm_node_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                provider TEXT,
                model_id TEXT,
                status TEXT NOT NULL,
                reason_code TEXT,
                latency_ms INTEGER,
                input_digest TEXT,
                output_digest TEXT,
                ts_ms INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO llm_node_call_log(
                request_id, node_id, provider, model_id, status, reason_code,
                latency_ms, input_digest, output_digest, ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "request-old-ok",
                    "core_reasoner_primary",
                    "gemini",
                    "gemini-old",
                    "OK",
                    None,
                    25,
                    "raw prompt must not survive",
                    "raw narrative must not survive",
                    500,
                ),
                (
                    "request-old-failed",
                    "safety_verifier",
                    "gemini",
                    "gemini-old",
                    "DEGRADED",
                    "LEGACY_TIMEOUT",
                    30,
                    "secret input",
                    "secret output",
                    600,
                ),
            ),
        )
        conn.commit()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="llm-cold-migration-upgrade",
        ts_ms=3_000,
    )
    assert result.success is True, result.as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert _table_exists(conn, "llm_node_call_log") is False
        rows = conn.execute(
            """
            SELECT * FROM model_attempt_log
            WHERE retry_reason = 'legacy_cold_migration'
            ORDER BY request_id
            """
        ).fetchall()
        assert len(rows) == 2
        by_request = {row["request_id"]: row for row in rows}
        assert by_request["request-old-ok"]["status"] == "succeeded"
        assert by_request["request-old-failed"]["status"] == "failed"
        assert by_request["request-old-failed"]["error_code"] == "LEGACY_TIMEOUT"
        for row in rows:
            metadata = json.loads(row["metadata"])
            assert metadata["legacy_source"] == "llm_node_call_log"
            assert metadata["model_node_id"]
            assert row["output_digest"] is None
            assert row["error_message"] is None
            serialized = json.dumps(dict(row), ensure_ascii=False)
            assert "raw prompt" not in serialized
            assert "raw narrative" not in serialized
            assert "secret input" not in serialized
            assert "secret output" not in serialized

    replay = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="llm-cold-migration-replay",
        ts_ms=4_000,
    )
    assert replay.success is True, replay.as_dict()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM model_attempt_log WHERE retry_reason = 'legacy_cold_migration'"
        ).fetchone()[0]
        assert count == 2
        assert _table_exists(conn, "llm_node_call_log") is False


def test_0103_upgrades_a_legacy_inline_schema_database(tmp_path) -> None:
    """Exercise the pre-migration SQLiteLedger shape seen in old deployments."""

    db_path = tmp_path / "legacy-inline.sqlite3"
    ledger = SQLiteLedger(db_path)
    try:
        ledger._conn.executescript(
            """
            CREATE TABLE llm_node_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                provider TEXT,
                model_id TEXT,
                status TEXT NOT NULL,
                reason_code TEXT,
                latency_ms INTEGER,
                input_digest TEXT,
                output_digest TEXT,
                ts_ms INTEGER NOT NULL
            );
            INSERT INTO llm_node_call_log(
                request_id, node_id, provider, model_id, status,
                latency_ms, input_digest, output_digest, ts_ms
            ) VALUES (
                'request-inline', 'planner', 'gemini', 'legacy-model', 'OK',
                9, 'private-input', 'private-output', 900
            );
            """
        )
    finally:
        ledger.close()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="llm-cold-migration-inline-upgrade",
        ts_ms=5_000,
    )
    assert result.success is True, result.as_dict()

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM model_attempt_log
            WHERE request_id = 'request-inline'
              AND retry_reason = 'legacy_cold_migration'
            """
        ).fetchone()
        assert row is not None
        assert row["status"] == "succeeded"
        assert row["output_digest"] is None
        assert "private-input" not in json.dumps(dict(row), ensure_ascii=False)
        assert "private-output" not in json.dumps(dict(row), ensure_ascii=False)
        assert _table_exists(conn, "llm_node_call_log") is False
    finally:
        conn.close()


@pytest.mark.parametrize("existing_status", ["started", "succeeded"])
def test_0103_any_attempt_id_collision_fails_closed_without_dropping_legacy_evidence(
    tmp_path,
    existing_status: str,
) -> None:
    db_path = tmp_path / f"collision-{existing_status}.sqlite3"
    _apply_through_0102(db_path, tmp_path / "migrations-collision-0102")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE llm_node_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                provider TEXT,
                model_id TEXT,
                status TEXT NOT NULL,
                reason_code TEXT,
                latency_ms INTEGER,
                input_digest TEXT,
                output_digest TEXT,
                ts_ms INTEGER NOT NULL
            );
            INSERT INTO llm_node_call_log(
                request_id, node_id, provider, model_id, status, ts_ms
            ) VALUES ('request-collision', 'planner', 'test', 'legacy', 'OK', 10);
            """
        )
        conn.execute(
            """
            INSERT INTO model_attempt_log(
                attempt_id, request_id, route_decision_id, attempt_index,
                status, retry_reason, adapter_id, provider, model_id,
                prompt_id, prompt_version, prompt_digest, started_at_ms,
                ended_at_ms, latency_ms, output_digest, error_code,
                error_message, metadata, ts_ms
            ) VALUES (
                'legacy-llm-node-0000000000000001', 'different-request',
                'different-route', 1, ?, 'existing', 'existing',
                'test', 'existing', 'existing', '1',
                '0000000000000000000000000000000000000000000000000000000000000000',
                1, ?, ?, NULL, NULL, NULL, '{}', 2
            )
            """,
            (
                existing_status,
                None if existing_status == "started" else 2,
                None if existing_status == "started" else 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="llm-cold-migration-collision",
        ts_ms=6_000,
    )
    assert result.success is False
    assert result.reason_code == "MIGRATION_EXEC_FAILED"

    conn = sqlite3.connect(db_path)
    try:
        assert _table_exists(conn, "llm_node_call_log") is True
        assert conn.execute("SELECT COUNT(*) FROM llm_node_call_log").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM model_attempt_log WHERE attempt_id = ?",
                ("legacy-llm-node-0000000000000001",),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 103"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_model_attempt_projection_counts_one_terminal_row_and_explicit_identity() -> None:
    rows = [
        {
            "id": 1,
            "attempt_id": "attempt-1",
            "status": "started",
            "ts_ms": 10,
        },
        {
            "id": 2,
            "attempt_id": "attempt-1",
            "status": "succeeded",
            "ended_at_ms": 20,
            "ts_ms": 20,
            "metadata": {
                "model_node_id": "core_reasoner_primary",
                "provider_budget_measurements": {
                    "prompt_tokens": 7,
                    "completion_tokens": 5,
                    "token_count": 12,
                },
            },
        },
        {
            "id": 3,
            "attempt_id": "attempt-2",
            "status": "started",
            "ts_ms": 30,
        },
    ]

    terminal = latest_terminal_model_attempts(rows)
    assert [row["attempt_id"] for row in terminal] == ["attempt-1"]
    assert model_attempt_node_id(terminal[0]) == "core_reasoner_primary"
    assert model_attempt_token_counts(terminal[0]) == (7, 5, 12)


def test_model_attempt_token_projection_preserves_unmeasured_dimensions() -> None:
    assert model_attempt_token_counts({"total_tokens": 12}) == (None, None, 12)
    assert model_attempt_token_counts({"prompt_tokens": 7}) == (7, None, None)
    assert model_attempt_token_counts(
        {"prompt_tokens": 0, "completion_tokens": 0}
    ) == (0, 0, 0)
    assert model_attempt_token_counts(
        {
            "metadata": {
                "provider_budget_measurements": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                }
            }
        }
    ) == (3, 2, 5)


def test_llm_token_summary_distinguishes_empty_window_from_unmeasured_calls() -> None:
    from host_brain.app import obs_routes_stats_system as routes

    empty = routes._summarize_token_measurements([])
    assert empty["total_prompt_tokens"] == 0
    assert empty["prompt_tokens_measured_calls"] == 0
    assert empty["total_prompt_tokens_is_lower_bound"] is False

    unmeasured = routes._summarize_token_measurements(
        [
            {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        ]
    )
    assert unmeasured["total_prompt_tokens"] is None
    assert unmeasured["total_completion_tokens"] is None
    assert unmeasured["total_tokens"] is None
    assert unmeasured["prompt_tokens_measured_calls"] == 0
    assert unmeasured["completion_tokens_measured_calls"] == 0
    assert unmeasured["total_tokens_measured_calls"] == 0
    assert unmeasured["total_prompt_tokens_is_lower_bound"] is True
    assert unmeasured["total_completion_tokens_is_lower_bound"] is True
    assert unmeasured["total_tokens_is_lower_bound"] is True


def test_model_attempt_statuses_normalize_for_trace_and_node_health() -> None:
    from host_brain.app.obs_routes_core import (
        _compute_node_health,
        _normalize_trace_status,
    )

    assert _normalize_trace_status("succeeded") == "OK"
    for status in ("failed", "timed_out", "rejected", "cancelled"):
        assert _normalize_trace_status(status) == "FAIL"

    health = _compute_node_health(
        [
            {"status": "succeeded", "latency_ms": 12, "ts_ms": 100},
            {"status": "completed", "latency_ms": 15, "ts_ms": 110},
            {"status": "accepted", "latency_ms": 9, "ts_ms": 120},
        ]
    )
    assert health["status"] == "UP"
    assert health["success_rate"] == 100.0
    assert health["call_count"] == 3


def test_llm_call_stats_combines_terminal_attempts_and_pool_token_usage(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_stats_system as routes

    requested: list[str] = []

    class Projection:
        fail_closed = False

        @staticmethod
        def resolve_fetch_table(*candidates: str) -> str | None:
            return candidates[0] if candidates else None

        @staticmethod
        def fetch_by_time_range(table: str, **_kwargs):  # noqa: ANN001
            requested.append(table)
            if table == "model_attempt_log":
                return [
                        {
                            "attempt_id": "attempt-stats",
                            "status": "started",
                            "ts_ms": 9_999_100,
                        },
                    {
                        "attempt_id": "attempt-stats",
                        "status": "succeeded",
                        "provider": "gemini",
                        "model_id": "gemini-pro",
                            "latency_ms": 10,
                            "ended_at_ms": 9_999_110,
                            "ts_ms": 9_999_110,
                        "model_node_id": "core_reasoner_primary",
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                ]
            if table == "llm_token_usage_log":
                return [
                    {
                        "request_id": "request-pool",
                        "provider": "openai",
                        "model_id": "gpt-pool",
                        "prompt_tokens": 4,
                        "completion_tokens": 1,
                        "total_tokens": 5,
                        "ts_ms": 9_999_200,
                    }
                ]
            if table == "llm_pool_call_log":
                return [
                    {
                        "request_id": "request-pool",
                        "total_latency_ms": 20,
                        "ts_ms": 9_999_200,
                    }
                ]
            return []

    monkeypatch.setattr(routes, "_now_ms", lambda: 10_000_000)
    result = routes.get_llm_call_stats(
        window_minutes=60,
        interval_minutes=5,
        projection_view=Projection(),
    )

    assert requested == [
        "model_attempt_log",
        "llm_token_usage_log",
        "llm_pool_call_log",
    ]
    assert result.total_calls == 2
    assert result.total_prompt_tokens == 7
    assert result.total_completion_tokens == 3
    assert result.total_tokens == 10
    assert result.prompt_tokens_measured_calls == 2
    assert result.completion_tokens_measured_calls == 2
    assert result.total_tokens_measured_calls == 2
    assert result.total_prompt_tokens_is_lower_bound is False
    assert result.total_completion_tokens_is_lower_bound is False
    assert result.total_tokens_is_lower_bound is False
    assert result.avg_latency_ms == 15.0
    assert sum(result.timeseries_calls) == 2
    assert sum(result.timeseries_tokens) == 10
    assert sum(result.timeseries_token_measured_calls) == 2
    assert not any(result.timeseries_token_lower_bound_flags)
    assert {item.model_id: item.call_count for item in result.models} == {
        "gemini-pro": 1,
        "gpt-pool": 1,
    }
    assert all(item.total_tokens_measured_calls == 1 for item in result.models)
    assert not any(item.total_tokens_is_lower_bound for item in result.models)


def test_llm_call_stats_exposes_partial_token_coverage_as_lower_bounds(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_stats_system as routes

    class Projection:
        fail_closed = False

        @staticmethod
        def resolve_fetch_table(*candidates: str) -> str | None:
            return candidates[0] if candidates else None

        @staticmethod
        def fetch_by_time_range(table: str, **_kwargs):  # noqa: ANN001
            if table == "model_attempt_log":
                return [
                    {
                        "attempt_id": "attempt-total-only",
                        "status": "succeeded",
                        "model_id": "partial-model",
                        "total_tokens": 12,
                        "ended_at_ms": 9_999_100,
                        "ts_ms": 9_999_100,
                    },
                    {
                        "attempt_id": "attempt-unmeasured",
                        "status": "failed",
                        "model_id": "partial-model",
                        "ended_at_ms": 9_999_110,
                        "ts_ms": 9_999_110,
                    },
                ]
            if table == "llm_token_usage_log":
                return [
                    {
                        "request_id": "legacy-explicit",
                        "model_id": "legacy-model",
                        "prompt_tokens": 4,
                        "completion_tokens": 1,
                        "ts_ms": 9_999_200,
                    }
                ]
            if table == "llm_pool_call_log":
                return []
            return []

    monkeypatch.setattr(routes, "_now_ms", lambda: 10_000_000)
    result = routes.get_llm_call_stats(
        window_minutes=60,
        interval_minutes=5,
        projection_view=Projection(),
    )

    assert result.total_calls == 3
    assert result.total_prompt_tokens == 4
    assert result.total_completion_tokens == 1
    assert result.total_tokens == 17
    assert result.prompt_tokens_measured_calls == 1
    assert result.completion_tokens_measured_calls == 1
    assert result.total_tokens_measured_calls == 2
    assert result.total_prompt_tokens_is_lower_bound is True
    assert result.total_completion_tokens_is_lower_bound is True
    assert result.total_tokens_is_lower_bound is True

    by_model = {item.model_id: item for item in result.models}
    partial = by_model["partial-model"]
    assert partial.total_prompt_tokens is None
    assert partial.total_completion_tokens is None
    assert partial.total_tokens == 12
    assert partial.prompt_tokens_measured_calls == 0
    assert partial.completion_tokens_measured_calls == 0
    assert partial.total_tokens_measured_calls == 1
    assert partial.total_prompt_tokens_is_lower_bound is True
    assert partial.total_completion_tokens_is_lower_bound is True
    assert partial.total_tokens_is_lower_bound is True

    legacy = by_model["legacy-model"]
    assert legacy.total_prompt_tokens == 4
    assert legacy.total_completion_tokens == 1
    assert legacy.total_tokens == 5
    assert legacy.prompt_tokens_measured_calls == 1
    assert legacy.completion_tokens_measured_calls == 1
    assert legacy.total_tokens_measured_calls == 1
    assert legacy.total_prompt_tokens_is_lower_bound is False
    assert legacy.total_completion_tokens_is_lower_bound is False
    assert legacy.total_tokens_is_lower_bound is False

    measured_bucket = next(
        index for index, calls in enumerate(result.timeseries_calls) if calls == 3
    )
    assert result.timeseries_tokens[measured_bucket] == 17
    assert result.timeseries_token_measured_calls[measured_bucket] == 2
    assert result.timeseries_token_lower_bound_flags[measured_bucket] is True
