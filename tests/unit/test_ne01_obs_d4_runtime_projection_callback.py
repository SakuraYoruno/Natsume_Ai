from __future__ import annotations

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.process_state_projection import (
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
)
from host_brain.app.observability_runtime.runtime_snapshot_source import (
    RUNTIME_LIFE_SNAPSHOT_NOT_FOUND,
    RuntimeSnapshotSourceUnavailable,
)
from host_brain.app.obs_application import build_default_obs_application_ports
from host_brain.app.obs_projection_jobs import _runtime_snapshot_callback


class _PartialReader:
    def __call__(self):
        return (
            {
                "persona_user_id": "default",
                "current_topic": "",
                "sys_mode": "unknown",
                "hard_state": {"life_version": 2},
                "runtime_summary": {"status": "PARTIAL_DEGRADED"},
                "slot_status": {"sys_mode": "source_unavailable"},
                "degraded": True,
                "degraded_reasons": ["OBS_RUNTIME_SYS_MODE_STATUS_NOT_READY"],
                "timestamp": 123,
            },
            123,
        )


class _MissingReader:
    def __call__(self):
        raise RuntimeSnapshotSourceUnavailable(RUNTIME_LIFE_SNAPSHOT_NOT_FOUND)


def test_runtime_projection_keeps_usable_partial_payload_available(tmp_path) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "runtime-partial.sqlite3",
    )
    try:
        result = _runtime_snapshot_callback(
            ports.query_projection_store,
            _PartialReader(),  # type: ignore[arg-type]
        )()
        document = ports.query_projection_store.get(
            RUNTIME_SNAPSHOT_KIND,
            RUNTIME_SNAPSHOT_KEY,
        )

        assert result["partial_document_count"] == 1
        assert document is not None
        assert document.degraded_state is ProjectionDegradedState.HEALTHY
        assert document.payload["degraded"] is True
        assert document.payload["runtime_summary"]["status"] == (
            "PARTIAL_DEGRADED"
        )
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()


def test_runtime_projection_missing_life_root_is_stable_unavailable(tmp_path) -> None:
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "runtime-missing.sqlite3",
    )
    try:
        callback = _runtime_snapshot_callback(
            ports.query_projection_store,
            _MissingReader(),  # type: ignore[arg-type]
        )
        first = callback()
        before = ports.query_projection_store.get(
            RUNTIME_SNAPSHOT_KIND,
            RUNTIME_SNAPSHOT_KEY,
        )
        second = callback()
        after = ports.query_projection_store.get(
            RUNTIME_SNAPSHOT_KIND,
            RUNTIME_SNAPSHOT_KEY,
        )

        assert first["changed_document_count"] == 1
        assert second["changed_document_count"] == 0
        assert before is not None and after is not None
        assert before.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
        assert before.payload["reason_code"] == RUNTIME_LIFE_SNAPSHOT_NOT_FOUND
        assert before.payload["source_scope"] == (
            "canonical-life-and-operator-projections"
        )
        assert before.source_cursor == after.source_cursor
    finally:
        assert ports.close_owned_resources is not None
        ports.close_owned_resources()
