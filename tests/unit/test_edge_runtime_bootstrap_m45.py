"""M45-A: Edge runtime bootstrap lifecycle tests (T-EDGE-BOOT-01~05)."""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from edge_client.config import EdgeConfig, load_edge_config
from edge_client.runtime.bootstrap import EdgeRuntime, bootstrap, shutdown, startup
from edge_client.runtime.health import collect_health


def _load_test_config(*, fallback_enabled: bool = True) -> EdgeConfig:
    result = load_edge_config(
        env={
            "Natsume_ENV": "test",
            "EDGE_ALLOW_INSECURE_AUTH": "true",
            "EDGE_RPC_GATEWAY_ENABLED": "true",
            "EDGE_ASR_ENABLED": "true",
            "EDGE_TTS_ENABLED": "true",
            "EDGE_BARGE_IN_ENABLED": "true",
            "EDGE_FALLBACK_LLM_ENABLED": "true" if fallback_enabled else "false",
            "EDGE_RESOURCE_GATE_ENABLED": "true",
        }
    )
    assert result.success and result.config is not None, result.as_dict()
    return result.config


class _LifecycleComponent:
    def __init__(self, name: str, events: list[str], *, raise_on_startup: bool = False) -> None:
        self._name = name
        self._events = events
        self._raise_on_startup = raise_on_startup

    async def startup(self) -> None:
        self._events.append(f"startup:{self._name}")
        if self._raise_on_startup:
            raise RuntimeError(f"boom:{self._name}")

    async def shutdown(self) -> None:
        self._events.append(f"shutdown:{self._name}")


class TestEdgeRuntimeBootstrapM45(unittest.TestCase):
    def test_t_edge_boot_01_bootstrap_returns_edge_runtime(self) -> None:
        runtime = bootstrap(_load_test_config())

        self.assertIsInstance(runtime, EdgeRuntime)
        self.assertIsNotNone(runtime.asr)
        self.assertIsNotNone(runtime.tts)
        self.assertIsNotNone(runtime.vts)
        self.assertIsNotNone(runtime.fallback)
        self.assertIsNotNone(runtime.resource_gate)
        self.assertIsNotNone(runtime.rpc_client)

    def test_t_edge_boot_02_startup_calls_components_in_order(self) -> None:
        events: list[str] = []
        runtime = SimpleNamespace(
            asr=_LifecycleComponent("asr", events),
            tts=_LifecycleComponent("tts", events),
            vts=_LifecycleComponent("vts", events),
            fallback=_LifecycleComponent("fallback", events),
            resource_gate=_LifecycleComponent("resource_gate", events),
            rpc_client=_LifecycleComponent("rpc_client", events),
            close=lambda: events.append("close"),
        )

        asyncio.run(startup(runtime))
        self.assertEqual(
            [
                "startup:asr",
                "startup:tts",
                "startup:vts",
                "startup:fallback",
                "startup:resource_gate",
                "startup:rpc_client",
            ],
            events,
        )

    def test_t_edge_boot_03_shutdown_calls_components_in_reverse_order(self) -> None:
        events: list[str] = []
        runtime = SimpleNamespace(
            asr=_LifecycleComponent("asr", events),
            tts=_LifecycleComponent("tts", events),
            vts=_LifecycleComponent("vts", events),
            fallback=_LifecycleComponent("fallback", events),
            resource_gate=_LifecycleComponent("resource_gate", events),
            rpc_client=_LifecycleComponent("rpc_client", events),
            close=lambda: events.append("close"),
        )

        asyncio.run(shutdown(runtime))
        self.assertEqual(
            [
                "shutdown:rpc_client",
                "shutdown:resource_gate",
                "shutdown:fallback",
                "shutdown:vts",
                "shutdown:tts",
                "shutdown:asr",
                "close",
            ],
            events,
        )

    def test_t_edge_boot_04_startup_exception_is_logged_and_other_components_continue(self) -> None:
        events: list[str] = []
        runtime = SimpleNamespace(
            asr=_LifecycleComponent("asr", events),
            tts=_LifecycleComponent("tts", events, raise_on_startup=True),
            vts=_LifecycleComponent("vts", events),
            fallback=_LifecycleComponent("fallback", events),
            resource_gate=_LifecycleComponent("resource_gate", events),
            rpc_client=_LifecycleComponent("rpc_client", events),
            close=lambda: events.append("close"),
        )

        with self.assertLogs("Natsume.edge.runtime.bootstrap", level="ERROR") as captured:
            asyncio.run(startup(runtime))

        self.assertIn("startup:rpc_client", events)
        self.assertTrue(any("component=tts" in line for line in captured.output))

    def test_t_edge_boot_05_runtime_fields_support_health_snapshot_serialization(self) -> None:
        runtime = bootstrap(_load_test_config())
        snapshot = collect_health(runtime)

        self.assertIn("status", snapshot)
        self.assertIn("components", snapshot)
        self.assertIn("resource_gate", snapshot)
        self.assertIn("timestamp", snapshot)
        self.assertIn("asr", snapshot["components"])
        self.assertIn("tts", snapshot["components"])
        self.assertIn("vts", snapshot["components"])
        self.assertIn("fallback", snapshot["components"])
        json.dumps(snapshot)


if __name__ == "__main__":
    unittest.main()
