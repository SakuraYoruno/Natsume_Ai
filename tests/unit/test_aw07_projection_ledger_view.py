from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
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

    def fetch_by_time_range(self, table: str, **kwargs):
        rows = list(self.rows)
        rows.sort(key=lambda row: (int(row.get("ts_ms") or 0), str(row.get("id") or "")))
        if kwargs.get("descending"):
            rows.reverse()
        offset = max(0, int(kwargs.get("offset") or 0))
        limit = max(1, int(kwargs.get("limit") or len(rows) or 1))
        return rows[offset : offset + limit]


def test_stats_source_authority_uses_canonical_model_attempts() -> None:
    assert "model_attempt_log" in LEDGER_STATS_TABLE_FIELDS
    assert "llm_token_usage_log" in LEDGER_STATS_TABLE_FIELDS
    assert "llm_pool_call_log" in LEDGER_STATS_TABLE_FIELDS
    assert "llm_call_log" not in LEDGER_STATS_TABLE_FIELDS
    assert "capability_execution_receipt" in LEDGER_STATS_TABLE_FIELDS
    assert "graph_run_trace" not in LEDGER_STATS_TABLE_FIELDS
    assert "llm_node_call_log" not in LEDGER_STATS_TABLE_FIELDS
    assert "sandbox_exec_log" not in LEDGER_STATS_TABLE_FIELDS
    assert {
        "memory_ops_log",
        "life_memory_admission_receipt",
        "scheduler_event_log",
        "error_log",
        "avatar_action_log",
    }.issubset(LEDGER_STATS_TABLE_FIELDS)
    assert {
        "episodic_store_log",
        "memory_guard_log",
        "context_compress_log",
        "context_compressor_log",
        "distill_log",
        "distillation_cycle_log",
        "vts_event_log",
        "talk_gate_log",
    }.isdisjoint(LEDGER_STATS_TABLE_FIELDS)

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

    measured = sanitize_stats_row(
        "model_attempt_log",
        {
            "attempt_id": "attempt-2",
            "request_id": "request-2",
            "status": "succeeded",
            "metadata": {
                "model_node_id": "core_reasoner_primary",
                "provider_budget_measurements": {
                    "prompt_tokens": 7,
                    "completion_tokens": 5,
                    "token_count": 12,
                    "private_cost_detail": "drop-me",
                },
                "private_prompt": "drop-me",
            },
        },
    )
    assert measured == {
        "attempt_id": "attempt-2",
        "completion_tokens": 5,
        "model_node_id": "core_reasoner_primary",
        "prompt_tokens": 7,
        "request_id": "request-2",
        "status": "succeeded",
        "total_tokens": 12,
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


def test_real_ledger_latest_window_is_bounded_readable_and_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    """The projector keeps the newest stable set and polls without new facts."""

    from host_brain.app.observability_runtime import (
        projection_ledger_view as projection_module,
    )

    db_path = tmp_path / "ledger-stats-latest-window.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-latest-window"
    )
    assert migrated.success, migrated

    clock_ms = [2_000_000_000_000]
    shared_ts = clock_ms[0] - 100
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO error_log(request_id, stage, reason_code, detail, ts_ms)
            VALUES (?, 'MODEL', 'OK', 'private detail', ?)
            """,
            [
                (f"req-{index:05d}", shared_ts)
                for index in range(LEDGER_STATS_ROW_CAP + 2)
            ],
        )
        conn.commit()

    ledger = SQLiteLedger(db_path, initialize_schema=False)
    store = QueryProjectionStore(db_path, clock=lambda: clock_ms[0])
    lease = store.acquire_leader_lease("aw07-latest-window", lease_ms=10_000)
    assert lease is not None
    monkeypatch.setattr(
        projection_module.time,
        "time",
        lambda: clock_ms[0] / 1000,
    )

    # Same-timestamp rows use their durable id as the stable descending
    # tie-break instead of depending on SQLite's unspecified tie order.
    latest_three = ledger.fetch_by_time_range(
        "error_log",
        start_ms=shared_ts,
        end_ms=shared_ts,
        limit=3,
        descending=True,
    )
    assert [row["request_id"] for row in latest_three] == [
        f"req-{LEDGER_STATS_ROW_CAP + 1:05d}",
        f"req-{LEDGER_STATS_ROW_CAP:05d}",
        f"req-{LEDGER_STATS_ROW_CAP - 1:05d}",
    ]

    with store.bind_leader_lease(lease):
        refresh_ledger_stats_projection(ledger=ledger, store=store)
        first = store.get(LEDGER_STATS_KIND, "error_log")
        assert first is not None
        with sqlite3.connect(db_path) as conn:
            first_journal_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM operator_query_projection_journal
                    WHERE projection_kind = ?
                    """,
                    (LEDGER_STATS_KIND,),
                ).fetchone()[0]
            )

        clock_ms[0] += 1_000
        refresh_ledger_stats_projection(ledger=ledger, store=store)
        second = store.get(LEDGER_STATS_KIND, "error_log")
        assert second is not None
        with sqlite3.connect(db_path) as conn:
            second_journal_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM operator_query_projection_journal
                    WHERE projection_kind = ?
                    """,
                    (LEDGER_STATS_KIND,),
                ).fetchone()[0]
            )

    assert first_journal_count == second_journal_count
    assert second.source_cursor == first.source_cursor
    assert second.source_digest == first.source_digest
    assert second.projection_cursor == first.projection_cursor
    assert second.projection_version == first.projection_version
    assert second.freshness_ts_ms == first.freshness_ts_ms + 1_000

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO error_log(request_id, stage, reason_code, detail, ts_ms)
            VALUES ('req-new-source-fact', 'MODEL', 'OK', 'private detail', ?)
            """,
            (shared_ts + 1,),
        )
        conn.commit()
    clock_ms[0] += 1_000
    with store.bind_leader_lease(lease):
        refresh_ledger_stats_projection(ledger=ledger, store=store)
    third = store.get(LEDGER_STATS_KIND, "error_log")
    assert third is not None
    with sqlite3.connect(db_path) as conn:
        third_journal_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM operator_query_projection_journal
                WHERE projection_kind = ?
                """,
                (LEDGER_STATS_KIND,),
            ).fetchone()[0]
        )
    assert third.source_cursor > second.source_cursor
    assert third.source_digest != second.source_digest
    assert third_journal_count == second_journal_count + 1

    rows = list(third.payload["rows"])
    assert third.payload["truncated"] is True
    assert len(rows) == LEDGER_STATS_ROW_CAP
    projected_request_ids = {str(row["request_id"]) for row in rows}
    assert "req-00000" not in projected_request_ids
    assert "req-00001" not in projected_request_ids
    assert "req-00002" not in projected_request_ids
    assert "req-new-source-fact" in projected_request_ids

    # A window that touches the trimmed boundary is incomplete and must fail
    # closed.  A strictly newer window remains queryable, so a high-volume
    # table is not permanently unavailable.
    view = ProjectionLedgerView(
        store,
        clock=lambda: clock_ms[0],
        fail_closed=True,
    )
    assert view.fetch_by_time_range(
        "error_log",
        start_ms=shared_ts,
        end_ms=clock_ms[0],
        limit=LEDGER_STATS_ROW_CAP,
    ) == []
    assert view.last_degraded_reason == "PROJECTION_SOURCE_TRUNCATED"
    visible = view.fetch_by_time_range(
        "error_log",
        start_ms=shared_ts + 1,
        end_ms=clock_ms[0],
        limit=LEDGER_STATS_ROW_CAP,
    )
    assert [row["request_id"] for row in visible] == ["req-new-source-fact"]
    assert view.last_degraded_reason is None
    ledger.close()


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


def test_view_construction_uses_only_the_explicit_store(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies

    def forbidden(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("explicit view touched a process locator")

    monkeypatch.setattr(
        dependencies,
        "peek_query_projection_store",
        forbidden,
        raising=False,
    )
    monkeypatch.setattr(dependencies, "get_ledger", forbidden)
    now = 2_000_000_000_000
    view = ProjectionLedgerView(
        _DocumentStore(
            {
                "table": "error_log",
                "rows": [],
                "retention_ms": LEDGER_STATS_RETENTION_MS,
                "row_cap": LEDGER_STATS_ROW_CAP,
                "truncated": False,
                "stable_cursor": "empty",
            },
            freshness_ts_ms=now,
        ),
        clock=lambda: now,
    )
    assert view.fetch_by_time_range(
        "error_log",
        start_ms=now - 1_000,
        end_ms=now,
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


def test_view_rejects_source_unavailable_and_only_serves_covered_latest_window() -> None:
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
            {
                **base,
                "rows": [
                    {"ts_ms": now - 100, "request_id": "cutoff"},
                    {"ts_ms": now - 50, "request_id": "latest"},
                ],
                "truncated": True,
            },
            freshness_ts_ms=now,
        ),
        clock=lambda: now,
    )
    assert truncated.fetch_by_time_range(
        "error_log", start_ms=now - 1_000, end_ms=now
    ) == []
    assert truncated.last_degraded_reason == "PROJECTION_SOURCE_TRUNCATED"
    assert truncated.fetch_by_time_range(
        "error_log", start_ms=now - 99, end_ms=now
    ) == [{"ts_ms": now - 50, "request_id": "latest"}]
    assert truncated.last_degraded_reason is None

    direct = ProjectionLedgerView(
        _DocumentStore(
            {
                **base,
                "rows": [{"ts_ms": now - 50, "request_id": "latest"}],
                "truncated": True,
            },
            freshness_ts_ms=now,
        ),
        clock=lambda: now,
    )
    assert direct.error_log == []
    assert direct.last_degraded_reason == "PROJECTION_SOURCE_TRUNCATED"
    assert direct.fetch_by_request_id("error_log", "missing") == []
    assert direct.last_degraded_reason == "PROJECTION_SOURCE_TRUNCATED"


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
                "table": "avatar_action_log",
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


def test_memory_and_vts_stats_use_durable_projection_after_source_shutdown(
    tmp_path,
) -> None:
    """Cold independent Obs reads real sources without reopening Host state."""

    from host_brain.app.obs_routes_stats_agent import get_memory_stats
    from host_brain.app.obs_routes_stats_capability_skill import (
        get_context_compressor_stats,
        get_episodic_store_stats,
        get_memory_guard_stats,
    )
    from host_brain.app.obs_routes_stats_system import (
        get_distillation_stats,
        get_vts_stats,
    )

    db_path = tmp_path / "memory-vts-stats.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-memory-vts-source"
    )
    assert migrated.success, migrated
    ledger = SQLiteLedger(db_path, initialize_schema=False)
    now = int(time.time() * 1000)

    ledger.log_memory_op(
        {
            "request_id": "memory-write-1",
            "intent_id": "intent-1",
            "op_kind": "WRITE",
            "memory_id": "memory-1",
            "target_memory_id": None,
            "source_memory_ids": [],
            "evidence_refs": ["chat://request/memory-write-1"],
            "status": "SUCCESS",
            "reason_code": None,
            "detail": "private memory detail",
            "decay_profile_id": "default",
            "visibility": "internal",
            "ts_ms": now - 900,
        }
    )
    ledger.log_scheduler_event(
        {
            "event_id": "compress-1",
            "task_id": "context_compressor",
            "trigger_ts": now - 800,
            "intent_id": None,
            "status": "COMPRESS_BEFORE_SAVE",
            "reason_code": "COMPRESS_BEFORE_SAVE",
            "mode_at_trigger": "session-1",
            "error_detail": "saved_count=2 private",
            "ts_ms": now - 800,
        }
    )
    ledger.log_error(
        request_id="distillation_engine",
        stage="DISTILL_DONE",
        reason_code="DISTILL_DONE",
        detail="private distillation result",
    )
    ledger.log_error(
        request_id="distillation_engine",
        stage="DISTILL_SKIPPED",
        reason_code="DISTILL_SKIPPED",
        detail="private skip reason",
    )

    for index, (status, emotion) in enumerate(
        (("APPLIED", "HAPPY"), ("DEGRADED", "NEUTRAL"), ("REJECTED", "BAD"))
    ):
        ledger.log_avatar_action(
            {
                "request_id": f"vts-{index}",
                "trigger": "LLM",
                "emotion_tag": emotion,
                "expression_id": f"expression-{index}",
                "status": status,
                "reason_code": None,
                "stage": "S_AVATAR_EXPRESSION_MAP",
                "detail": "private avatar detail",
                "ts_ms": now - 700 + index,
            }
        )
    for index, status in enumerate(("APPLIED", "REJECTED")):
        ledger.log_avatar_action(
            {
                "request_id": f"talk-{index}",
                "trigger": "HOST",
                "emotion_tag": None,
                "expression_id": f"talk_gate::{index}",
                "status": status,
                "reason_code": None,
                "stage": "S_AVATAR_TALK_GATE",
                "detail": "private talk gate detail",
                "ts_ms": now - 600 + index,
            }
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for index in range(2):
            conn.execute(
                """
                INSERT INTO life_episode (
                    episode_id, episode_kind, salience, truth_state, status,
                    created_ts_ms, last_touched_ts_ms
                ) VALUES (?, 'CHAT', 0.5, 'CANDIDATE', 'ACTIVE', ?, ?)
                """,
                (f"episode-{index}", now - 500 + index, now - 500 + index),
            )
        conn.execute(
            """
            INSERT INTO life_memory_admission_receipt (
                request_id, command_hash, decision, status, reason_code,
                worth_score, episode_id, outbox_id, created_ts_ms
            ) VALUES (?, ?, 'WRITE', 'COMMITTED', 'MEMORY_ADMISSION_ACCEPTED',
                      0.9, ?, ?, ?)
            """,
            ("admission-ok", "a" * 64, "episode-0", "outbox-0", now - 500),
        )
        conn.execute(
            """
            INSERT INTO life_memory_admission_receipt (
                request_id, command_hash, decision, status, reason_code,
                worth_score, episode_id, created_ts_ms
            ) VALUES (?, ?, 'WRITE_LIGHT', 'COMMITTED',
                      'MEMORY_GUARD_LIGHT_ONLY:TEST_REJECTED', 0.8, ?, ?)
            """,
            ("admission-guard", "b" * 64, "episode-1", now - 499),
        )
        conn.commit()

    store = QueryProjectionStore(db_path)
    lease = store.acquire_leader_lease("aw07-memory-vts", lease_ms=10_000)
    assert lease is not None
    with store.bind_leader_lease(lease):
        refresh_ledger_stats_projection(ledger=ledger, store=store)

    admission_document = store.get(
        LEDGER_STATS_KIND,
        "life_memory_admission_receipt",
    )
    assert admission_document is not None
    assert len(admission_document.payload["rows"]) == 2
    assert "command_hash" not in str(admission_document.payload)

    # Source shutdown proves the HTTP calculation is app-local/projection-only.
    ledger.close()
    projection_view = ProjectionLedgerView(store, fail_closed=True)
    memory = get_memory_stats(window_minutes=60, projection_view=projection_view)
    vts = get_vts_stats(window_minutes=60, projection_view=projection_view)
    guard = get_memory_guard_stats(
        window_minutes=60,
        projection_view=projection_view,
    )
    compressor = get_context_compressor_stats(
        window_minutes=60,
        projection_view=projection_view,
    )
    episodic = get_episodic_store_stats(
        window_minutes=60,
        projection_view=projection_view,
    )
    distillation = get_distillation_stats(
        window_minutes=60,
        limit=10,
        projection_view=projection_view,
    )

    assert memory.total_writes == 1
    assert memory.guard_reject_rate == 50.0
    assert memory.compress_rate is None
    assert memory.distill_count is None
    assert memory.observed_distillation_terminal_events == 2
    by_subsystem = {item.subsystem: item for item in memory.subsystems}
    compressor_subsystem = by_subsystem["context_compressor"]
    assert (
        compressor_subsystem.total,
        compressor_subsystem.success,
        compressor_subsystem.failed,
    ) == (
        None,
        None,
        None,
    )
    assert compressor_subsystem.success_rate is None
    assert compressor_subsystem.extra["observed_pre_save_hooks"] == 1
    distiller_subsystem = by_subsystem["long_term_distiller"]
    assert (
        distiller_subsystem.total,
        distiller_subsystem.success,
        distiller_subsystem.failed,
    ) == (
        None,
        None,
        None,
    )
    assert distiller_subsystem.extra["observed_terminal_events"] == 2
    assert distiller_subsystem.extra["observed_done_events"] == 1
    assert distiller_subsystem.extra["observed_skipped_events"] == 1
    assert distiller_subsystem.extra["cycle_measurement_available"] is False
    assert distiller_subsystem.success_rate is None

    assert vts.total_events == 3
    assert (vts.applied, vts.degraded, vts.rejected) == (1, 1, 1)
    assert vts.talk_gate_total == 2
    assert (vts.talk_gate_allowed, vts.talk_gate_blocked) == (1, 1)

    assert (guard.total_checks, guard.passed_count, guard.blocked_count) == (
        2,
        1,
        1,
    )
    assert guard.source_table == "life_memory_admission_receipt"
    assert guard.extended_measurement_available is False
    assert guard.distill_eligible_count is None
    assert compressor.observed_pre_save_hooks == 1
    assert compressor.total_compressions is None
    assert compressor.strategy_distribution is None
    assert compressor.outcome_measurement_available is False
    assert compressor.total_tokens_saved is None
    assert (episodic.total_attempts, episodic.written_count) == (1, 1)
    assert episodic.memory_guard_blocked_count == 1
    assert episodic.extended_guard_measurement_available is False
    assert distillation.total_cycles is None
    assert distillation.success_cycles is None
    assert distillation.skipped_cycles is None
    assert distillation.failed_cycles is None
    assert distillation.observed_terminal_events == 2
    assert distillation.observed_done_events == 1
    assert distillation.observed_skipped_events == 1
    assert distillation.cycle_measurement_available is False
    assert distillation.failure_measurement_available is False
    assert distillation.recent_cycles is None
    assert sorted(item.stage for item in distillation.recent_terminal_events) == [
        "DISTILL_DONE",
        "DISTILL_SKIPPED",
    ]


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
