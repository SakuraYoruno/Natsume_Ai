from __future__ import annotations

import asyncio
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
    # Compatibility input cannot resurrect a process-local cache.
    assert load_device_status_projection_sources(include_process_cache=True) == {}
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


def test_health_source_is_passive_durable_ledger_only(monkeypatch) -> None:
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat
    from host_brain.app.observability_runtime.health_device_projection import (
        load_health_projection_sources,
    )

    def forbidden_probe(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("projection source must not launch heartbeat probes")

    monkeypatch.setattr(heartbeat, "prime_core_service_cache_once", forbidden_probe)
    monkeypatch.setattr(
        heartbeat,
        "get_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("projection must not read a process heartbeat cache")
        ),
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
        include_process_cache=True,
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


def test_obs_probe_registry_does_not_read_process_heartbeat_cache(
    monkeypatch,
) -> None:
    from host_brain.app import obs_routes_core
    from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat

    monkeypatch.setattr(
        heartbeat,
        "get_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dedicated Obs must not read Host process cache")
        ),
    )

    class Ledger:
        def fetch_by_time_range(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return []

    registry, _extra = obs_routes_core._build_probe_registry(Ledger(), since_ms=0)

    edge_rpc = asyncio.run(registry["edge_rpc"]())

    assert edge_rpc.status == "UNKNOWN"


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
        include_process_cache=False,
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
