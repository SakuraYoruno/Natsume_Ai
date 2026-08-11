from __future__ import annotations

from host_brain.lifecycle_runtime.life_jobs import _run_evolution_cycle


class _FakeLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


def test_run_evolution_cycle_delegates_to_prefrontal_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakePrefrontalAgent:
        def run_evolution_cycle(self, *, system_intent, logger):
            captured["system_intent"] = system_intent
            captured["logger"] = logger
            return {"status": "OK", "delegated": True}

    monkeypatch.setattr(
        "host_brain.tribe.prefrontal_agent.get_prefrontal_agent",
        lambda: _FakePrefrontalAgent(),
    )

    logger = _FakeLogger()
    result = _run_evolution_cycle(
        system_intent={"args": {"user_id": "user_z"}},
        logger=logger,
    )

    assert result == {"status": "OK", "delegated": True}
    assert captured["system_intent"]["args"]["user_id"] == "user_z"
    assert captured["logger"] is logger
