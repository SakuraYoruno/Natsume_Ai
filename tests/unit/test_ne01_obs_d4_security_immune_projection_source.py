from __future__ import annotations

import json
import time

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.domain_state_projection import (
    SECURITY_IMMUNE_KEY,
    SECURITY_IMMUNE_KIND,
)
from host_brain.app.obs_application import build_default_obs_application_ports


def _job(ports):  # noqa: ANN001
    return next(
        job for job in ports.projection_jobs if job.name == "security-immune"
    )


def test_security_immune_projection_reads_durable_evidence_without_host_runtime(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    evidence = tmp_path / "immune-evidence"
    evidence.mkdir()
    generated_ts_ms = int(time.time() * 1000)
    (evidence / "state.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "mode": "observe",
                "last_run_ts_ms": generated_ts_ms - 1,
                "run_count": 1,
                "generated_ts_ms": generated_ts_ms,
                "assets": [],
                "incidents": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SECURITY_IMMUNE_STATE_DIR", str(evidence))
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "security-source.sqlite3",
    )
    try:
        result = _job(ports).callback()
        document = ports.query_projection_store.get(
            SECURITY_IMMUNE_KIND,
            SECURITY_IMMUNE_KEY,
        )

        assert result["unavailable_document_count"] == 0
        assert document is not None
        assert document.degraded_state is ProjectionDegradedState.HEALTHY
        assert document.payload["enabled"] is True
        assert document.payload["recent_events"] == []
        assert document.freshness_ts_ms == generated_ts_ms
        assert document.payload["evidence_source"]["freshness_ts_ms"] == (
            generated_ts_ms
        )
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_security_immune_projection_persists_stable_typed_unavailable_document(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    from host_brain.app import obs_projection_jobs

    observed_at = [1_000]
    monkeypatch.setattr(obs_projection_jobs, "_now_ms", lambda: observed_at[0])
    monkeypatch.setenv(
        "SECURITY_IMMUNE_STATE_DIR",
        str(tmp_path / "missing-evidence"),
    )
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "security-missing.sqlite3",
    )
    try:
        first = _job(ports).callback()
        before = ports.query_projection_store.get(
            SECURITY_IMMUNE_KIND,
            SECURITY_IMMUNE_KEY,
        )
        observed_at[0] = 2_000
        second = _job(ports).callback()
        after = ports.query_projection_store.get(
            SECURITY_IMMUNE_KIND,
            SECURITY_IMMUNE_KEY,
        )

        assert first["changed_document_count"] == 1
        assert second["changed_document_count"] == 0
        assert before is not None and after is not None
        assert before.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert before.payload["reason_code"] == (
            "OBS_SECURITY_IMMUNE_EVIDENCE_MISSING"
        )
        assert before.payload["source_scope"] == "security-immune-evidence-store"
        assert before.source_cursor == after.source_cursor
        assert before.projection_version == after.projection_version
        assert before.freshness_ts_ms == 1_000
        assert after.freshness_ts_ms == 2_000
        assert after.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_security_immune_projection_rejects_expired_evidence_despite_new_file(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    evidence = tmp_path / "stale-immune-evidence"
    evidence.mkdir()
    (evidence / "state.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "mode": "observe",
                "last_run_ts_ms": 99,
                "run_count": 1,
                "generated_ts_ms": 100,
                "assets": [],
                "incidents": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SECURITY_IMMUNE_STATE_DIR", str(evidence))
    monkeypatch.setenv("OBS_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS", "1")
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "security-stale.sqlite3",
    )
    try:
        result = _job(ports).callback()
        document = ports.query_projection_store.get(
            SECURITY_IMMUNE_KIND,
            SECURITY_IMMUNE_KEY,
        )

        assert result["unavailable_document_count"] == 1
        assert document is not None
        assert document.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert document.payload["reason_code"] == "OBS_SECURITY_IMMUNE_EVIDENCE_STALE"
        assert document.payload["source_scope"] == "security-immune-evidence-store"
        assert document.payload["available"] is False
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()
