from __future__ import annotations

import host_brain.skills.impl.emergent_growth_skill as emergent_growth_skill

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


def test_emergent_growth_weekly_delegates_to_prefrontal() -> None:
    fake_prefrontal = _FakePrefrontal()

    result = execute_emergent_growth_weekly(
        args={"user_id": "u-eg", "lookback_days": 9},
        context={
            "request_id": "req-eg-weekly",
            "model_attempt_service": object(),
            "prefrontal_agent": fake_prefrontal,
            "episodic_store": object(),
        },
    )

    assert result["status"] == "OK"
    assert result["growth_result"]["applied"] is True
    assert fake_prefrontal.weekly_calls[0]["user_id"] == "u-eg"
    assert fake_prefrontal.weekly_calls[0]["lookback_days"] == 9


def test_emergent_growth_weekly_canonical_preflight_skips_model_resolution(monkeypatch) -> None:
    class _CanonicalPrefrontal:
        def __init__(self) -> None:
            self.weekly_calls: list[dict[str, object]] = []

        def uses_canonical_affective_life(self) -> bool:
            return True

        def run_emergent_growth_weekly(self, **kwargs):  # noqa: ANN003
            self.weekly_calls.append(dict(kwargs))
            assert kwargs["llm_fn"] is None
            assert kwargs["episodic_store"] is None
            return {
                "status": "DEFERRED",
                "reason_code": "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED",
                "user_id": kwargs["user_id"],
                "lookback_days": kwargs["lookback_days"],
                "pattern_result": {},
                "growth_result": {},
                "proposal": {
                    "status": "NO_CANDIDATE",
                    "reason_code": "LEGACY_EMERGENT_GROWTH_RETIRED",
                },
            }

    monkeypatch.setattr(
        emergent_growth_skill,
        "_resolve_llm_fn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical preflight must not resolve a model port")
        ),
    )
    prefrontal = _CanonicalPrefrontal()

    result = execute_emergent_growth_weekly(
        args={"user_id": "u-canonical", "lookback_days": 8},
        context={"prefrontal_agent": prefrontal},
    )

    assert result["status"] == "DEFERRED"
    assert result["reason_code"] == "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED"
    assert result["proposal"]["reason_code"] == "LEGACY_EMERGENT_GROWTH_RETIRED"
    assert prefrontal.weekly_calls[0]["user_id"] == "u-canonical"


def test_emergent_growth_status_delegates_to_prefrontal(monkeypatch) -> None:
    fake_prefrontal = _FakePrefrontal()
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_prefrontal_agent", lambda: fake_prefrontal)

    result = execute_emergent_growth_status(args={"user_id": "u-eg-status"})

    assert result["status"] == "OK"
    assert result["growth_summary"]["weekly_cycles"] == 2
    assert fake_prefrontal.status_calls[0]["user_id"] == "u-eg-status"
