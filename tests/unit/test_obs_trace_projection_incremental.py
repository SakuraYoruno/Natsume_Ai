from __future__ import annotations

import time
from pathlib import Path

from host_brain.app import obs_routes_trace as trace_routes
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.persistence.graph_evidence_repository import GraphEvidenceRepository


class _TraceLedger:
    def __init__(self, db_path: Path, rows: list[dict[str, object]]) -> None:
        self.canonical_db_path = str(db_path)
        self._rows = rows

    def fetch_by_time_range(
        self,
        table: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
        offset: int,
    ) -> list[dict[str, object]]:
        del start_ms, end_ms, limit, offset
        return list(self._rows) if table == "governor_log" else []


def _trace_payload(request_id: str, timestamp: int) -> dict[str, object]:
    return {
        "request_id": request_id,
        "status": "OK",
        "node_count": 0,
        "nodes": [],
        "edges": [],
        "ts_ms": timestamp,
        "timestamp": timestamp,
    }


def test_trace_refresh_skips_unchanged_history_and_uses_one_bulk_cursor_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trace-incremental.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="trace-incremental",
    )
    assert migration.success, migration
    store = QueryProjectionStore(db_path)
    now_ms = int(time.time() * 1000)
    request_id = "req-stable"
    payload = _trace_payload(request_id, now_ms)
    store.upsert(
        "trace",
        request_id,
        payload,
        source_cursor=now_ms,
        source_ref="test://trace-incremental",
        freshness_ts_ms=now_ms - 60_000,
    )
    before = store.get("trace", request_id)
    assert before is not None

    bulk_calls: list[tuple[int, int]] = []

    def bulk_stream_cursors(
        _repository: GraphEvidenceRepository,
        *,
        since_ms: int,
        end_ms: int,
        event_types=None,
    ) -> list[dict[str, object]]:
        del event_types
        bulk_calls.append((since_ms, end_ms))
        return []

    def forbidden_per_request_read(*_args, **_kwargs):
        raise AssertionError("unchanged trace must not trigger an N+1 stream query")

    def forbidden_trace_build(*_args, **_kwargs):
        raise AssertionError("unchanged trace must not be rebuilt")

    monkeypatch.setattr(
        GraphEvidenceRepository,
        "list_stream_event_cursors_by_time",
        bulk_stream_cursors,
    )
    monkeypatch.setattr(
        GraphEvidenceRepository,
        "list_stream_events",
        forbidden_per_request_read,
    )
    monkeypatch.setattr(
        trace_routes,
        "_get_trace_builders",
        lambda: (
            forbidden_trace_build,
            None,
            lambda *_args, **_kwargs: {
                "type": "snapshot",
                "items": [],
                "count": 0,
                "timestamp": now_ms,
            },
            None,
            None,
        ),
    )

    result = trace_routes.refresh_trace_query_projections(
        ledger=_TraceLedger(
            db_path,
            [{"request_id": request_id, "ts_ms": now_ms}],
        ),
        store=store,
        tribe_event_reader=lambda: [],
    )

    after = store.get("trace", request_id)
    assert result["refreshed_count"] == 0
    assert result["unchanged_count"] == 1
    assert result["deferred_count"] == 0
    assert bulk_calls and len(bulk_calls) == 1
    assert after is not None
    assert after.projection_version == before.projection_version
    assert after.projection_cursor == before.projection_cursor
    assert after.freshness_ts_ms == before.freshness_ts_ms
    assert trace_routes.TRACE_HISTORY_MAX_AGE_MS >= 24 * 60 * 60 * 1000
