from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from natsume_contracts.operator import (
    HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
    HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
    HOST_MODE_SOURCE_SCHEMA_VERSION,
    HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
    HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
    HostModeSourceSnapshot,
)
from host_brain.app.observability_runtime import host_read_transport as transport_module
from host_brain.app.observability_runtime.host_projection_sources import (
    AuthenticatedHostProjectionSources,
)
from host_brain.app.observability_runtime.host_read_transport import HostReadTransport
from host_brain.app.observability_runtime.operator_live_sources import (
    OperatorSourceUnavailable,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


NOW = 1_750_000_000_000


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.raw if size < 0 else self.raw[:size]


def _payloads(marker: str = "one") -> dict[str, dict]:
    return {
        "/internal/obs-sources/mode": {
            "schema_version": HOST_MODE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 10,
            "source_instance_id": "host-source-test",
            "source_revision": 1,
            "status": {
                "sys_mode": marker,
                "ts_ms": NOW - 10,
            },
            "history": [{"event_id": marker, "ts_ms": NOW - 11}],
        },
        "/internal/obs-sources/capability-forge": {
            "schema_version": HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 20,
            "source_instance_id": "host-source-test",
            "source_revision": 1,
            "gap": {"marker": marker},
            "proposals": [{"proposal_id": marker}],
            "runtime_stats": {"marker": marker},
            "runtime_tuning": {},
            "runtime_quality": {},
            "semantic_recall": {
                "status": "MOCK_READY",
                "timestamp": NOW - 20,
            },
            "governance": {
                "schema_version": "Natsume.governance.snapshot.v1",
                "generated_at_ms": NOW - 20,
            },
        },
        "/internal/obs-sources/runtime": {
            "schema_version": HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 30,
            "source_instance_id": "host-source-test",
            "source_revision": 1,
            "organ_tribe": {
                "agents": [],
                "active_agents": 0,
                "marker": marker,
            },
            "scheduler": {"available": True, "marker": marker},
        },
        "/internal/obs-sources/semantic-recall": {
            "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 21,
            "source_instance_id": "host-source-test",
            "source_revision": 1,
            "stats": {
                "status": "MOCK_READY",
                "timestamp": NOW - 21,
            },
        },
        "/internal/obs-sources/governance": {
            "schema_version": HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 22,
            "source_instance_id": "host-source-test",
            "source_revision": 1,
            "governance": {
                "schema_version": "Natsume.governance.snapshot.v1",
                "generated_at_ms": NOW - 22,
            },
        },
    }


def _sources(
    monkeypatch,
    *,
    marker: str = "one",
    cache_ttl_ms: int = 1_000,
    lineage_store: QueryProjectionStore | None = None,
):
    payloads = _payloads(marker)
    calls: list[str] = []

    def open_url(request, *, timeout):
        del timeout
        path = request.full_url.removeprefix("https://host-runtime:18080")
        calls.append(path)
        return _Response(payloads[path])

    monkeypatch.setattr(transport_module, "_open_no_redirect", open_url)
    return (
        AuthenticatedHostProjectionSources(
            HostReadTransport(
                base_url="https://host-runtime:18080",
                token="dedicated-token",
            ),
            cache_ttl_ms=cache_ttl_ms,
            lineage_store=lineage_store,
            clock_ms=lambda: NOW,
        ),
        calls,
        payloads,
    )


def test_mode_status_and_history_share_one_app_owned_contract_read(monkeypatch) -> None:
    sources, calls, _payloads_by_path = _sources(monkeypatch)

    status = sources.mode_status()
    assert status.payload["sys_mode"] == "one"
    assert status.evidence_ts_ms == NOW - 10
    history = sources.mode_history()

    assert history.payload["items"] == [
        {"event_id": "one", "ts_ms": NOW - 11}
    ]
    assert history.payload["timestamp"] == NOW
    assert history.evidence_ts_ms == NOW - 10
    assert calls == ["/internal/obs-sources/mode"]


def test_capability_and_runtime_use_remote_evidence_time(monkeypatch) -> None:
    sources, calls, _payloads_by_path = _sources(monkeypatch)

    capability, capability_evidence = sources.capability_forge()
    organ, organ_evidence = sources.organ_tribe()

    assert capability["gap"] == {"marker": "one"}
    assert capability["timestamp"] == NOW
    assert capability_evidence == NOW - 20
    assert organ["marker"] == "one"
    assert organ["host_scheduler"] == {"available": True, "marker": "one"}
    assert organ_evidence == NOW - 30
    assert calls == [
        "/internal/obs-sources/capability-forge",
        "/internal/obs-sources/runtime",
    ]


def test_semantic_and_governance_use_independent_child_payloads(monkeypatch) -> None:
    sources, calls, _payloads_by_path = _sources(monkeypatch)

    semantic, semantic_evidence = sources.semantic_recall()
    governance, governance_evidence = sources.governance()

    assert semantic == {"status": "MOCK_READY", "timestamp": NOW - 21}
    assert governance == {
        "schema_version": "Natsume.governance.snapshot.v1",
        "generated_at_ms": NOW - 22,
    }
    assert semantic_evidence == NOW - 21
    assert governance_evidence == NOW - 22
    assert calls == [
        "/internal/obs-sources/semantic-recall",
        "/internal/obs-sources/governance",
    ]


def test_semantic_unavailability_does_not_block_governance(monkeypatch) -> None:
    sources, calls, payloads = _sources(monkeypatch)
    payloads["/internal/obs-sources/semantic-recall"]["stats"] = {
        "available": False,
        "reason_code": "HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY",
        "status": "SOURCE_UNAVAILABLE",
        "timestamp": NOW - 21,
    }

    with pytest.raises(OperatorSourceUnavailable) as unavailable:
        sources.semantic_recall()
    governance, governance_evidence = sources.governance()

    assert unavailable.value.reason_code == "HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY"
    assert governance["schema_version"] == "Natsume.governance.snapshot.v1"
    assert governance_evidence == NOW - 22
    assert calls == [
        "/internal/obs-sources/semantic-recall",
        "/internal/obs-sources/governance",
    ]


def test_contract_violation_is_typed_without_reflecting_payload(monkeypatch) -> None:
    sources, _calls, payloads = _sources(monkeypatch)
    payloads["/internal/obs-sources/runtime"] = {
        "schema_version": "private-invalid-schema",
        "secret": "must-not-leak",
    }

    with pytest.raises(OperatorSourceUnavailable) as raised:
        sources.organ_tribe()

    assert raised.value.reason_code == "OBS_HOST_RUNTIME_SOURCE_CONTRACT_INVALID"
    assert "must-not-leak" not in str(raised.value)
    assert "private-invalid-schema" not in str(raised.value)


def test_invalid_dedicated_transport_configuration_fails_before_io(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        transport_module,
        "_open_no_redirect",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    sources = AuthenticatedHostProjectionSources(
        HostReadTransport(
            base_url="https://host-runtime:18080",
            token="",
        ),
        clock_ms=lambda: NOW,
    )

    with pytest.raises(OperatorSourceUnavailable) as raised:
        sources.mode_status()

    assert raised.value.reason_code == "OBS_HOST_READ_CONFIG"
    assert calls == []


@pytest.mark.parametrize(
    ("path", "reader", "reason_code"),
    (
        (
            "/internal/obs-sources/mode",
            "mode_status",
            "OBS_HOST_MODE_EVIDENCE_NOT_OBSERVED",
        ),
        (
            "/internal/obs-sources/capability-forge",
            "capability_forge",
            "OBS_HOST_CAPABILITY_EVIDENCE_NOT_OBSERVED",
        ),
        (
            "/internal/obs-sources/runtime",
            "organ_tribe",
            "OBS_HOST_ORGAN_EVIDENCE_NOT_OBSERVED",
        ),
        (
            "/internal/obs-sources/semantic-recall",
            "semantic_recall",
            "OBS_HOST_SEMANTIC_RECALL_EVIDENCE_NOT_OBSERVED",
        ),
        (
            "/internal/obs-sources/governance",
            "governance",
            "OBS_HOST_GOVERNANCE_EVIDENCE_NOT_OBSERVED",
        ),
    ),
)
def test_zero_evidence_never_materializes_a_healthy_host_projection(
    monkeypatch,
    path: str,
    reader: str,
    reason_code: str,
) -> None:
    sources, _calls, payloads = _sources(monkeypatch)
    payloads[path]["evidence_ts_ms"] = 0

    with pytest.raises(OperatorSourceUnavailable) as raised:
        getattr(sources, reader)()

    assert raised.value.reason_code == reason_code


def test_future_evidence_is_rejected_as_a_contract_violation(monkeypatch) -> None:
    sources, _calls, payloads = _sources(monkeypatch)
    payloads["/internal/obs-sources/runtime"]["evidence_ts_ms"] = NOW + 1

    with pytest.raises(OperatorSourceUnavailable) as raised:
        sources.organ_tribe()

    assert raised.value.reason_code == "OBS_HOST_RUNTIME_SOURCE_CONTRACT_INVALID"


def test_host_self_reported_future_clock_and_stale_evidence_fail_closed(
    monkeypatch,
) -> None:
    sources, _calls, payloads = _sources(monkeypatch, cache_ttl_ms=0)
    mode = payloads["/internal/obs-sources/mode"]
    mode["observed_at_ms"] = NOW + 10_000
    mode["evidence_ts_ms"] = NOW + 10_000
    mode["status"]["ts_ms"] = NOW + 10_000
    mode["history"][0]["ts_ms"] = NOW + 9_999

    with pytest.raises(OperatorSourceUnavailable) as future:
        sources.mode_status()

    assert future.value.reason_code == "OBS_HOST_SOURCE_CLOCK_AHEAD"

    mode["observed_at_ms"] = NOW
    mode["evidence_ts_ms"] = NOW - 120_001
    mode["status"]["ts_ms"] = NOW - 120_001
    mode["history"][0]["ts_ms"] = NOW - 120_002
    mode["source_revision"] = 2

    with pytest.raises(OperatorSourceUnavailable) as stale:
        sources.mode_status()

    assert stale.value.reason_code == "OBS_HOST_SOURCE_EVIDENCE_STALE"


def test_nested_future_timestamp_is_rejected_by_the_versioned_contract(
    monkeypatch,
) -> None:
    sources, _calls, payloads = _sources(monkeypatch)
    payloads["/internal/obs-sources/mode"]["status"]["ts_ms"] = NOW + 1

    with pytest.raises(OperatorSourceUnavailable) as raised:
        sources.mode_status()

    assert raised.value.reason_code == "OBS_HOST_MODE_SOURCE_CONTRACT_INVALID"


def test_obs_reapplies_public_serialization_before_contract_materialization(
    monkeypatch,
) -> None:
    sources, _calls, payloads = _sources(monkeypatch)
    capability_payload = payloads["/internal/obs-sources/capability-forge"]
    capability_payload["gap"]["private_profile"] = "private home address"
    capability_payload["proposals"][0]["approval_token"] = "secret-token"

    capability, _evidence = sources.capability_forge()

    assert "private home address" not in repr(capability)
    assert "secret-token" not in repr(capability)
    assert "private_profile" not in capability["gap"]
    assert "approval_token" not in capability["proposals"][0]


def test_durable_lineage_rejects_revision_rollback_across_reader_restart(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = tmp_path / "host-source-lineage.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-host-source-lineage",
        ts_ms=NOW,
    )
    assert migrated.success is True
    store = QueryProjectionStore(
        db_path,
        clock=lambda: NOW,
        journal_checkpoint_interval_ms=3_600_000,
    )

    first, _calls, first_payloads = _sources(
        monkeypatch,
        cache_ttl_ms=0,
        lineage_store=store,
    )
    first_payloads["/internal/obs-sources/capability-forge"][
        "source_revision"
    ] = 5
    first.capability_forge()
    first_payloads["/internal/obs-sources/capability-forge"][
        "source_revision"
    ] = 6
    first.capability_forge()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operator_query_projection_journal"
        ).fetchone()[0] == 1

    restarted, _calls, restarted_payloads = _sources(
        monkeypatch,
        cache_ttl_ms=0,
        lineage_store=store,
    )
    restarted_payloads["/internal/obs-sources/capability-forge"][
        "source_revision"
    ] = 4

    with pytest.raises(OperatorSourceUnavailable) as raised:
        restarted.capability_forge()

    assert raised.value.reason_code == "OBS_HOST_SOURCE_REVISION_ROLLBACK"


def test_same_instance_evidence_regression_and_retired_epoch_replay_are_rejected(
    monkeypatch,
) -> None:
    sources, _calls, payloads = _sources(monkeypatch, cache_ttl_ms=0)
    capability = payloads["/internal/obs-sources/capability-forge"]
    sources.capability_forge()

    capability["source_revision"] = 2
    capability["evidence_ts_ms"] = NOW - 21
    with pytest.raises(OperatorSourceUnavailable) as regression:
        sources.capability_forge()
    assert regression.value.reason_code == "OBS_HOST_SOURCE_EVIDENCE_ROLLBACK"

    capability["source_instance_id"] = "host-source-second"
    capability["source_revision"] = 1
    capability["evidence_ts_ms"] = NOW - 19
    sources.capability_forge()

    capability["source_instance_id"] = "host-source-test"
    capability["source_revision"] = 3
    capability["evidence_ts_ms"] = NOW - 18
    with pytest.raises(OperatorSourceUnavailable) as replay:
        sources.capability_forge()
    assert replay.value.reason_code == "OBS_HOST_SOURCE_INSTANCE_REPLAY"


def _lineage_mode_snapshot(
    *,
    source_instance_id: str,
    source_revision: int,
    evidence_ts_ms: int,
) -> HostModeSourceSnapshot:
    return HostModeSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_MODE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": evidence_ts_ms,
            "source_instance_id": source_instance_id,
            "source_revision": source_revision,
            "status": {"sys_mode": "deep-focus", "ts_ms": evidence_ts_ms},
            "history": [],
        }
    )


def test_concurrent_lineage_cas_never_commits_a_lower_revision_last(
    tmp_path,
) -> None:
    class _BarrierStore(QueryProjectionStore):
        def __init__(self, db_path) -> None:
            super().__init__(db_path, clock=lambda: NOW)
            self._barrier = threading.Barrier(2)
            self._gate_lock = threading.Lock()
            self._gated_calls = 0
            self.gate_enabled = False

        def compare_and_upsert(self, *args, **kwargs):
            should_wait = False
            with self._gate_lock:
                if self.gate_enabled and self._gated_calls < 2:
                    self._gated_calls += 1
                    should_wait = True
            if should_wait:
                self._barrier.wait(timeout=5)
            return super().compare_and_upsert(*args, **kwargs)

    db_path = tmp_path / "host-source-lineage-race.sqlite3"
    assert SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-host-source-lineage-race",
        ts_ms=NOW,
    ).success
    store = _BarrierStore(db_path)
    transport = HostReadTransport(
        base_url="https://host-runtime:18080",
        token="dedicated-token",
    )
    path = "/internal/obs-sources/mode"
    initial = AuthenticatedHostProjectionSources(
        transport,
        lineage_store=store,
        clock_ms=lambda: NOW,
    )
    initial._accept_lineage(
        path,
        _lineage_mode_snapshot(
            source_instance_id="host-concurrent-epoch",
            source_revision=1,
            evidence_ts_ms=NOW - 30,
        ),
        local_now_ms=NOW,
    )
    store.gate_enabled = True

    def _accept(revision: int) -> str:
        source = AuthenticatedHostProjectionSources(
            transport,
            lineage_store=store,
            clock_ms=lambda: NOW,
        )
        try:
            source._accept_lineage(
                path,
                _lineage_mode_snapshot(
                    source_instance_id="host-concurrent-epoch",
                    source_revision=revision,
                    evidence_ts_ms=NOW - 30 + revision,
                ),
                local_now_ms=NOW,
            )
        except OperatorSourceUnavailable as exc:
            return exc.reason_code
        return "OK"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(_accept, (2, 3)))

    final = store.get(
        "obs_internal_host_source_lineage",
        path,
    )
    assert final is not None
    assert final.payload["source_revision"] == 3
    assert outcomes[1] == "OK"
    assert outcomes[0] in {"OK", "OBS_HOST_SOURCE_REVISION_ROLLBACK"}


def test_retired_epoch_replay_remains_rejected_after_more_than_64_restarts(
    tmp_path,
) -> None:
    db_path = tmp_path / "host-source-long-lineage.sqlite3"
    assert SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-host-source-long-lineage",
        ts_ms=NOW,
    ).success
    store = QueryProjectionStore(
        db_path,
        clock=lambda: NOW,
        journal_checkpoint_interval_ms=3_600_000,
    )
    transport = HostReadTransport(
        base_url="https://host-runtime:18080",
        token="dedicated-token",
    )
    source = AuthenticatedHostProjectionSources(
        transport,
        lineage_store=store,
        clock_ms=lambda: NOW,
    )
    path = "/internal/obs-sources/mode"
    first_instance = "host-long-epoch-000"
    for index in range(70):
        source._accept_lineage(
            path,
            _lineage_mode_snapshot(
                source_instance_id=f"host-long-epoch-{index:03d}",
                source_revision=1,
                evidence_ts_ms=NOW - 1,
            ),
            local_now_ms=NOW,
        )

    assert len(
        store.journal_text_values(
            "obs_internal_host_source_lineage",
            path,
            "source_instance_id",
        )
    ) == 70
    current = store.get("obs_internal_host_source_lineage", path)
    assert current is not None
    assert current.payload["retired_instance_ids"] == []

    restarted = AuthenticatedHostProjectionSources(
        transport,
        lineage_store=QueryProjectionStore(db_path, clock=lambda: NOW),
        clock_ms=lambda: NOW,
    )
    with pytest.raises(OperatorSourceUnavailable) as replay:
        restarted._accept_lineage(
            path,
            _lineage_mode_snapshot(
                source_instance_id=first_instance,
                source_revision=2,
                evidence_ts_ms=NOW,
            ),
            local_now_ms=NOW,
        )

    assert replay.value.reason_code == "OBS_HOST_SOURCE_INSTANCE_REPLAY"
