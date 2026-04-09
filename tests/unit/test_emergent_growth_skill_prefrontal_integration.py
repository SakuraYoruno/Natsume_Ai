from __future__ import annotations

from host_brain.skills.impl.emergent_growth_skill import (
    execute_emergent_growth_status,
    execute_emergent_growth_weekly,
)


class _FakePrefrontal:
    def __init__(self) -> None:
        self.weekly_calls: list[dict[str, object]] = []
        self.status_calls: list[dict[str, object]] = []

    def run_emergent_growth_weekly(self, **kwargs):  # noqa: ANN003
        self.weekly_calls.append(dict(kwargs))
        return {
            "status": "OK",
            "user_id": kwargs.get("user_id"),
            "lookback_days": kwargs.get("lookback_days"),
            "pattern_result": {"signal": "rich"},
            "growth_result": {"applied": True},
        }

    def resolve_emergent_growth_status(self, **kwargs):  # noqa: ANN003
        self.status_calls.append(dict(kwargs))
        return {
            "status": "OK",
            "user_id": kwargs.get("user_id"),
            "growth_summary": {"weekly_cycles": 2},
            "recent_growth_log": [{"cycle": 2}],
        }


def test_emergent_growth_weekly_delegates_to_prefrontal(monkeypatch) -> None:
    fake_prefrontal = _FakePrefrontal()
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_prefrontal_agent", lambda: fake_prefrontal)
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: object())

    result = execute_emergent_growth_weekly(args={"user_id": "u-eg", "lookback_days": 9, "use_llm": False})

    assert result["status"] == "OK"
    assert result["growth_result"]["applied"] is True
    assert fake_prefrontal.weekly_calls[0]["user_id"] == "u-eg"
    assert fake_prefrontal.weekly_calls[0]["lookback_days"] == 9


def test_emergent_growth_status_delegates_to_prefrontal(monkeypatch) -> None:
    fake_prefrontal = _FakePrefrontal()
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_prefrontal_agent", lambda: fake_prefrontal)

    result = execute_emergent_growth_status(args={"user_id": "u-eg-status"})

    assert result["status"] == "OK"
    assert result["growth_summary"]["weekly_cycles"] == 2
    assert fake_prefrontal.status_calls[0]["user_id"] == "u-eg-status"
