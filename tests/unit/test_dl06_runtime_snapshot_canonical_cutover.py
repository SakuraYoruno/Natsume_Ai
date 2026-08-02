from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.lifecycle_runtime.runtime_snapshot import (
    build_digital_life_runtime_snapshot,
)


class _Reader:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot

    def get_snapshot(self, person_id: str) -> dict[str, Any]:
        assert person_id == "user-001"
        return dict(self._snapshot)


def _ports(
    reader_provider,
    *,
    require_canonical_life_snapshot: bool = False,
) -> LifecycleRuntimeApplicationPorts:  # noqa: ANN001
    return LifecycleRuntimeApplicationPorts(
        get_activity_monitor=lambda: None,
        get_endocrine_state=lambda **_kwargs: {"energy": 0.7},
        get_episodic_store=lambda: object(),
        get_hard_state=lambda: SimpleNamespace(sys_mode="normal"),
        get_ledger=lambda: object(),
        get_life_memory_store=lambda: object(),
        get_model_attempt_service=lambda: object(),
        emotion_timeline=SimpleNamespace(
            rebuild_summary=lambda **_kwargs: {},
            build_daily_diary=lambda **_kwargs: {},
        ),
        get_life_snapshot_reader=reader_provider,
        require_canonical_life_snapshot=require_canonical_life_snapshot,
    )


def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("canonical runtime snapshot must not read a legacy authority")


def test_canonical_runtime_snapshot_keeps_user_observation_out_of_natsume_affect(
    monkeypatch,
) -> None:
    snapshot = {
        "person_id": "user-001",
        "version": 12,
        "state_digest": "life-digest-12",
        "state": {
            "identity": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "user-001",
            },
            "affective": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "user-001",
                "revision": 8,
                "components": [
                    {
                        "emotion": "HURT",
                        "intensity": 0.62,
                        "valence": -0.48,
                        "arousal": 0.54,
                        "action_tendency": "repair",
                    }
                ],
                "mood": {"label": "quiet", "energy": 0.58},
                "needs": {"repair": 0.7, "safety": 0.2},
                "last_regulation": {"mode": "REPAIR_OPEN"},
            },
            "relationship_v2": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "user-001",
                "workspace_id": "workspace::demo",
                "revision": 4,
                "trust": 0.7,
                "safety": 0.8,
                "attunement": 0.6,
                "rupture_load": 0.3,
                "repair_required": True,
                "proactive_permission": "DENIED",
            },
            "observations": {
                "last_user_affect": {
                    "source": "text_heuristic",
                    "modality": "text",
                    "emotion_tag": "SAD",
                    "intensity": 0.91,
                    "confidence": 0.4,
                    "observed_at_ms": 123,
                }
            },
            "clock": {"day_phase": "evening", "last_user_seen_ms": 1},
            "affect": {"fatigue": 0.25, "loneliness": 0.0},
            "life_pressure": {"fatigue": 0.2},
            "inner_world": {"attention_target": "repair"},
        },
    }
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "user-001",
    )
    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.prefrontal_agent.get_prefrontal_agent",
        _forbidden,
    )

    result = build_digital_life_runtime_snapshot(
        ports=_ports(lambda: _Reader(snapshot)),
        current_topic="我没事。",
    )

    context = result["emotion_context"]
    assert result["self_id"] == "natsume_ai"
    assert result["relationship_subject_id"] == "user-001"
    assert context["emotion_tag"] == "HURT"
    assert context["user_affect_observation"]["emotion_tag"] == "SAD"
    assert result["bond_state"]["current_stage"] == "FRIEND"
    assert result["bond_summary"]["proactive_permission"] == "DENIED"
    assert result["slot_status"]["canonical_life"] == "live"
    assert result["slot_status"]["legacy_emotion_timeline"] == "retired"
    assert result["slot_status"]["legacy_bond"] == "retired"
    assert result["compatibility_projection_usage"] == {
        "schema_version": "Natsume.lifecycle_runtime.compatibility_usage.v1",
        "scope": "port_instance",
        "projection_id": "legacy_runtime_snapshot_assembler",
        "legacy_runtime_snapshot_read_count": 0,
        "legacy_runtime_snapshot_fallback_suppressed_count": 0,
    }


def test_composed_canonical_reader_failure_never_falls_back_to_social_brain(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "user-001",
    )
    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        _forbidden,
    )

    def unavailable_reader() -> Any:
        raise RuntimeError("life reader unavailable")

    result = build_digital_life_runtime_snapshot(
        ports=_ports(unavailable_reader),
    )

    assert result["degraded"] is True
    assert result["slot_status"]["canonical_life"] == "degraded"
    assert result["slot_status"]["legacy_bond"] == "retired"
    assert result["bond_state"] == {}
    assert (
        result["compatibility_projection_usage"]
        ["legacy_runtime_snapshot_read_count"]
        == 0
    )


def test_required_canonical_reader_missing_never_revives_legacy_assembler(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "user-001",
    )
    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        _forbidden,
    )

    result = build_digital_life_runtime_snapshot(
        ports=_ports(
            None,
            require_canonical_life_snapshot=True,
        ),
    )

    assert result["degraded"] is True
    assert result["slot_status"]["canonical_life"] == "degraded"
    assert result["slot_status"]["legacy_bond"] == "retired"
    assert result["bond_state"] == {}
    assert result["compatibility_projection_usage"] == {
        "schema_version": "Natsume.lifecycle_runtime.compatibility_usage.v1",
        "scope": "port_instance",
        "projection_id": "legacy_runtime_snapshot_assembler",
        "legacy_runtime_snapshot_read_count": 0,
        "legacy_runtime_snapshot_fallback_suppressed_count": 1,
    }
