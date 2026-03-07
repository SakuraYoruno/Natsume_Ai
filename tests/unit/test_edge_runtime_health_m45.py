"""M45-B: Edge runtime health aggregation tests (T-EDGE-HEALTH-01~05)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest

from edge_client.runtime.health import collect_health


class _HealthComponent:
    def __init__(self, status: str = "ok", *, raises: bool = False) -> None:
        self._status = status
        self._raises = raises

    def health(self) -> dict[str, str]:
        if self._raises:
            raise RuntimeError("health failed")
        return {"status": self._status}


class _FallbackComponent:
    def __init__(self, status: str = "ok") -> None:
        self.available = True
        self.default_unloaded = False
        self.model_id = "ollama:3b-q4"
        self._status = status

    def health(self) -> dict[str, str]:
        return {"status": self._status}


def _build_runtime(
    *,
    asr_status: str = "ok",
    tts_status: str = "ok",
    vts_status: str = "ok",
    asr_raises: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            asr_enabled=True,
            tts_enabled=True,
            fallback_llm_enabled=True,
            resource_gate_enabled=True,
            max_vram_mb=4096,
        ),
        asr=_HealthComponent(asr_status, raises=asr_raises),
        tts=_HealthComponent(tts_status),
        vts=_HealthComponent(vts_status),
        fallback=_FallbackComponent("ok"),
        event_reporter=_HealthComponent("ok"),
        resource_gate=SimpleNamespace(
            snapshot=lambda: {
                "stage": "S_VOICE_RESOURCE_GATE",
                "active_tasks": [],
                "metrics": {
                    "vram_total_mb": 4096,
                    "vram_used_mb": 1024,
                    "queue_depth": 2,
                    "vram_utilization": 0.25,
                },
            }
        ),
        speech_queue=SimpleNamespace(queue_depth=2),
    )


class TestEdgeRuntimeHealthM45(unittest.TestCase):
    def test_t_edge_health_01_all_components_ok(self) -> None:
        health = collect_health(_build_runtime())
        self.assertEqual("ok", health["status"])

    def test_t_edge_health_02_any_degraded_yields_degraded(self) -> None:
        health = collect_health(_build_runtime(asr_status="degraded"))
        self.assertEqual("degraded", health["status"])

    def test_t_edge_health_03_any_down_yields_down(self) -> None:
        health = collect_health(_build_runtime(tts_status="down"))
        self.assertEqual("down", health["status"])

    def test_t_edge_health_04_component_health_exception_marks_error(self) -> None:
        health = collect_health(_build_runtime(asr_raises=True))
        self.assertEqual("error", health["components"]["asr"]["status"])
        self.assertEqual("degraded", health["status"])

    def test_t_edge_health_05_timestamp_is_iso8601(self) -> None:
        health = collect_health(_build_runtime(), ts_ms=1_800_102_000_000)
        value = str(health["timestamp"])
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed)


if __name__ == "__main__":
    unittest.main()
