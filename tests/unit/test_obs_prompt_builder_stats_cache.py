from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache


class _CountingLedger:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_by_time_range(self, table: str, start_ms: int, end_ms: int):
        self.calls += 1
        if table != "prompt_builder_log":
            return []
        return [
            {
                "audit_tag": "PROMPT_BUILT",
                "detail": '{"risk_level":"HIGH","slots_filled":["persona","bond"],"user_message_len":42}',
                "stage": "PROMPT_BUILDER",
                "reason_code": None,
            }
        ]


def setup_function() -> None:
    _clear_obs_route_response_cache()


def teardown_function() -> None:
    _clear_obs_route_response_cache()


def test_obs_prompt_builder_stats_uses_short_ttl_cache(monkeypatch) -> None:
    ledger = _CountingLedger()
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_skill.get_projection_ledger_view",
        lambda: ledger,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    first = client.get("/obs/prompt-builder/stats?window_minutes=60")
    second = client.get("/obs/prompt-builder/stats?window_minutes=60")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["total_builds"] == 1
    assert second.json()["high_risk_count"] == 1
    assert ledger.calls == 1
