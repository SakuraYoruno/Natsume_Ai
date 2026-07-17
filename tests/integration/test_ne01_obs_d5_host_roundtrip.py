from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI

from host_brain.app.host_obs_read_routes import (
    HostObsReadPorts,
    create_host_obs_read_router,
)
from host_brain.app.observability_runtime.host_projection_sources import (
    AuthenticatedHostProjectionSources,
)
from host_brain.app.observability_runtime.host_read_transport import (
    HOST_READ_AUTH,
    HostReadTransport,
)
from natsume_contracts.operator import (
    HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
    HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
    HOST_MODE_SOURCE_SCHEMA_VERSION,
    HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
    HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostModeSourceSnapshot,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)


NOW = 1_750_000_000_000


@contextmanager
def _serve_loopback(app: FastAPI) -> Iterator[str]:
    """Run Uvicorn on an already-reserved loopback port for the test."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    port = int(listener.getsockname()[1])

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="error",
            access_log=False,
        )
    )
    failures: list[BaseException] = []

    def run_server() -> None:
        try:
            server.run(sockets=[listener])
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    thread = threading.Thread(
        target=run_server,
        name="test-host-obs-read-loopback",
        daemon=True,
    )
    thread.start()

    try:
        deadline = time.monotonic() + 5.0
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            if failures:
                raise AssertionError("loopback Host server failed to start") from failures[0]
            raise AssertionError("loopback Host server did not start within five seconds")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=1.0)
        listener.close()

    if thread.is_alive():
        raise AssertionError("loopback Host server did not stop cleanly")
    if failures:
        raise AssertionError("loopback Host server failed") from failures[0]


def test_host_to_obs_authenticated_projection_roundtrip_over_real_loopback() -> None:
    dedicated_token = "d5-dedicated-host-obs-token"
    unrelated_tribe_token = "must-not-authorize-host-obs-read"
    calls = {
        "mode": 0,
        "capability": 0,
        "semantic": 0,
        "governance": 0,
        "runtime": 0,
    }

    def read_mode() -> HostModeSourceSnapshot:
        calls["mode"] += 1
        return HostModeSourceSnapshot.from_mapping(
            {
                "schema_version": HOST_MODE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": NOW,
                "evidence_ts_ms": NOW - 10,
                "source_instance_id": "host-loopback-epoch",
                "source_revision": calls["mode"],
                "status": {"sys_mode": "deep-focus", "ts_ms": NOW - 10},
                "history": [
                    {
                        "event_id": "mode-loopback-1",
                        "sys_mode": "deep-focus",
                        "ts_ms": NOW - 20,
                    }
                ],
            }
        )

    def read_capability() -> HostCapabilityForgeSourceSnapshot:
        calls["capability"] += 1
        return HostCapabilityForgeSourceSnapshot.from_mapping(
            {
                "schema_version": HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": NOW,
                "evidence_ts_ms": NOW - 30,
                "source_instance_id": "host-loopback-epoch",
                "source_revision": calls["capability"],
                "gap": {"marker": "loopback", "gap_count": 1},
                "proposals": [{"proposal_id": "proposal-loopback-1"}],
                "runtime_stats": {"accepted": 3},
                "runtime_tuning": {"enabled": True},
                "runtime_quality": {"score": 0.95},
                "semantic_recall": {
                    "status": "MOCK_READY",
                    "timestamp": NOW - 30,
                },
                "governance": {
                    "schema_version": "Natsume.governance.snapshot.v1",
                    "generated_at_ms": NOW - 30,
                },
            }
        )

    def read_runtime() -> HostRuntimeSourceSnapshot:
        calls["runtime"] += 1
        return HostRuntimeSourceSnapshot.from_mapping(
            {
                "schema_version": HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": NOW,
                "evidence_ts_ms": NOW - 40,
                "source_instance_id": "host-loopback-epoch",
                "source_revision": calls["runtime"],
                "organ_tribe": {
                    "agents": [{"agent_id": "worker-loopback-1", "status": "IDLE"}],
                    "active_agents": 1,
                    "marker": "loopback",
                },
                "scheduler": {"available": True, "pending_jobs": 2},
            }
        )

    def read_semantic() -> HostSemanticRecallSourceSnapshot:
        calls["semantic"] += 1
        return HostSemanticRecallSourceSnapshot.from_mapping(
            {
                "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": NOW,
                "evidence_ts_ms": NOW - 30,
                "source_instance_id": "host-loopback-epoch",
                "source_revision": calls["semantic"],
                "stats": {"status": "MOCK_READY", "timestamp": NOW - 30},
            }
        )

    def read_governance() -> HostGovernanceSourceSnapshot:
        calls["governance"] += 1
        return HostGovernanceSourceSnapshot.from_mapping(
            {
                "schema_version": HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
                "observed_at_ms": NOW,
                "evidence_ts_ms": NOW - 30,
                "source_instance_id": "host-loopback-epoch",
                "source_revision": calls["governance"],
                "governance": {
                    "schema_version": "Natsume.governance.snapshot.v1",
                    "generated_at_ms": NOW - 30,
                    "read_only": True,
                },
            }
        )

    ports = HostObsReadPorts.from_environment(
        mode_source_reader=read_mode,
        capability_forge_source_reader=read_capability,
        semantic_recall_source_reader=read_semantic,
        governance_source_reader=read_governance,
        runtime_source_reader=read_runtime,
        environ={
            "HOST_OBS_READ_TOKEN": dedicated_token,
            "HOST_INTERNAL_TRIBE_TOKEN": unrelated_tribe_token,
        },
    )
    host_app = FastAPI()
    host_app.include_router(create_host_obs_read_router(ports))

    with _serve_loopback(host_app) as base_url:
        rejected = HostReadTransport(
            base_url=base_url,
            token=unrelated_tribe_token,
            timeout_s=2.0,
        ).read_json("/internal/obs-sources/mode")
        assert rejected.ok is False
        assert rejected.reason_code == HOST_READ_AUTH
        assert calls["mode"] == 0

        transport = HostReadTransport.from_environment(
            {
                "HOST_OBS_HOST_API_BASE_URL": base_url,
                "HOST_OBS_READ_TOKEN": dedicated_token,
                "HOST_INTERNAL_TRIBE_TOKEN": unrelated_tribe_token,
            },
            timeout_s=2.0,
        )
        sources = AuthenticatedHostProjectionSources(
            transport,
            cache_ttl_ms=1_000,
            clock_ms=lambda: NOW,
        )

        mode = sources.mode_status()
        mode_history = sources.mode_history()
        capability, capability_evidence_ts = sources.capability_forge()
        semantic, semantic_evidence_ts = sources.semantic_recall()
        governance, governance_evidence_ts = sources.governance()
        organ, organ_evidence_ts = sources.organ_tribe()

    assert mode.payload == {"sys_mode": "deep-focus", "ts_ms": NOW - 10}
    assert mode.evidence_ts_ms == NOW - 10
    assert mode_history.payload == {
        "items": [
            {
                "event_id": "mode-loopback-1",
                "sys_mode": "deep-focus",
                "ts_ms": NOW - 20,
            }
        ],
        "total": 1,
        "timestamp": NOW,
    }
    assert mode_history.evidence_ts_ms == NOW - 10
    assert capability["gap"] == {"marker": "loopback", "gap_count": 1}
    assert capability["proposals"] == [{"proposal_id": "proposal-loopback-1"}]
    assert capability["timestamp"] == NOW
    assert capability_evidence_ts == NOW - 30
    assert semantic == {"status": "MOCK_READY", "timestamp": NOW - 30}
    assert semantic_evidence_ts == NOW - 30
    assert governance["schema_version"] == "Natsume.governance.snapshot.v1"
    assert governance["read_only"] is True
    assert governance_evidence_ts == NOW - 30
    assert organ["marker"] == "loopback"
    assert organ["host_scheduler"] == {"available": True, "pending_jobs": 2}
    assert organ["timestamp"] == NOW
    assert organ_evidence_ts == NOW - 40
    assert calls == {
        "mode": 1,
        "capability": 1,
        "semantic": 1,
        "governance": 1,
        "runtime": 1,
    }
