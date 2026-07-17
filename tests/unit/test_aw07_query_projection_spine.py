from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from host_brain.app.obs_routes_trace import get_trace
from host_brain.app.obs_route_ports import (
    ObsRoutePorts,
    require_obs_projection_ledger_view,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.observability_runtime.projection_runtime import ProjectionOperatorReadModelService
from host_brain.observability.projection_store import (
    PROJECTION_CURSOR_CONFLICT,
    PROJECTION_CURSOR_REGRESSION,
    PROJECTION_LEASE_FENCED,
    QueryProjectionError,
    QueryProjectionStore,
    digest_json,
)
from host_brain.observability.projection_worker import ProjectionSource, QueryProjectionWorker
from host_brain.app.observability_runtime.session_query import SessionQueryService
from natsume_contracts.operator.v1 import ProjectionDegradedState


def _store(tmp_path, *, clock=lambda: 1000) -> QueryProjectionStore:
    db_path = tmp_path / "projection.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-unit")
    assert result.success, result
    return QueryProjectionStore(db_path, clock=clock)


def _obs_request(store: QueryProjectionStore) -> SimpleNamespace:
    ports = ObsRoutePorts(query_projection_store_provider=lambda: store)

    class ObsRequest(SimpleNamespace):
        async def is_disconnected(self) -> bool:
            return False

    return ObsRequest(
        app=SimpleNamespace(state=SimpleNamespace(obs_route_ports=ports)),
    )


def test_projection_ledger_view_is_built_from_each_obs_app_store(tmp_path):
    first = _store(tmp_path / "first")
    second = _store(tmp_path / "second")

    first_view = require_obs_projection_ledger_view(_obs_request(first))
    second_view = require_obs_projection_ledger_view(_obs_request(second))

    assert first_view is not second_view
    assert first_view._store is first
    assert second_view._store is second
    assert first_view.fail_closed is True
    assert second_view.fail_closed is True


def test_generic_kind_key_store_tracks_version_cursor_freshness_and_degraded_state(tmp_path):
    store = _store(tmp_path)
    first = store.upsert(
        "device_status",
        "avatar",
        {"online": True},
        source_cursor=7,
        source_ref="edge-receipt",
    )
    assert first.projection_version == 1
    assert first.projection_cursor >= 1
    assert first.source_cursor == 7
    assert first.freshness_ts_ms == 1000
    assert first.degraded_state is ProjectionDegradedState.HEALTHY

    same = store.upsert(
        "device_status",
        "avatar",
        {"online": True},
        source_cursor=7,
        source_ref="edge-receipt",
    )
    assert same.projection_version == 1

    changed = store.upsert(
        "device_status",
        "avatar",
        {"online": False},
        source_cursor=8,
        source_ref="edge-receipt",
    )
    assert changed.projection_version == 2
    assert changed.payload == {"online": False}
    degraded = store.mark_degraded(
        "device_status",
        "avatar",
        state="SOURCE_UNAVAILABLE",
        detail="edge disconnected",
    )
    assert degraded is not None
    assert degraded.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE


def test_upsert_cas_rejects_old_cursor_and_same_cursor_digest_conflict(tmp_path):
    store = _store(tmp_path)
    current = store.upsert("trace", "req", {"value": 10}, source_cursor=10)

    with pytest.raises(QueryProjectionError) as regression:
        store.upsert("trace", "req", {"value": 9}, source_cursor=9)
    assert regression.value.reason_code == PROJECTION_CURSOR_REGRESSION

    with pytest.raises(QueryProjectionError) as conflict:
        store.upsert("trace", "req", {"value": 11}, source_cursor=10)
    assert conflict.value.reason_code == PROJECTION_CURSOR_CONFLICT
    unchanged = store.get("trace", "req")
    assert unchanged is not None
    assert unchanged.payload == current.payload
    assert unchanged.source_cursor == 10


def test_two_store_instances_compete_on_durable_lease_and_stale_fence_cannot_write(tmp_path):
    now = [1000]
    db_path = tmp_path / "projection-leader.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-leader")
    assert migrated.success, migrated
    first_store = QueryProjectionStore(db_path, clock=lambda: now[0])
    second_store = QueryProjectionStore(db_path, clock=lambda: now[0])

    first_lease = first_store.acquire_leader_lease("worker-a", lease_ms=10)
    assert first_lease is not None
    assert second_store.acquire_leader_lease("worker-b", lease_ms=10) is None

    now[0] = 1010
    second_lease = second_store.acquire_leader_lease("worker-b", lease_ms=10)
    assert second_lease is not None
    assert second_lease.fencing_token == first_lease.fencing_token + 1
    assert first_store.renew_leader_lease(first_lease, lease_ms=10) is None
    assert first_store.release_leader_lease(first_lease) is False

    with first_store.bind_leader_lease(first_lease):
        with pytest.raises(QueryProjectionError) as fenced:
            first_store.upsert("trace", "req", {"value": "stale"}, source_cursor=1)
    assert fenced.value.reason_code == PROJECTION_LEASE_FENCED
    assert first_store.get("trace", "req") is None

    with second_store.bind_leader_lease(second_lease):
        accepted = second_store.upsert("trace", "req", {"value": "fresh"}, source_cursor=2)
    assert accepted.payload == {"value": "fresh"}


def test_evidence_freshness_does_not_replace_operation_clock_for_lease_or_journal(
    tmp_path,
) -> None:
    now = [1_000]
    store = _store(tmp_path, clock=lambda: now[0])
    lease = store.acquire_leader_lease("evidence-reader", lease_ms=10)
    assert lease is not None

    now[0] = 1_005
    with store.bind_leader_lease(lease):
        document = store.upsert(
            "security_immune",
            "current",
            {"evidence": "old-but-within-source-policy"},
            source_cursor=7,
            freshness_ts_ms=123,
        )

    assert document.freshness_ts_ms == 123
    with store._connect() as conn:  # noqa: SLF001 - physical clock contract
        journal = conn.execute(
            """
            SELECT observed_ts_ms
            FROM operator_query_projection_journal
            WHERE projection_kind = 'security_immune' AND document_key = 'current'
            """
        ).fetchone()
        persisted = conn.execute(
            """
            SELECT freshness_ts_ms, updated_ts_ms
            FROM operator_query_projection_document
            WHERE projection_kind = 'security_immune' AND document_key = 'current'
            """
        ).fetchone()
    assert journal is not None and int(journal[0]) == 1_005
    assert persisted is not None
    assert (int(persisted[0]), int(persisted[1])) == (123, 1_005)

    # If freshness were still reused as the lease clock, this old timestamp
    # would make the expired lease appear live and the write would succeed.
    now[0] = 1_011
    with store.bind_leader_lease(lease):
        with pytest.raises(QueryProjectionError) as fenced:
            store.upsert(
                "security_immune",
                "next",
                {"evidence": "must-not-write"},
                source_cursor=8,
                freshness_ts_ms=1,
            )
    assert fenced.value.reason_code == PROJECTION_LEASE_FENCED
    assert store.get("security_immune", "next") is None


@pytest.mark.parametrize(
    "operation",
    ("acquire", "renew", "release", "upsert", "mark_degraded", "rebuild"),
)
def test_leader_operations_resample_clock_after_waiting_for_sqlite_write_lock(
    tmp_path,
    monkeypatch,
    operation: str,
) -> None:
    now = [1_000]
    store = _store(tmp_path / operation, clock=lambda: now[0])
    lease = store.acquire_leader_lease("worker-a", lease_ms=10)
    assert lease is not None
    if operation in {"mark_degraded", "rebuild"}:
        store.upsert(
            "lease-clock",
            "current",
            {"value": "last-good"},
            source_cursor=1,
        )

    raw_connect = store._connect  # noqa: SLF001 - deterministic lock harness
    blocker = raw_connect()
    blocker.execute("BEGIN IMMEDIATE")
    begin_attempted = threading.Event()

    class _SignalingConnection:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def execute(self, statement, parameters=()):
            if str(statement).strip().upper() == "BEGIN IMMEDIATE":
                begin_attempted.set()
            return self._connection.execute(statement, parameters)

    monkeypatch.setattr(
        store,
        "_connect",
        lambda: _SignalingConnection(raw_connect()),
    )

    def _run_operation():
        if operation == "acquire":
            return store.acquire_leader_lease("worker-b", lease_ms=10)
        if operation == "renew":
            return store.renew_leader_lease(lease, lease_ms=10)
        if operation == "release":
            return store.release_leader_lease(lease)
        try:
            with store.bind_leader_lease(lease):
                if operation == "upsert":
                    return store.upsert(
                        "lease-clock",
                        "new",
                        {"value": "must-not-commit"},
                        source_cursor=2,
                        freshness_ts_ms=123,
                    )
                if operation == "mark_degraded":
                    return store.mark_degraded(
                        "lease-clock",
                        "current",
                        state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                        detail="must-not-commit",
                    )
                return store.rebuild(
                    "lease-clock",
                    [("replacement", {"value": "must-not-commit"}, 2, "")],
                )
        except QueryProjectionError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_operation)
        assert begin_attempted.wait(timeout=5)
        now[0] = 1_011
        blocker.rollback()
        blocker.close()
        outcome = future.result(timeout=5)

    if operation == "acquire":
        assert outcome is not None
        assert outcome.owner_id == "worker-b"
        assert outcome.fencing_token == lease.fencing_token + 1
    elif operation == "renew":
        assert outcome is None
    elif operation == "release":
        assert outcome is False
    else:
        assert isinstance(outcome, QueryProjectionError)
        assert outcome.reason_code == PROJECTION_LEASE_FENCED
        assert store.get("lease-clock", "new") is None
        current = store.get("lease-clock", "current")
        if operation in {"mark_degraded", "rebuild"}:
            assert current is not None
            assert current.payload == {"value": "last-good"}
            assert current.degraded_state is ProjectionDegradedState.HEALTHY


def test_projection_daemon_skips_jobs_without_cross_process_leader_lease(tmp_path):
    from host_brain.app.observability_runtime.projection_daemon import ProjectionJobDaemon

    now = [1000]
    db_path = tmp_path / "projection-daemon.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-daemon")
    assert migrated.success, migrated
    first_store = QueryProjectionStore(db_path, clock=lambda: now[0])
    second_store = QueryProjectionStore(db_path, clock=lambda: now[0])
    calls: list[str] = []
    daemon = ProjectionJobDaemon(owner_id="process-a")
    daemon.register("job", lambda: calls.append("ran"))

    assert daemon.run_once(
        store=first_store,
        lease_ms=100,
    ) == {"job": "OK"}
    assert daemon.run_once(
        store=second_store,
        owner_id="process-b",
        lease_ms=100,
    ) == {"job": "SKIPPED_NOT_LEADER"}
    assert calls == ["ran"]


def test_projection_worker_caches_refresh_and_preserves_last_good_document(tmp_path):
    now = [1000]
    store = _store(tmp_path, clock=lambda: now[0])
    worker = QueryProjectionWorker(store, clock=lambda: now[0])
    loader = Mock(return_value=ProjectionSource("key", {"value": 1}, 1))

    first = worker.get_or_refresh("kind", "key", loader, max_age_ms=100)
    second = worker.get_or_refresh("kind", "key", loader, max_age_ms=100)
    assert first.payload == second.payload
    assert loader.call_count == 1

    now[0] = 1200
    loader.side_effect = RuntimeError("source down")
    stale = worker.get_or_refresh("kind", "key", loader, max_age_ms=100)
    assert stale.payload == {"value": 1}
    assert stale.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE


def test_task_session_and_trace_gets_never_call_source_loader(tmp_path, monkeypatch):
    store = _store(tmp_path)
    source_loader = Mock(side_effect=AssertionError("GET queried source"))

    task_reader = ProjectionOperatorReadModelService(store=store, max_age_ms=5000)
    task_result = task_reader.list_executions(limit=10)
    assert task_result["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
    source_loader.assert_not_called()

    ledger = Mock()
    session_reader = SessionQueryService(ledger, projection_store=store)
    session_result = session_reader.query()
    assert session_result["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
    ledger.fetch_by_time_range.assert_not_called()

    monkeypatch.setattr(
        "host_brain.app.obs_routes_trace._get_trace_builders",
        source_loader,
    )
    request = _obs_request(store)
    trace_result = asyncio.run(get_trace("req-missing", request))
    assert trace_result.status == "DEGRADED"
    assert trace_result.projection["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
    source_loader.assert_not_called()


def test_reconcile_repairs_changed_payload_without_bumping_unchanged_document(tmp_path):
    store = _store(tmp_path)
    first = {"value": 1}
    store.upsert("trace", "req-1", first, source_cursor=1, source_digest=digest_json(first))
    result = store.reconcile(
        "trace",
        [
            ("req-1", first, 1, digest_json(first)),
            ("req-2", {"value": 2}, 2, digest_json({"value": 2})),
        ],
    )
    assert result["unchanged_count"] == 1
    assert result["repaired_count"] == 1
    assert store.get("trace", "req-2").payload == {"value": 2}


def test_stream_feed_tribe_sse_and_session_export_never_call_sources(tmp_path, monkeypatch):
    from host_brain.app import obs_routes_sessions as sessions
    from host_brain.app import obs_routes_trace as trace

    store = _store(tmp_path)
    request = _obs_request(store)
    forbidden = Mock(side_effect=AssertionError("GET/SSE touched a source"))
    monkeypatch.setattr(trace, "_get_trace_builders", forbidden)
    monkeypatch.setattr(trace, "_get_ledger", forbidden)
    monkeypatch.setattr(sessions, "_get_ledger", forbidden)

    stream = trace.get_trace_stream_events("req-empty", request)
    assert stream.events == []
    feed = trace.get_trace_live_feed_snapshot(
        request=request,
        limit=6,
        window_minutes=60,
        active_within_ms=60_000,
        keyword=None,
    )
    assert feed.items == []
    tribe = trace.get_tribe_events(
        request=request,
        limit=20,
        event_type=None,
        source_agent=None,
        target_agent=None,
        request_id=None,
        session_id=None,
        conversation_id=None,
    )
    assert tribe.items == []

    response = asyncio.run(
        trace.stream_trace_live_feed(
            request,
            limit=6,
            window_minutes=60,
            active_within_ms=60_000,
            interval_ms=250,
            keyword=None,
            once=True,
        )
    )

    async def consume():
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(consume())
    assert chunks and "snapshot" in str(chunks[0])

    export = sessions.export_sessions_csv(
        request,
        status=None,
        window_minutes=60,
        keyword=None,
        max_rows=100,
    )
    assert export.media_type.startswith("text/csv")
    forbidden.assert_not_called()


def test_projection_get_handlers_have_no_source_builder_calls():
    from host_brain.app import obs_routes_sessions as sessions
    from host_brain.app import obs_routes_trace as trace

    for handler in (
        trace.get_trace,
        trace.get_trace_stream_events,
        trace.get_trace_live_feed_snapshot,
        trace.stream_trace_live_feed,
        trace.get_tribe_events,
    ):
        source = inspect.getsource(handler)
        assert "_get_ledger(" not in source
        assert "_get_trace_builders(" not in source
        assert "get_evidence_plane(" not in source
        assert "get_tribe_event_bus(" not in source
    for handler in (sessions.get_sessions, sessions.export_sessions_csv):
        assert "_get_ledger(" not in inspect.getsource(handler)


def test_embodiment_receipt_observer_projects_secret_free_device_status(tmp_path, monkeypatch):
    from host_brain.composition import process_services as dependencies
    from host_brain.channels import embodiment
    from host_brain.app.observability_runtime import projection_runtime
    from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt

    store = _store(tmp_path)
    monkeypatch.setattr(dependencies, "get_query_projection_store", lambda: store)
    projection_runtime.stop_embodiment_receipt_projection()
    assert projection_runtime.configure_embodiment_receipt_projection() is True
    try:
        command = embodiment.build_host_embodiment_command(
            kind="avatar",
            action="status",
            payload={"api_key": "RAW_DEVICE_SECRET"},
            request_id="req-device-projection",
        )
        receipt = EmbodimentReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            accepted=True,
            status="COMPLETED",
            reason_code=None,
            detail="avatar ready",
            received_at_ms=1234,
            event={"device": "avatar", "token": "RAW_RECEIPT_SECRET"},
        )
        embodiment._publish_embodiment_receipt(command, receipt)
        document = store.get("device_status", "avatar")
        assert document is not None
        assert document.payload["success"] is True
        assert document.payload["event"] == {"device": "avatar"}
        raw = Path(store.db_path).read_bytes()
        assert b"RAW_DEVICE_SECRET" not in raw
        assert b"RAW_RECEIPT_SECRET" not in raw
    finally:
        projection_runtime.stop_embodiment_receipt_projection()
