from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.observability_runtime.projection_ledger_view import (
    LEDGER_STATS_KIND,
    LEDGER_STATS_MAX_AGE_MS,
    LEDGER_STATS_RETENTION_MS,
    LEDGER_STATS_ROW_CAP,
    LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR,
    LEDGER_STATS_TABLE_FIELDS,
    ProjectionLedgerView,
    ProjectionLedgerViewUnavailable,
    _load_table_rows,
    get_projection_ledger_view,
    refresh_ledger_stats_projection,
    sanitize_stats_row,
)
from host_brain.observability.projection_store import QueryProjectionStore


class _DocumentStore:
    def __init__(self, payload, *, freshness_ts_ms: int = 0):
        self.payload = payload
        self.freshness_ts_ms = freshness_ts_ms

    def get(self, kind: str, key: str):
        assert kind == LEDGER_STATS_KIND
        return SimpleNamespace(
            payload=self.payload,
            source_cursor=1,
            freshness_ts_ms=self.freshness_ts_ms,
            degraded_state="HEALTHY",
        )


class _RowsLedger:
    def __init__(self, rows):
        self.rows = rows

    def resolve_fetch_table(self, table: str):
        return table

    def fetch_by_time_range(self, table: str, **_kwargs):
        return list(self.rows)


def test_stats_source_authority_uses_canonical_model_attempts() -> None:
    assert "model_attempt_log" in LEDGER_STATS_TABLE_FIELDS
    assert "capability_execution_receipt" in LEDGER_STATS_TABLE_FIELDS
    assert "graph_run_trace" not in LEDGER_STATS_TABLE_FIELDS
    assert "llm_node_call_log" not in LEDGER_STATS_TABLE_FIELDS
    assert "sandbox_exec_log" not in LEDGER_STATS_TABLE_FIELDS

    projected = sanitize_stats_row(
        "model_attempt_log",
        {
            "attempt_id": "attempt-1",
            "request_id": "request-1",
            "status": "succeeded",
            "model_id": "gpt-5.2",
            "latency_ms": 42,
            "prompt_digest": "private-prompt-digest",
        },
    )
    assert projected == {
        "attempt_id": "attempt-1",
        "latency_ms": 42,
        "model_id": "gpt-5.2",
        "request_id": "request-1",
        "status": "succeeded",
    }


def test_allowlisted_table_with_unresolved_canonical_source_is_unavailable() -> None:
    class _UnresolvedLedger:
        @staticmethod
        def resolve_fetch_table(_table: str):
            return None

        @staticmethod
        def fetch_by_time_range(*_args, **_kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("unresolved source must not be queried")

    rows, truncated, cursor = _load_table_rows(
        _UnresolvedLedger(),
        "error_log",
        now_ms=2_000_000_000_000,
    )

    assert rows == []
    assert truncated is False
    assert cursor == LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR


def test_source_projection_is_capped_stable_and_drops_private_fields() -> None:
    now = 2_000_000_000_000
    rows = [
        {
            "ts_ms": now - 10_000 + index,
            "request_id": f"req-{index:05d}",
            "stage": "MODEL",
            "reason_code": "OK",
            "detail": f"private body {index}",
            "raw_text": f"raw body {index}",
            "input_text": f"input body {index}",
            "authorization": "Bearer should-not-survive",
            "api_key": "secret",
        }
        for index in range(LEDGER_STATS_ROW_CAP + 3)
    ]

    projected, truncated, cursor = _load_table_rows(
        _RowsLedger(rows), "error_log", now_ms=now
    )
    repeated, repeated_truncated, repeated_cursor = _load_table_rows(
        _RowsLedger(rows), "error_log", now_ms=now
    )

    assert len(projected) == LEDGER_STATS_ROW_CAP
    assert truncated is repeated_truncated is True
    assert cursor == repeated_cursor
    assert projected == repeated
    serialized = str(projected)
    assert "private body" not in serialized
    assert "raw body" not in serialized
    assert "input body" not in serialized
    assert "should-not-survive" not in serialized
    assert "secret" not in serialized


def test_view_filters_parameterized_window_and_fails_closed_outside_retention() -> None:
    now = 2_000_000_000_000
    payload = {
        "table": "error_log",
        "rows": [
            {"ts_ms": now - 2_000, "request_id": "older", "stage": "A"},
            {"ts_ms": now - 1_000, "request_id": "newer", "stage": "B"},
        ],
        "retention_ms": LEDGER_STATS_RETENTION_MS,
        "row_cap": LEDGER_STATS_ROW_CAP,
        "truncated": False,
        "stable_cursor": "stable",
    }
    view = ProjectionLedgerView(_DocumentStore(payload), clock=lambda: now)

    assert view.fetch_by_time_range(
        "error_log", start_ms=now - 1_500, end_ms=now, limit=100
    ) == [{"ts_ms": now - 1_000, "request_id": "newer", "stage": "B"}]
    assert view.fetch_by_request_id("error_log", "older")[0]["stage"] == "A"
    assert view.fetch_by_time_range("error_log", start_ms=0, end_ms=now) == []
    assert view.last_degraded_reason == "PROJECTION_WINDOW_OUT_OF_RANGE"


def test_get_factory_only_peeks_initialized_store(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies

    monkeypatch.setattr(dependencies, "peek_query_projection_store", lambda: None)
    monkeypatch.setattr(
        dependencies,
        "get_ledger",
        lambda: (_ for _ in ()).throw(AssertionError("GET opened canonical ledger")),
    )
    view = get_projection_ledger_view()
    assert view.fetch_by_time_range(
        "error_log",
        start_ms=2_000_000_000_000 - 1_000,
        end_ms=2_000_000_000_000,
    ) == []


def test_stale_table_projection_returns_stable_degraded_empty() -> None:
    now = 2_000_000_000_000
    payload = {
        "table": "error_log",
        "rows": [{"ts_ms": now - 1_000, "request_id": "stale"}],
        "retention_ms": LEDGER_STATS_RETENTION_MS,
        "row_cap": LEDGER_STATS_ROW_CAP,
        "truncated": False,
        "stable_cursor": "stable",
    }
    view = ProjectionLedgerView(
        _DocumentStore(payload, freshness_ts_ms=now - LEDGER_STATS_MAX_AGE_MS - 1),
        clock=lambda: now,
    )
    assert view.fetch_by_time_range(
        "error_log", start_ms=now - 2_000, end_ms=now
    ) == []
    assert view.last_degraded_reason == "PROJECTION_STALE"


def test_view_rejects_source_unavailable_cursor_and_truncation() -> None:
    now = 2_000_000_000_000
    base = {
        "table": "error_log",
        "rows": [],
        "retention_ms": LEDGER_STATS_RETENTION_MS,
        "row_cap": LEDGER_STATS_ROW_CAP,
        "truncated": False,
        "stable_cursor": "0:empty",
    }
    unavailable = ProjectionLedgerView(
        _DocumentStore(
            {**base, "stable_cursor": LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR},
            freshness_ts_ms=now,
        ),
        clock=lambda: now,
    )
    assert unavailable.fetch_by_time_range(
        "error_log", start_ms=now - 1_000, end_ms=now
    ) == []
    assert unavailable.last_degraded_reason == "PROJECTION_SOURCE_UNAVAILABLE"

    truncated = ProjectionLedgerView(
        _DocumentStore(
            {**base, "truncated": True},
            freshness_ts_ms=now,
        ),
        clock=lambda: now,
    )
    assert truncated.fetch_by_time_range(
        "error_log", start_ms=now - 1_000, end_ms=now
    ) == []
    assert truncated.last_degraded_reason == "PROJECTION_SOURCE_TRUNCATED"


def test_view_converts_projection_store_failure_to_typed_degraded_reason() -> None:
    class UnavailableStore:
        @staticmethod
        def get(_kind: str, _key: str):
            raise RuntimeError("private durable store detail")

    now = 2_000_000_000_000
    view = ProjectionLedgerView(UnavailableStore(), clock=lambda: now)
    assert view.fetch_by_time_range(
        "error_log", start_ms=now - 1_000, end_ms=now
    ) == []
    assert view.last_degraded_reason == "PROJECTION_SOURCE_UNAVAILABLE"


def test_direct_stats_route_cannot_swallow_degraded_view_as_healthy_zero() -> None:
    from host_brain.app.obs_routes_stats_system import get_vts_stats

    now = int(time.time() * 1000)
    view = ProjectionLedgerView(
        _DocumentStore(
            {
                "table": "vts_event_log",
                "rows": [],
                "retention_ms": LEDGER_STATS_RETENTION_MS,
                "row_cap": LEDGER_STATS_ROW_CAP,
                "truncated": False,
                "stable_cursor": LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR,
            },
            freshness_ts_ms=now,
        ),
        clock=lambda: int(time.time() * 1000),
        fail_closed=True,
    )

    with pytest.raises(ProjectionLedgerViewUnavailable) as captured:
        get_vts_stats(window_minutes=60, projection_view=view)

    assert captured.value.status_code == 503
    assert captured.value.detail["reason_code"] == (
        "PROJECTION_SOURCE_UNAVAILABLE"
    )


def test_all_direct_projection_stats_routes_have_fail_closed_guard() -> None:
    root = Path(__file__).resolve().parents[2] / "host_brain" / "app"
    guarded: list[str] = []
    offenders: list[str] = []
    for path in root.glob("obs_routes*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_get_route = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "get"
                for decorator in node.decorator_list
            )
            if not is_get_route:
                continue
            uses_projection_view = any(
                isinstance(child, ast.Name)
                and child.id == "require_obs_projection_ledger_view"
                for child in ast.walk(node.args)
            )
            if not uses_projection_view:
                continue
            has_guard = any(
                isinstance(decorator, ast.Name)
                and decorator.id == "fail_closed_projection_route"
                for decorator in node.decorator_list
            )
            route_name = f"{path.name}:{node.name}"
            if has_guard:
                guarded.append(route_name)
            else:
                offenders.append(route_name)

    assert offenders == []
    assert len(guarded) == 40


def test_source_failure_is_durably_materialized_as_unavailable(tmp_path) -> None:
    db_path = tmp_path / "projection-source-unavailable.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-source-unavailable"
    )
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    lease = store.acquire_leader_lease("aw07-source-unavailable", lease_ms=10_000)
    assert lease is not None

    class UnavailableLedger:
        @staticmethod
        def resolve_fetch_table(table: str) -> str:
            return table

        @staticmethod
        def fetch_by_time_range(*_args, **_kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("private canonical ledger detail")

    with store.bind_leader_lease(lease):
        refresh_ledger_stats_projection(ledger=UnavailableLedger(), store=store)

    document = store.get(LEDGER_STATS_KIND, "error_log")
    assert document is not None
    assert document.payload["stable_cursor"] == LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR
    assert document.payload["rows"] == []
    assert document.degraded_state == ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert document.degraded_detail is None


def test_projection_database_contains_no_raw_body_or_secret(tmp_path) -> None:
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-stats")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path, clock=lambda: 2_000_000_000_000)
    lease = store.acquire_leader_lease("aw07-test", lease_ms=10_000)
    assert lease is not None
    raw = {
        "ts_ms": 1_999_999_999_000,
        "request_id": "req-public",
        "reason_code": "MODEL_FAILED",
        "detail": "private conversation body",
        "raw_text": "raw user input",
        "token": "approval::secret-proof",
    }
    row = sanitize_stats_row("error_log", raw)
    payload = {
        "table": "error_log",
        "rows": [row],
        "retention_ms": LEDGER_STATS_RETENTION_MS,
        "row_cap": LEDGER_STATS_ROW_CAP,
        "truncated": False,
        "stable_cursor": "1999999999000:stable",
    }
    with store.bind_leader_lease(lease):
        store.upsert(LEDGER_STATS_KIND, "error_log", payload, source_cursor=1)

    with sqlite3.connect(db_path) as conn:
        raw_db = "\n".join(
            str(value)
            for row_value in conn.execute(
                "SELECT source_payload_json FROM operator_query_projection_journal "
                "UNION ALL SELECT payload_json FROM operator_query_projection_document"
            )
            for value in row_value
        )
    assert "req-public" in raw_db
    assert "private conversation body" not in raw_db
    assert "raw user input" not in raw_db
    assert "approval::secret-proof" not in raw_db


def test_converted_get_handlers_do_not_call_canonical_ledger() -> None:
    root = Path(__file__).resolve().parents[2]
    files = [
        "obs_routes_metrics.py",
        "obs_routes_platform.py",
        "obs_routes_proactive_autonomy.py",
        "obs_routes_stats_agent.py",
        "obs_routes_stats_capability_agent.py",
        "obs_routes_stats_capability_skill.py",
        "obs_routes_stats_governor.py",
        "obs_routes_stats_input_dispatch.py",
        "obs_routes_stats_input_humanoid.py",
        "obs_routes_stats_system.py",
    ]
    offenders: list[str] = []
    for filename in files:
        path = root / "host_brain" / "app" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_query = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in {"get", "websocket"}
                for decorator in node.decorator_list
            )
            if not is_query:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Name) and child.func.id in {
                    "_get_ledger", "get_ledger", "_get_ledger_dep"
                }:
                    offenders.append(f"{filename}:{node.name}:{child.lineno}")
    assert offenders == []
