from __future__ import annotations

from host_brain.bond.bond_stage_engine import BondStageEngine, STAGE_FRIEND


def test_disabled_automatic_bond_stage_never_promotes_intimacy() -> None:
    """A legacy score can describe activity but cannot change relationship rank."""

    engine = BondStageEngine(
        stage_thresholds={"FRIEND": 0, "CONFIDANT": 1},
        score_map={"EMOTIONAL_SUPPORT": 8},
        automatic_stage_transitions=False,
    )

    result = engine.apply_event(
        user_id="person-dl03",
        update_kind="EMOTIONAL_SUPPORT",
        evidence_refs=["life-event://accepted-response"],
        request_id="dl03-no-auto-stage",
        ts_ms=1_900_000_000_000,
    )

    assert result.from_stage == STAGE_FRIEND
    assert result.to_stage == STAGE_FRIEND
    assert result.transition_status == "STABLE"
    assert result.total_score == 8
    assert "AUTO_STAGE_DISABLED" in result.detail
