from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_device_projection_observes_receipt_after_submission(monkeypatch) -> None:
    from host_brain.app import dependencies
    from host_brain.channels import embodiment
    from host_brain.observability.health_device_projection import (
        load_device_status_projection_sources,
    )
    from host_brain.observability import health_device_projection
    from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt

    with health_device_projection._receipt_lock:
        health_device_projection._latest_receipts.clear()

    command = embodiment.build_host_embodiment_command(
        kind="avatar",
        action="state",
        payload={"behavior_id": "idle"},
        request_id="aw07-device-receipt",
    )
    durable_facts: list[dict] = []
    monkeypatch.setattr(
        dependencies,
        "get_ledger",
        lambda: SimpleNamespace(log_voice_event=lambda row: durable_facts.append(dict(row))),
    )

    class Port:
        async def submit(self, submitted):  # noqa: ANN001
            assert submitted is command
            return EmbodimentReceipt(
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

    embodiment.configure_embodiment_command_port(Port())
    try:
        receipt = asyncio.run(embodiment.submit_embodiment_command(command))
    finally:
        embodiment.configure_embodiment_command_port(None)

    sources = load_device_status_projection_sources()
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


def test_health_source_is_passive_cache_and_ledger_only(monkeypatch) -> None:
    from host_brain.observability import service_health_heartbeat as heartbeat
    from host_brain.observability.health_device_projection import (
        load_health_projection_sources,
    )

    def forbidden_probe(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("projection source must not launch heartbeat probes")

    monkeypatch.setattr(heartbeat, "prime_core_service_cache_once", forbidden_probe)
    monkeypatch.setattr(
        heartbeat,
        "get_cached",
        lambda service_id: (
            {
                "status": "UP",
                "latency_ms": 3,
                "last_check_ts": 900,
            }
            if service_id == "edge_rpc"
            else None
        ),
    )

    class Ledger:
        def fetch_by_time_range(self, table, **_kwargs):  # noqa: ANN001
            if table == "voice_event_log":
                return [{"event_type": "TTS_SENT", "status": "OK", "latency_ms": 8, "ts_ms": 800}]
            if table == "llm_node_call_log":
                return [
                    {
                        "node_id": "chat_direct_ja",
                        "status": "OK",
                        "latency_ms": 12,
                        "ts_ms": 850,
                    }
                ]
            return []

    services, nodes, cursor = load_health_projection_sources(Ledger(), now_ms=1_000)

    edge_rpc = next(row for row in services["services"] if row["service_id"] == "edge_rpc")
    chat = next(row for row in nodes["nodes"] if row["node_id"] == "chat_direct_ja")
    assert edge_rpc["status"] == "UP"
    assert chat["status"] == "UP"
    assert cursor == 1_000


def test_device_worker_can_rebuild_from_cross_process_receipt_fact() -> None:
    from host_brain.observability.health_device_projection import (
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


def test_organ_source_reads_existing_process_state_without_getter(monkeypatch) -> None:
    from host_brain.observability.health_device_projection import (
        load_organ_tribe_projection_source,
    )
    from host_brain.tribe import tribe_state

    monkeypatch.setattr(tribe_state, "_GLOBAL_REGISTRY", None)

    payload, cursor = load_organ_tribe_projection_source(
        host_state_loader=lambda: {
            "namespaces": [
                {
                    "namespace": "limbic_agent",
                    "state": {"mood": "steady", "last_updated_ts_ms": 700},
                    "updated_at_ms": 710,
                }
            ],
            "ts_ms": 800,
        },
        scheduler_loader=lambda: {
            "runtime": {"active": True},
            "tasks": [{"task_id": "emotion_decay_tick", "enabled": True}],
            "timestamp": 900,
        },
        now_ms=1_000,
    )

    limbic = next(row for row in payload["agents"] if row["agent_id"] == "limbic_agent")
    assert payload["total_agents"] == 23
    assert payload["active_agents"] >= 1
    assert payload["continuous_system_active"] is True
    assert limbic["status"] == "active"
    assert limbic["key_metrics"] == {"mood": "steady"}
    assert cursor == 1_000
