from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from host_brain.composition.host_memory_runtime import HostMemoryRuntimeOwner
from host_brain.core.emotion_timeline import RetiredEmotionTimelineProjection
from host_brain.app.startup import warmup_emotion_timeline
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.lifecycle_runtime.runtime_snapshot import (
    build_digital_life_runtime_snapshot,
)
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.limbic_agent import LimbicAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def _unused_factory(**_kwargs: Any) -> object:
    return object()


def test_production_memory_owner_does_not_hydrate_legacy_emotion_timeline() -> None:
    ledger_calls = 0

    def ledger_provider() -> Any:
        nonlocal ledger_calls
        ledger_calls += 1
        raise AssertionError("retired timeline must not open the legacy ledger")

    owner = HostMemoryRuntimeOwner(
        ledger_provider=ledger_provider,
        memory_guard_factory=_unused_factory,
        state_arbiter_factory=_unused_factory,
        state_write_arbiter_factory=_unused_factory,
        episodic_store_adapter_factory=_unused_factory,
        distillation_engine_factory=_unused_factory,
        semantic_recaller_factory=_unused_factory,
        legacy_emotion_timeline_enabled=False,
    )

    projection = owner.require_emotion_timeline()

    assert isinstance(projection, RetiredEmotionTimelineProjection)
    assert owner.require_emotion_timeline() is projection
    assert ledger_calls == 0
    assert projection.rebuild_summary() == {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_EMOTION_TIMELINE_RETIRED",
        "entries": [],
    }


def test_startup_reports_retired_timeline_instead_of_false_ok() -> None:
    assert warmup_emotion_timeline(
        ports=SimpleNamespace(
            emotion_timeline=lambda: RetiredEmotionTimelineProjection()
        )
    ) == "RETIRED_CANONICAL_LIFE"


def test_canonical_limbic_does_not_resolve_or_cache_legacy_timeline() -> None:
    calls = 0

    def timeline_provider() -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("canonical Life must not resolve legacy timeline")

    agent = LimbicAgent(
        state_registry=TribeStateRegistry(),
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
            emotion_timeline=timeline_provider,
        ),
    )

    result = agent.resolve_emotion_timeline_snapshot()

    assert calls == 0
    assert result["status"] == "RETIRED_CANONICAL_LIFE"
    assert result["reason_code"] == "LEGACY_EMOTION_TIMELINE_READ_RETIRED"
    assert result["summary"] == {}
    assert result["diary"] == {}
    assert result["dominant_emotion"] == ""
    assert agent.state_registry.get_state("limbic") is None


def test_runtime_snapshot_keeps_user_profile_out_of_self_affect_and_marks_retirement() -> None:
    class _Profile:
        def to_summary(self) -> dict[str, Any]:
            return {
                "top_emotions": [{"tag": "SAD", "ratio": 0.9}],
                "volatility": 0.95,
            }

    ports = LifecycleRuntimeApplicationPorts(
        get_activity_monitor=lambda: None,
        get_endocrine_state=lambda **_kwargs: {},
        get_episodic_store=lambda: object(),
        get_hard_state=lambda: SimpleNamespace(sys_mode="normal"),
        get_ledger=lambda: object(),
        get_life_memory_store=lambda: object(),
        get_model_attempt_service=lambda: object(),
        emotion_timeline=RetiredEmotionTimelineProjection(),
        get_user_emotion_profile=lambda: _Profile(),
    )

    snapshot = build_digital_life_runtime_snapshot(ports=ports)

    emotion_context = dict(snapshot["emotion_context"])
    assert emotion_context["emotion_tag"] == "NEUTRAL"
    assert emotion_context["emotion_intensity"] == 0.0
    assert emotion_context["user_emotion_profile"]["top_emotions"][0]["tag"] == "SAD"
    assert "emotion_timeline_summary" not in emotion_context
    assert "emotion_diary" not in emotion_context
    assert snapshot["slot_status"]["emotion_context"] == "retired"
    assert "emotion_context:retired" not in snapshot["degraded_reasons"]
