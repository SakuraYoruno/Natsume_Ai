from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.life_body_source import (
    LIFE_BODY_ROOT_INVALID,
    LIFE_BODY_ROOT_NOT_READY,
    LIFE_BODY_ROOT_STALE,
)
from host_brain.app.observability_runtime.health_device_projection import (
    ORGAN_TRIBE_KEY,
    ORGAN_TRIBE_KIND,
)
from host_brain.app.observability_runtime.process_state_projection import (
    DIGITAL_LIFE_OVERVIEW_KIND,
    INSIGHTS_SUMMARY_KIND,
    LIFE_BODY_KIND,
    RUNTIME_MAX_AGE_MS,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    window_key,
)
from host_brain.app.observability_runtime.projection_runtime import (
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_KIND,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.obs_fastapi_app import create_obs_app
from host_brain.app.obs_application import (
    build_default_obs_application_ports,
)
from host_brain.app.obs_projection_jobs import DURABLE_READ_PARTIAL
from host_brain.observability.projection_store import QueryProjectionStore


def _upsert_root(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    payload: dict,
    *,
    cursor: int,
    freshness_ts_ms: int,
) -> None:
    store.upsert(
        kind,
        key,
        payload,
        source_cursor=cursor,
        source_ref="test://d7b/root",
        freshness_ts_ms=freshness_ts_ms,
    )


def _seed_roots(
    store: QueryProjectionStore,
    *,
    marker: str,
    now_ms: int,
    cursor_base: int = 100,
    omit: str | None = None,
    runtime_timestamp: int | None = None,
    runtime_freshness_ts_ms: int | None = None,
) -> None:
    roots = {
        "runtime-snapshot": (
            RUNTIME_SNAPSHOT_KIND,
            RUNTIME_SNAPSHOT_KEY,
            {
                "schema_version": "Natsume.digital_life.runtime_snapshot.v2",
                "persona_user_id": marker,
                "sys_mode": "normal",
                "slot_status": {"hard_state": "live"},
                "private_body": f"PRIVATE_RUNTIME_{marker}",
                "timestamp": (
                    now_ms
                    if runtime_timestamp is None
                    else runtime_timestamp
                ),
            },
            runtime_freshness_ts_ms or now_ms,
        ),
        "digital-life-overview": (
            DIGITAL_LIFE_OVERVIEW_KIND,
            window_key(360),
            {
                "window_minutes": 360,
                "timestamp": now_ms,
                "memoryData": {
                    "marker": marker,
                    "subsystems": [{"name": "episodic", "total": 1}],
                },
                "servicesData": {"total": 1, "down": 0},
                "subsystemsData": {"subsystems": [{"name": "life", "total": 1}]},
            },
            now_ms,
        ),
        "insights-summary": (
            INSIGHTS_SUMMARY_KIND,
            window_key(60),
            {
                "window_minutes": 60,
                "timestamp": now_ms,
                "promptStats": {"injection_detected_count": 0},
            },
            now_ms,
        ),
        "trace-feed": (
            TRACE_FEED_KIND,
            TRACE_FEED_INDEX_KEY,
            {
                "type": "snapshot",
                "items": [
                    {
                        "session": {
                            "request_id": f"req-{marker}",
                            "session_id": f"session-{marker}",
                            "status": "OK",
                            "input_preview": f"PRIVATE_TRACE_{marker}",
                            "ts_ms": now_ms,
                        },
                        "trace": {
                            "request_id": f"req-{marker}",
                            "status": "OK",
                            "node_count": 1,
                            "ts_ms": now_ms,
                        },
                    }
                ],
                "count": 1,
                "timestamp": now_ms,
            },
            now_ms,
        ),
        "organ-tribe": (
            ORGAN_TRIBE_KIND,
            ORGAN_TRIBE_KEY,
            {
                "total_agents": 23,
                "active_agents": 0,
                "continuous_system_active": True,
                "timestamp": now_ms,
                "host_scheduler": {
                    "clock_active": True,
                    "clock": {
                        "active": True,
                        "last_tick_ms": now_ms,
                        "next_retry_at_ms": now_ms + 60_000,
                    },
                },
            },
            now_ms,
        ),
    }
    for offset, (name, (kind, key, payload, freshness)) in enumerate(
        roots.items(),
        start=1,
    ):
        if name == omit:
            continue
        _upsert_root(
            store,
            kind,
            key,
            payload,
            cursor=cursor_base + offset,
            freshness_ts_ms=freshness,
        )


def _life_body_job(ports):  # noqa: ANN001
    jobs = {job.name: job for job in ports.projection_jobs}
    assert jobs["life-body"].source_mode == DURABLE_READ_PARTIAL
    return jobs["life-body"]


def test_two_obs_apps_cold_read_only_their_own_sqlite_roots(
    tmp_path: Path,
) -> None:
    now_ms = int(time.time() * 1000)
    first = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-first.sqlite3",
    )
    second = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-second.sqlite3",
    )
    try:
        _seed_roots(first.query_projection_store, marker="first", now_ms=now_ms)
        _seed_roots(second.query_projection_store, marker="second", now_ms=now_ms)

        first_job = _life_body_job(first)
        assert first_job.callback()["unavailable_document_count"] == 0
        assert first_job.callback()["unavailable_document_count"] == 0
        assert _life_body_job(second).callback()["unavailable_document_count"] == 0

        first_body = first.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        second_body = second.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert first_body is not None and second_body is not None
        assert first_body.degraded_state is ProjectionDegradedState.HEALTHY
        assert first_body.payload["runtimeSnapshot"]["persona_user_id"] == "first"
        assert first_body.payload["memoryData"]["marker"] == "first"
        assert first_body.payload["organTribeData"]["total_agents"] == 23
        assert second_body.payload["runtimeSnapshot"]["persona_user_id"] == "second"
        assert second_body.payload["memoryData"]["marker"] == "second"
        assert "second" not in json.dumps(first_body.payload, ensure_ascii=False)
        assert "first" not in json.dumps(second_body.payload, ensure_ascii=False)
    finally:
        first.close_owned_resources()
        second.close_owned_resources()


def test_real_obs_app_lifecycle_serves_cold_life_body_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    operator_token = "d7b-life-body-route"
    monkeypatch.setenv(
        "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS",
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(
                        operator_token.encode("utf-8")
                    ).hexdigest(),
                    "principal_id": "tests.d7b.life-body",
                    "scopes": ["obs:operator:read"],
                }
            ]
        ),
    )
    now_ms = int(time.time() * 1000)
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-route.sqlite3",
    )
    _seed_roots(ports.query_projection_store, marker="route", now_ms=now_ms)
    job = _life_body_job(ports)
    assert job.callback()["unavailable_document_count"] == 0
    route_ports = replace(ports, projection_jobs=())
    app = create_obs_app(ports_factory=lambda: route_ports)

    with TestClient(app) as client:
        response = client.get(
            "/obs/life-body?window_minutes=360",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["runtimeSnapshot"]["persona_user_id"] == "route"
        assert payload["memoryData"]["marker"] == "route"
        assert payload["meta"]["projection"]["degraded_state"] == "HEALTHY"


def test_life_body_cold_reads_after_host_ledger_is_closed_without_content_leak(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "d7b-host-closed.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d7b-host-closed",
    )
    assert migrated.success, migrated
    host_ledger = SQLiteLedger(
        db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    now_ms = int(time.time() * 1000)
    private_marker = "PRIVATE_LEDGER_BODY_D7B"
    host_store = QueryProjectionStore(db_path)
    _seed_roots(host_store, marker="closed-host", now_ms=now_ms)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_ops_log(
                request_id, op_kind, status, detail, ts_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("req-closed-host", "write", "OK", private_marker, now_ms),
        )
        conn.commit()
    host_ledger.close()

    ports = build_default_obs_application_ports(canonical_db_path=db_path)
    try:
        result = _life_body_job(ports).callback()
        assert result["unavailable_document_count"] == 0
        document = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert document is not None
        assert document.degraded_state is ProjectionDegradedState.HEALTHY
        assert document.payload["derived"]["memoryLineageCount"] == 1
        serialized = json.dumps(document.payload, ensure_ascii=False)
        assert private_marker not in serialized
        assert "PRIVATE_RUNTIME_closed-host" not in serialized
        assert "PRIVATE_TRACE_closed-host" not in serialized

        with sqlite3.connect(db_path) as conn:
            life_body_rows = conn.execute(
                """
                SELECT payload_json
                FROM operator_query_projection_document
                WHERE projection_kind = ? AND document_key = ?
                UNION ALL
                SELECT source_payload_json
                FROM operator_query_projection_journal
                WHERE projection_kind = ? AND document_key = ?
                """,
                (
                    LIFE_BODY_KIND,
                    window_key(360),
                    LIFE_BODY_KIND,
                    window_key(360),
                ),
            ).fetchall()
        assert private_marker not in "\n".join(str(row[0]) for row in life_body_rows)
    finally:
        ports.close_owned_resources()


def test_life_body_missing_invalid_and_stale_roots_fail_closed(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-fail-closed.sqlite3",
    )
    now_ms = int(time.time() * 1000)
    job = _life_body_job(ports)
    try:
        _seed_roots(
            ports.query_projection_store,
            marker="missing",
            now_ms=now_ms,
            omit="runtime-snapshot",
            cursor_base=100,
        )
        assert job.callback()["unavailable_document_count"] == 1
        missing = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert missing is not None
        assert missing.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert missing.payload["reason_code"] == LIFE_BODY_ROOT_NOT_READY

        _seed_roots(
            ports.query_projection_store,
            marker="invalid",
            now_ms=now_ms,
            runtime_timestamp=0,
            cursor_base=200,
        )
        assert job.callback()["unavailable_document_count"] == 1
        invalid = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert invalid is not None
        assert invalid.payload["reason_code"] == LIFE_BODY_ROOT_INVALID

        _seed_roots(
            ports.query_projection_store,
            marker="stale",
            now_ms=now_ms,
            runtime_freshness_ts_ms=now_ms - RUNTIME_MAX_AGE_MS - 1,
            cursor_base=300,
        )
        assert job.callback()["unavailable_document_count"] == 1
        stale = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert stale is not None
        assert stale.payload["reason_code"] == LIFE_BODY_ROOT_STALE
    finally:
        ports.close_owned_resources()


def test_life_body_keeps_runtime_live_when_optional_trace_feed_is_missing(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-optional-trace.sqlite3",
    )
    now_ms = int(time.time() * 1000)
    try:
        _seed_roots(
            ports.query_projection_store,
            marker="optional-trace",
            now_ms=now_ms,
            omit="trace-feed",
        )

        result = _life_body_job(ports).callback()

        assert result["unavailable_document_count"] == 0
        document = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert document is not None
        assert document.degraded_state is ProjectionDegradedState.HEALTHY
        assert document.payload["runtimeSnapshot"]["persona_user_id"] == (
            "optional-trace"
        )
        assert document.payload["derived"]["activeTraceFeedCount"] == 0
        assert document.payload["meta"]["optionalSourceStatus"]["trace-feed"] == {
            "available": False,
            "reason_code": LIFE_BODY_ROOT_NOT_READY,
        }
        assert document.payload["meta"]["optionalSourceStatus"]["organ-tribe"] == {
            "available": True,
            "reason_code": None,
        }
    finally:
        ports.close_owned_resources()


def test_life_body_runs_before_trace_after_its_required_prerequisites(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-order.sqlite3",
    )
    try:
        names = [job.name for job in ports.projection_jobs]
        life_body_index = names.index("life-body")
        assert life_body_index < names.index("trace")
        for prerequisite in (
            "health-passive",
            "remaining-operator-queries",
            "operator-live-state",
            "organ-tribe",
            "runtime-snapshot",
            "digital-life-overview",
            "insights-summary",
        ):
            assert names.index(prerequisite) < life_body_index
    finally:
        ports.close_owned_resources()


def test_life_body_preserves_public_security_gateway_and_capability_risks(
    tmp_path: Path,
) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "d7b-public-risks.sqlite3",
    )
    now_ms = int(time.time() * 1000)
    try:
        _seed_roots(ports.query_projection_store, marker="risks", now_ms=now_ms)
        insights = ports.query_projection_store.get(
            INSIGHTS_SUMMARY_KIND,
            window_key(60),
        )
        overview = ports.query_projection_store.get(
            DIGITAL_LIFE_OVERVIEW_KIND,
            window_key(360),
        )
        assert insights is not None and overview is not None
        insights_payload = dict(insights.payload)
        insights_payload["promptStats"] = {
            "injection_detected_count": 2,
            "high_risk_count": 1,
        }
        _upsert_root(
            ports.query_projection_store,
            INSIGHTS_SUMMARY_KIND,
            window_key(60),
            insights_payload,
            cursor=500,
            freshness_ts_ms=now_ms,
        )
        overview_payload = dict(overview.payload)
        overview_payload.update(
            {
                "servicesData": {"total": 2, "down": 1, "degraded": 1},
                "humanoidLoopData": {
                    "total_gateway_requests": 10,
                    "closed_loop_rate": 30,
                },
                "humanoidLoopAlertsData": {
                    "status": "critical",
                    "severity": "critical",
                    "alerts": [
                        {
                            "reason_code": "EDGE_RECEIPT_MISSING",
                            "severity": "critical",
                        }
                    ],
                },
                "capabilityGapData": {
                    "failed_total": 1,
                    "forge_runtime_quality": {
                        "summary": {"critical_proposals": 1}
                    },
                },
            }
        )
        _upsert_root(
            ports.query_projection_store,
            DIGITAL_LIFE_OVERVIEW_KIND,
            window_key(360),
            overview_payload,
            cursor=501,
            freshness_ts_ms=now_ms,
        )

        result = _life_body_job(ports).callback()
        assert result["unavailable_document_count"] == 0
        document = ports.query_projection_store.get(
            LIFE_BODY_KIND,
            window_key(360),
        )
        assert document is not None
        reasons = {
            item["reason_code"] for item in document.payload["runtimeAlerts"]
        }
        assert {
            "PROMPT_INJECTION_DETECTED",
            "PROMPT_HIGH_RISK_BUILD",
            "SERVICE_DOWN",
            "EDGE_RECEIPT_MISSING",
            "FORGE_RUNTIME_CRITICAL_PROPOSALS",
        } <= reasons
        immune = next(
            item
            for item in document.payload["model"]["organSystems"]
            if item["id"] == "immune"
        )
        assert immune["status"] == "critical"
        assert document.payload["model"]["lifeState"]["tone"] == "critical"
        assert any(
            item["organId"] == "immune"
            for item in document.payload["model"]["pathologies"]
        )
        assert document.payload["model"]["diagnosticGuidance"]["actions"]
    finally:
        ports.close_owned_resources()


def test_life_body_unchanged_sources_only_refresh_freshness_without_journal_churn(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "d7b-life-body-idempotent.sqlite3"
    ports = build_default_obs_application_ports(canonical_db_path=db_path)
    now_ms = int(time.time() * 1000)
    try:
        _seed_roots(ports.query_projection_store, marker="same", now_ms=now_ms)
        job = _life_body_job(ports)
        first = job.callback()
        assert first["changed_document_count"] == 1
        before = ports.query_projection_store.get(LIFE_BODY_KIND, window_key(360))
        assert before is not None
        with sqlite3.connect(db_path) as conn:
            journal_before = conn.execute(
                """
                SELECT COUNT(*)
                FROM operator_query_projection_journal
                WHERE projection_kind = ? AND document_key = ?
                """,
                (LIFE_BODY_KIND, window_key(360)),
            ).fetchone()[0]

        second = job.callback()
        after = ports.query_projection_store.get(LIFE_BODY_KIND, window_key(360))
        assert second["changed_document_count"] == 0
        assert after is not None
        assert after.projection_version == before.projection_version
        assert after.projection_cursor == before.projection_cursor
        assert after.source_cursor == before.source_cursor
        assert after.source_digest == before.source_digest
        assert after.freshness_ts_ms >= before.freshness_ts_ms
        with sqlite3.connect(db_path) as conn:
            journal_after = conn.execute(
                """
                SELECT COUNT(*)
                FROM operator_query_projection_journal
                WHERE projection_kind = ? AND document_key = ?
                """,
                (LIFE_BODY_KIND, window_key(360)),
            ).fetchone()[0]
        assert journal_after == journal_before
    finally:
        ports.close_owned_resources()
