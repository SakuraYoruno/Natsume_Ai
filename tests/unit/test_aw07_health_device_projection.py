from __future__ import annotations

import sys
from types import SimpleNamespace


def test_device_projection_rebuilds_only_from_durable_receipt_facts() -> None:
    from host_brain.channels import embodiment
    from host_brain.app.observability_runtime.health_device_projection import (
        load_device_status_projection_sources,
    )
    from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt

    command = embodiment.build_host_embodiment_command(
        kind="avatar",
        action="state",
        payload={"behavior_id": "idle"},
        request_id="aw07-device-receipt",
    )
    durable_facts: list[dict] = []
    receipt = EmbodimentReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        accepted=True,
        status="APPLIED",
        reason_code=None,
        detail="ready",
        received_at_ms=100,
        completed_at_ms=105,
        event={"device": "avatar", "connected": True, "token": "must-not-project"},
    )
    embodiment.build_embodiment_receipt_audit_observer(
        lambda row: durable_facts.append(dict(row))
    )(command, receipt)

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            assert table == "voice_event_log"
            return durable_facts

    assert load_device_status_projection_sources() == {}
    sources = load_device_status_projection_sources(Ledger())
    assert receipt.status == "APPLIED"
    assert sources["avatar"][0]["success"] is True
    assert sources["vts"][0]["event"] == {
        "kind": "avatar",
        "action": "state",
        "device": "avatar",
        "connected": True,
    }
    assert durable_facts[0]["event_type"] == "EMBODIMENT_RECEIPT_AVATAR"
    assert durable_facts[0]["payload"]["event"] == {
        "device": "avatar",
        "connected": True,
    }


def test_health_source_is_passive_durable_ledger_only() -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        load_health_projection_sources,
    )

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            if table == "voice_event_log":
                return [{"event_type": "TTS_SENT", "status": "OK", "latency_ms": 8, "ts_ms": 800}]
            if table == "model_attempt_log":
                return [
                    {
                        "attempt_id": "attempt-chat",
                        "status": "started",
                        "provider": "gemini",
                        "model_id": "gemini-2.5-flash-lite",
                        "metadata": {"model_node_id": "chat_direct_ja"},
                        "ts_ms": 840,
                    },
                    {
                        "attempt_id": "attempt-chat",
                        "status": "succeeded",
                        "provider": "gemini",
                        "model_id": "gemini-2.5-flash-lite",
                        "metadata": {"model_node_id": "chat_direct_ja"},
                        "latency_ms": 12,
                        "ts_ms": 850,
                    }
                ]
            if table == "llm_pool_call_log":
                return []
            return []

    services, nodes, cursor = load_health_projection_sources(
        Ledger(),
        now_ms=1_000,
    )

    edge_rpc = next(row for row in services["services"] if row["service_id"] == "edge_rpc")
    tts = next(row for row in services["services"] if row["service_id"] == "tts")
    chat = next(row for row in nodes["nodes"] if row["node_id"] == "chat_direct_ja")
    same_model_other_node = next(
        row for row in nodes["nodes"] if row["node_id"] == "core_reasoner_secondary"
    )
    assert edge_rpc["status"] == "UNKNOWN"
    assert tts["status"] == "UP"
    assert chat["status"] == "UP"
    assert chat["call_count"] == 1
    assert same_model_other_node["status"] == "UNKNOWN"
    assert cursor == 1_000


def test_health_projection_rebuilds_remote_edge_not_tested_after_restart(
    tmp_path,
) -> None:
    from natsume_contracts.edge.service_catalog_v1 import edge_required_services

    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.app.observability_runtime.health_device_projection import (
        load_health_projection_sources,
    )
    from host_brain.core.errors import ACCEPTANCE_PENDING_EXTERNAL
    from host_brain.core.services import (
        ServiceHealthReporter,
        build_default_service_registry,
    )

    database = tmp_path / "canonical.sqlite3"
    observed_at = 1_800_000_300_000
    runtime_statuses = {
        descriptor.service_id: {
            "available": False,
            "status": "NOT_TESTED",
            "not_tested": True,
            "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
            "detail": "remote Edge disabled by Host configuration",
        }
        for descriptor in edge_required_services()
    }

    writer = SQLiteLedger(database)
    ServiceHealthReporter(
        registry=build_default_service_registry(),
        ledger=writer,
    ).build_report(
        request_id="dl07-edge-disabled",
        runtime_statuses=runtime_statuses,
        ts_ms=observed_at,
    )
    writer.close()

    # Reopen the canonical database to model a standalone Obs restart.  No
    # Host process cache or network probe is available on this path.
    reader = SQLiteLedger(database, initialize_schema=False)
    try:
        services, _nodes, _cursor = load_health_projection_sources(
            reader,
            now_ms=observed_at + 1,
        )
    finally:
        reader.close()

    edge_rows = {
        row["service_id"]: row
        for row in services["services"]
        if row["service_type"] == "edge"
    }
    assert services["not_tested"] == 7
    assert services["unknown"] >= 1
    assert set(edge_rows) == {
        "asr",
        "tts",
        "vts",
        "audio_playback",
        "barge_in",
        "resource_gate",
        "edge_rpc",
        "unified_gateway",
    }
    assert {
        row["status"]
        for service_id, row in edge_rows.items()
        if service_id != "unified_gateway"
    } == {"NOT_TESTED"}
    assert edge_rows["unified_gateway"]["status"] == "UNKNOWN"
    assert {
        row["reason_code"]
        for service_id, row in edge_rows.items()
        if service_id != "unified_gateway"
    } == {ACCEPTANCE_PENDING_EXTERNAL}
    assert edge_rows["vts"]["public_summary"] == (
        "remote Edge disabled by Host configuration"
    )


def test_gateway_health_merge_uses_durable_live_source_without_unknown() -> None:
    from types import SimpleNamespace

    from host_brain.app.observability_runtime.health_device_projection import (
        merge_gateway_health_projection,
    )
    from natsume_contracts.operator.v1 import ProjectionDegradedState

    payload = {
        "services": [
            {
                "service_id": "unified_gateway",
                "service_type": "edge",
                "display_name": "Unified Gateway",
                "status": "UNKNOWN",
            }
        ],
        "timestamp": 100,
    }
    document = SimpleNamespace(
        degraded_state=ProjectionDegradedState.HEALTHY,
        freshness_ts_ms=200,
        payload={
            "remote_gateway_health": {
                "body": {
                    "status": "ok",
                    "outbound_ready": True,
                    "transport": {"worker_alive": True, "delivery_enabled": True},
                }
            }
        },
    )

    merged = merge_gateway_health_projection(payload, document)

    assert merged["unknown"] == 0
    assert merged["up"] == 1
    assert merged["services"][0]["service_id"] == "unified_gateway"
    assert merged["services"][0]["status"] == "UP"
    assert merged["services"][0]["last_call_ts"] == 200


def test_newer_voice_receipt_is_not_hidden_by_older_health_snapshot() -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        load_health_projection_sources,
    )

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            if table == "voice_event_log":
                return [
                    {
                        "event_type": "TTS_SENT",
                        "status": "OK",
                        "latency_ms": 8,
                        "ts_ms": 900,
                    }
                ]
            if table == "service_health_log":
                return [
                    {
                        "request_id": "older-disabled-report",
                        "service_id": "edge.rpc.tts",
                        "status": "NOT_TESTED",
                        "reason_code": "ACCEPTANCE_PENDING_EXTERNAL",
                        "detail": "older status",
                        "ts_ms": 800,
                    }
                ]
            return []

    services, _nodes, _cursor = load_health_projection_sources(
        Ledger(),
        now_ms=1_000,
    )

    tts = next(row for row in services["services"] if row["service_id"] == "tts")
    assert tts["status"] == "UP"
    assert tts["last_call_ts"] == 900


def test_llm_gateway_health_merges_sources_by_event_time_before_windowing() -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        load_health_projection_sources,
    )

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            if table == "model_attempt_log":
                return [
                    {
                        "attempt_id": f"new-attempt-{index}",
                        "status": "succeeded",
                        "latency_ms": 10,
                        "ended_at_ms": 800 + index,
                        "ts_ms": 800 + index,
                        "metadata": {},
                    }
                    for index in range(20)
                ]
            if table == "llm_pool_call_log":
                return [
                    {
                        "request_id": f"old-pool-{index}",
                        "pool_exhausted": 1,
                        "total_latency_ms": 20,
                        "ts_ms": 100 + index,
                    }
                    for index in range(20)
                ]
            return []

    services, _nodes, _cursor = load_health_projection_sources(
        Ledger(),
        now_ms=1_000,
    )

    gateway = next(
        row for row in services["services"] if row["service_id"] == "llm_gateway"
    )
    assert gateway["status"] == "UP"
    assert gateway["success_rate"] == 100.0
    assert gateway["call_count"] == 20
    assert gateway["last_call_ts"] == 819


def test_device_worker_can_rebuild_from_cross_process_receipt_fact() -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        load_device_status_projection_sources,
    )

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            assert table == "voice_event_log"
            return [
                {
                    "request_id": "req-device",
                    "event_type": "EMBODIMENT_RECEIPT_DESKTOP",
                    "status": "APPLIED",
                    "reason_code": None,
                    "detail": "ready",
                    "payload": {
                        "kind": "desktop",
                        "action": "open",
                        "command_id": "emb-1",
                        "success": True,
                        "latency_ms": 6,
                        "completed_at_ms": 500,
                        "event": {"device": "desktop", "connected": True},
                    },
                    "ts_ms": 500,
                }
            ]

    sources = load_device_status_projection_sources(Ledger())

    assert sources["desktop"][1] == 500
    assert sources["desktop"][0]["status"] == "APPLIED"
    assert sources["desktop"][0]["event"]["connected"] is True


def test_organ_public_snapshot_uses_only_explicit_evidence(monkeypatch) -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        ORGAN_AGENT_REGISTRY,
        build_organ_tribe_public_snapshot,
    )
    from host_brain.tribe import tribe_state

    class ForbiddenRegistry:
        def snapshot(self):
            raise AssertionError("public snapshot builder read the global registry")

    monkeypatch.setattr(
        tribe_state,
        "_GLOBAL_REGISTRY",
        ForbiddenRegistry(),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "host_brain.tribe.prefrontal_agent",
        SimpleNamespace(_PREFRONTAL_AGENT=object()),
    )

    payload, cursor = build_organ_tribe_public_snapshot(
        registry_snapshots={
            "limbic_agent": {
                "state": {
                    "active": True,
                    "mood": "steady",
                    "last_immune_block_reason": "private user home address",
                    "last_updated_ts_ms": 700,
                },
                "updated_at_ms": 710,
            },
            # Snapshot presence alone is not live evidence.
            "prefrontal_agent": {"state": {"focus": "ready"}},
            # Future evidence is invalid for this observation.
            "social_brain_agent": {
                "state": {"rapport": "warm"},
                "evidence_ts_ms": 1_100,
                "timestamp": 800,
            },
        },
        scheduler_snapshot={
            "runtime": {"active": True},
            "tasks": [{"task_id": "emotion_decay_tick", "enabled": True}],
            "timestamp": 900,
        },
        observed_at_ms=1_000,
    )

    limbic = next(row for row in payload["agents"] if row["agent_id"] == "limbic_agent")
    prefrontal = next(
        row for row in payload["agents"] if row["agent_id"] == "prefrontal_agent"
    )
    social = next(
        row for row in payload["agents"] if row["agent_id"] == "social_brain_agent"
    )
    assert payload["total_agents"] == len(ORGAN_AGENT_REGISTRY) == 23
    assert all(len(entry) == 2 for entry in ORGAN_AGENT_REGISTRY)
    assert [row["agent_id"] for row in payload["agents"]] == [
        row[0] for row in ORGAN_AGENT_REGISTRY
    ]
    assert payload["active_agents"] == 1
    assert payload["continuous_system_active"] is True
    assert payload["emotion_decay_tick_enabled"] is True
    assert payload["source_process"] == "host-runtime"
    assert payload["observed_at_ms"] == payload["timestamp"] == 1_000
    assert payload["evidence_ts_ms"] == 900
    assert limbic["status"] == "active"
    assert limbic["key_metrics"] == {"active": True}
    assert "private user home address" not in repr(payload)
    assert "last_immune_block_reason" not in repr(payload)
    assert limbic["last_updated_ts_ms"] == 710
    assert prefrontal["status"] == "loaded"
    assert prefrontal["last_updated_ts_ms"] is None
    assert social["status"] == "loaded"
    assert social["last_updated_ts_ms"] is None
    assert cursor == 900


def test_organ_public_snapshot_does_not_manufacture_evidence_from_observation_time() -> None:
    from host_brain.app.observability_runtime.health_device_projection import (
        build_organ_tribe_public_snapshot,
    )

    payload, cursor = build_organ_tribe_public_snapshot(
        registry_snapshots={},
        scheduler_snapshot={"runtime": {"active": False}},
        observed_at_ms=5_000,
    )

    assert payload["active_agents"] == 0
    assert payload["evidence_ts_ms"] == 0
    assert payload["timestamp"] == 5_000
    assert cursor == 0
