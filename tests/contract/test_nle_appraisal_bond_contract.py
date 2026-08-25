from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_projection import (
    BOND_PROJECTION_DISABLED,
    BOND_PROJECTION_SCHEMA_VERSION,
    BondProjectionReadModel,
    build_bond_projection,
)
from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.bond.neo4j_client import BOND_WRITER_ID, InMemoryNeo4jBondClient
from host_brain.core.errors import BOND_EVIDENCE_REQUIRED, BOND_SINGLE_WRITER_VIOLATION
from host_brain.persona.appraisal_adapter import (
    APPRAISAL_SCHEMA_VERSION,
    build_appraisal_from_emotional_aftereffect,
    build_appraisal_from_emotional_episode,
    replay_appraisal,
    replay_appraisal_chain,
)
from host_brain.persona.emotional_proactive import (
    build_emotional_proactive_aftereffect,
    build_emotional_proactive_episode,
)
from host_brain.tribe.social_brain_agent import SocialBrainAgent
from host_brain.tribe.tribe_state import TribeStateRegistry
from tools.static_scan.bond_ssot_scan import scan_repo


ROOT = Path(__file__).resolve().parents[2]


def _episode_and_boundary_aftereffect():
    episode = build_emotional_proactive_episode(
        inner_state={
            "loneliness": 0.78,
            "anticipation": 0.63,
            "continuity_score": 0.74,
            "drifted_valence": 0.22,
            "drifted_arousal": 0.58,
            "anticipation_topics": ["project"],
        },
        inner_weather={"reach_out_bias": 0.71},
        bond_summary={"stage": "亲近兄妹", "trend": "稳定"},
        scene_awareness={"scene_tags": []},
        now_ms=1_800_000_000_000,
    )
    aftereffect = build_emotional_proactive_aftereffect(
        episode=episode,
        outcome="replied",
        user_reply="别发了，我在忙",
        now_ms=1_800_000_001_000,
        delivery_status="delivered",
    )
    return episode, aftereffect


def _write(
    writer: BondDynamicsWriter,
    *,
    request_id: str,
    update_kind: str,
    evidence_refs: list[str],
    payload: dict | None = None,
):
    return writer.apply_update(
        request_id=request_id,
        intent_id=f"intent:{request_id}",
        session_id="session-t-nle-16",
        seq=1,
        user_id="user-t-nle-16",
        topic_id="relationship",
        capability_hint="test",
        evidence_refs=evidence_refs,
        payload=dict(payload or {}),
        update_kind=update_kind,
        ts_ms=1_800_000_010_000 + (1 if update_kind == "APOLOGY" else 0),
    )


def test_t_nle_16_event_replays_deterministically_to_appraisal_affect_drive_and_expression() -> None:
    episode, aftereffect = _episode_and_boundary_aftereffect()

    episode_record = episode.to_appraisal_record()
    replayed_episode = replay_appraisal(episode)
    aftereffect_record = aftereffect.to_appraisal_record()

    assert episode_record.as_dict() == replayed_episode.as_dict()
    assert episode_record.schema_version == APPRAISAL_SCHEMA_VERSION
    assert episode_record.source_event_ref in episode_record.evidence_refs
    assert episode_record.appraisal["felt_reason"] == episode.felt_reason
    assert episode_record.affect_drive["drives"]["dominant_motive"] == episode.motive
    assert episode_record.episode_expression["chosen_action"] == episode.chosen_action
    assert episode_record.relationship_permission_effect == "NONE"

    replay = replay_appraisal_chain(aftereffect_record)
    stages = [node["stage"] for node in replay["stages"]]
    assert stages == ["event", "appraisal", "affect_drive", "episode_expression", "bond_signal"]
    assert replay["source_event_ref"] in replay["evidence_refs"]
    assert f"emotional_proactive_episode://{episode.episode_id}" in replay["evidence_refs"]
    assert replay["terminal_ref"].endswith("#bond-signal")
    assert replay["write_effect"] == "NONE"


def test_t_nle_16_boundary_semantics_survive_appraisal_and_read_only_bond_projection() -> None:
    episode, aftereffect = _episode_and_boundary_aftereffect()
    record = aftereffect.to_appraisal_record()
    engine = BondStageEngine()
    social = SocialBrainAgent(state_registry=TribeStateRegistry())

    before = engine.list_users()
    projection = build_bond_projection(
        user_id="boundary-user",
        stage_engine=engine,
        social_brain=social,
        appraisal_records=[record],
        now_ms=1_800_000_002_000,
    )
    after = engine.list_users()

    assert aftereffect.reply_mode == "boundary"
    assert record.bond_signal["direction"] == "respect_boundary"
    assert record.bond_signal["permission_effect"] == "NONE"
    assert record.affect_drive["drives"]["next_impulse_modulation"]["next_action_preference"] == "draft_only"
    assert before == after == []
    assert projection["schema_version"] == BOND_PROJECTION_SCHEMA_VERSION
    assert projection["read_only"] is True
    assert projection["write_api_available"] is False
    assert projection["state_available"] is False
    boundary = projection["relationship"]["boundary"]
    assert boundary["status"] == "RESPECT_REQUIRED"
    assert boundary["expression_constraint"] == "WITHDRAW_PROACTIVE_EXPRESSION"
    assert boundary["permission_effect"] == "NONE"
    assert boundary["requires_governed_command_for_bond_change"] is True
    assert projection["relationship"]["relationship_permission"]["changed"] is False


def test_t_nle_16_governed_bond_command_preserves_rift_repair_and_projection_only_score_stage() -> None:
    engine = BondStageEngine()
    client = InMemoryNeo4jBondClient()
    writer = BondDynamicsWriter(stage_engine=engine, neo4j_client=client)

    conflict = _write(
        writer,
        request_id="conflict",
        update_kind="CONFLICT",
        evidence_refs=["conversation://turn/conflict"],
        payload={"cause": "user boundary crossed"},
    )
    apology = _write(
        writer,
        request_id="apology",
        update_kind="APOLOGY",
        evidence_refs=["conversation://turn/apology"],
        payload={"repair": "acknowledge boundary"},
    )

    assert conflict.success is True
    assert apology.success is True
    assert conflict.event["stage_transition"]["evidence_refs"] == ["conversation://turn/conflict"]
    state = engine.get_state("user-t-nle-16")
    assert state.rift is not None
    assert state.rift.is_active() is True
    assert state.rift.rift_cause == "user boundary crossed"
    assert state.rift.repair_progress == pytest.approx(0.3)

    social = SocialBrainAgent(state_registry=TribeStateRegistry())
    projection = social.resolve_bond_projection(
        person_id="user-t-nle-16",
        stage_engine=engine,
        now_ms=1_800_000_020_000,
    )
    compatibility = projection["compatibility"]
    assert compatibility["authority"] == "projection_only"
    assert compatibility["authoritative"] is False
    assert compatibility["permission_effect"] == "NONE"
    assert compatibility["total_score"] == state.total_score
    assert compatibility["current_stage"] == state.current_stage
    assert projection["relationship"]["rift"]["active"] is True
    assert projection["relationship"]["rift"]["repair_progress"] == pytest.approx(0.3)
    assert projection["relationship"]["repair"]["rift_repair_progress"] == pytest.approx(0.3)
    assert {"trust", "affinity", "safety", "dependency", "rupture_load", "repair_progress"}.issubset(
        projection["relationship"]["vector"]
    )


def test_t_nle_16_bond_runtime_and_static_boundaries_reject_ungoverned_writes() -> None:
    engine = BondStageEngine()
    client = InMemoryNeo4jBondClient()
    writer = BondDynamicsWriter(stage_engine=engine, neo4j_client=client)

    rejected = _write(
        writer,
        request_id="missing-evidence",
        update_kind="INTERACTION",
        evidence_refs=[],
    )
    assert rejected.success is False
    assert rejected.reason_code == BOND_EVIDENCE_REQUIRED
    assert engine.list_users() == []

    with pytest.raises(ValueError, match=BOND_EVIDENCE_REQUIRED):
        engine.apply_event(
            user_id="direct-empty",
            update_kind="INTERACTION",
            evidence_refs=[],
        )
    with pytest.raises(PermissionError, match=BOND_SINGLE_WRITER_VIOLATION):
        client.write_bond_event(
            writer_id="host_brain.external.module",
            event_id="rogue-event",
            user_id="rogue-user",
            session_id="rogue-session",
            topic_id="rogue-topic",
            capability_hint="rogue",
            evidence_refs=["evidence://rogue"],
            payload={},
            ts_ms=1_800_000_030_000,
        )
    assert BOND_WRITER_ID == "host_brain.bond.bond_dynamics"
    assert scan_repo(ROOT) == []
def test_t_nle_16_projection_and_appraisal_apis_expose_no_mutation_methods() -> None:
    forbidden = {"write", "update", "apply", "promote", "commit", "save", "set_state"}
    projection_methods = {name for name in dir(BondProjectionReadModel) if not name.startswith("_")}
    appraisal_functions = {
        build_appraisal_from_emotional_aftereffect.__name__,
        build_appraisal_from_emotional_episode.__name__,
        replay_appraisal.__name__,
        replay_appraisal_chain.__name__,
    }

    assert projection_methods == {"snapshot"}
    assert forbidden.isdisjoint(projection_methods)
    assert forbidden.isdisjoint(appraisal_functions)

    engine = BondStageEngine()
    disabled = BondProjectionReadModel(stage_engine=engine, enabled=False).snapshot(
        user_id="disabled-user",
        now_ms=1_800_000_040_000,
    )
    assert disabled["status"] == "DISABLED"
    assert disabled["reason_codes"] == [BOND_PROJECTION_DISABLED]
    assert disabled["read_only"] is True
    assert disabled["write_api_available"] is False
    assert engine.list_users() == []


def test_t_nle_16_peek_state_is_detached_from_live_score_stage() -> None:
    engine = BondStageEngine()
    writer = BondDynamicsWriter(stage_engine=engine, neo4j_client=InMemoryNeo4jBondClient())
    applied = _write(
        writer,
        request_id="detached",
        update_kind="INTERACTION",
        evidence_refs=["conversation://turn/detached"],
    )
    assert applied.success is True

    detached = engine.peek_state("user-t-nle-16")
    assert detached is not None
    detached.total_score = 999
    detached.current_stage = "SOULMATE"

    live = engine.get_state("user-t-nle-16")
    assert live.total_score != 999
    assert live.current_stage != "SOULMATE"
